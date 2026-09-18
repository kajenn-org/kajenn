# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""An ordinary mount whose application executes over a private frame connection.

An instance given only an ``address`` connects and manages no peer process.
An instance also given a ``factory`` starts an owned subprocess with a fresh
interpreter and holds its lifecycle; the same factory can instead be served
independently over UDS or loopback TCP, and then the mount only connects.

The scope types it serves are buffered HTTP and ``WSK``; any other raises
``ValueError``, so a raw WebSocket, an SSE answer and a streaming reply do not
cross this mount.
"""

import asyncio
import os
import secrets
import sys
from typing import Any

from .application import BaseApplication
from .asgi_endpoint import BufferedAsgiEndpoint
from .channel.frame import Frame
from .http_record import HttpRecord
from .transport_limits import FrameTooLarge, HttpBodyTooLarge, http_max_body_size
from .remote_connection import RemoteCallFailed, RemoteConnection, RemotePeerMismatch
from .response import Response


class RemoteApplication(BaseApplication):
    """Forward one mounted application to an operator-configured endpoint."""

    #: Read by ``WsxConnection``: the answers this mount hands back were
    #: already adapted at the endpoint that produced them, so they travel on
    #: without a second conversion.
    forwards_payloads = True

    def __init__(self, *, address: str, factory: str | None = None,
                 request_timeout: float = 30.0, startup_timeout: float = 10.0,
                 shutdown_timeout: float = 5.0, max_calls: int = 16, **kwargs) -> None:
        """Build the mount and its connection to the endpoint.

        Args:
            address: ``uds:<path>`` or ``tcp:<loopback-ip>:<port>``.
            factory: ``module:callable`` to launch as an owned subprocess;
                ``None`` connects to an endpoint somebody else runs.
            request_timeout: seconds one forwarded call may take.
            startup_timeout: seconds to wait for the endpoint's readiness.
            shutdown_timeout: seconds to wait for the owned process to stop.
            max_calls: how many calls may be in flight at once.

        Raises:
            ValueError: ``startup_timeout`` or ``shutdown_timeout`` is not
                positive, or ``address`` is not one of the two forms.
        """
        super().__init__(**kwargs)
        if startup_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("lifecycle timeouts must be positive")
        self.address = address
        self.factory = factory
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.request_timeout = request_timeout
        self.max_calls = max_calls
        self._instance_id = secrets.token_hex(32) if factory is not None else None
        self.connection = self._new_connection()
        self._connection_closed = False
        self._process: asyncio.subprocess.Process | None = None
        self._started = False
        self._slots = asyncio.Semaphore(max_calls)

    async def on_startup(self) -> None:
        """Launch the owned process when there is one, and wait for readiness.

        A launch gets a fresh instance id, so the connection admits only the
        runner this mount started. Readiness is polled with ``/_ready`` until
        ``startup_timeout`` runs out. Returns at once when already started.

        Raises:
            RuntimeError: the owned process exited before answering, or the
                endpoint refused readiness.
            TimeoutError: readiness did not arrive within ``startup_timeout``.
            RemoteCallFailed: the endpoint is not the runner this mount owns,
                or the call failed after being sent.

        Any failure runs ``on_shutdown`` before it propagates.
        """
        if self._started:
            return
        if self._connection_closed:
            self.connection = self._new_connection()
            self._connection_closed = False
        if self.factory is not None:
            # A new launch gets a new identity, including after shutdown/restart.
            await self.connection.close()
            self._instance_id = secrets.token_hex(32)
            self.connection = self._new_connection()
            self._connection_closed = False
            self._process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "kajenn.remote_runner", "--address", self.address,
                "--factory", self.factory, "--mount", self.mount or "",
                "--shutdown-timeout", str(self.shutdown_timeout),
                "--request-timeout", str(self.request_timeout),
                "--max-calls", str(self.max_calls),
                env={**os.environ, "KAJENN_REMOTE_INSTANCE_ID": self._instance_id},
            )
        try:
            async with asyncio.timeout(self.startup_timeout):
                while True:
                    if self._process is not None and self._process.returncode is not None:
                        raise RuntimeError("remote application process exited before readiness")
                    try:
                        reply = await self.connection.call(Frame(method="CALL", path="/_ready"))
                        if reply.info.get("ready") is not True:
                            raise RuntimeError("remote endpoint refused readiness")
                        self._started = True
                        return
                    except RemoteCallFailed as exc:
                        if isinstance(exc, RemotePeerMismatch) or exc.outcome != "not_sent":
                            raise
                        await asyncio.sleep(0.05)
        except BaseException:
            await self.on_shutdown()
            raise

    async def on_shutdown(self) -> None:
        """Close the connection and stop the owned process, if there is one.

        The process is asked to terminate and given ``shutdown_timeout`` plus
        a second; it is killed when that runs out. A mount that owns no
        process only closes its connection. Calling it twice is no error.
        """
        self._started = False
        await self.connection.close()
        self._connection_closed = True
        process = self._process
        if process is not None:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), self.shutdown_timeout + 1)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            else:
                await process.wait()
            self._process = None

    async def __call__(self, scope, receive, send) -> None:
        """Forward one buffered request to the endpoint and write its answer.

        The body is read whole under an admission slot, encoded as an
        ``HttpRecord`` and sent as one ``CALL``. The identity in
        ``scope["auth"]`` travels as its identity and tags.

        The answer the endpoint returns is written as it is. A body over the
        policy ceiling answers 413, an endpoint that failed answers 502, and
        an endpoint that is unreachable or too slow answers 503. A client that
        disconnects while the body is being read ends the call silently.

        Raises:
            ValueError: the scope is not ``http``, or the identity on it does
                not carry a string ``identity`` and a list of string tags.
        """
        if scope["type"] != "http":
            raise ValueError("remote applications support buffered HTTP/WSK only")
        local_response = True
        try:
            # Take admission before collecting body bytes, bounding retained data.
            async with self._slots:
                async with asyncio.timeout(self.connection.timeout):
                    body = bytearray()
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > http_max_body_size():
                            await self._send_local_response(
                                Response(content="Request too large", status_code=413),
                                scope,
                                receive,
                                send,
                            )
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
                    info: dict[str, Any] = {"format": "http", "mount": self.mount or ""}
                    avatar = scope.get("auth")
                    if avatar is not None:
                        if (
                            not isinstance(avatar.identity, str)
                            or not isinstance(avatar.tags, list)
                            or not all(isinstance(tag, str) for tag in avatar.tags)
                        ):
                            raise ValueError("invalid trusted authentication context")
                        info["auth"] = {"identity": avatar.identity, "tags": list(avatar.tags)}
                    reply = await self.connection.call(Frame(
                        method="CALL", path="/http", info=info,
                        payload=HttpRecord().encode_request(scope, bytes(body))))
                    if "error" in reply.info:
                        response = Response(content="Remote application failed", status_code=502)
                    else:
                        result = HttpRecord().decode_response(reply.payload)
                        local_response = False
                        response = Response(content=result["body"], status_code=result["status"],
                                            headers=result["headers"])
        except (FrameTooLarge, HttpBodyTooLarge):
            response = Response(content="Request too large", status_code=413)
        except (RemoteCallFailed, TimeoutError):
            response = Response(content="Remote application unavailable", status_code=503)
        if local_response:
            await self._send_local_response(response, scope, receive, send)
            return
        await response(scope, receive, send)

    def _new_connection(self) -> RemoteConnection:
        """A connection to this mount's address, bound to the current instance id."""
        return RemoteConnection(
            self.address, timeout=self.request_timeout, max_calls=self.max_calls,
            expected_instance_id=self._instance_id,
        )

    async def _send_local_response(self, response, scope, receive, send) -> None:
        """Write an answer this mount built itself, adapting it for ``WSK``.

        A ``WSK`` scope gets the answer through ``BufferedAsgiEndpoint``, so it
        reaches the browser in the same json shape a forwarded answer has.
        """
        if scope.get("method") == "WSK":
            result = await BufferedAsgiEndpoint(response).serve(scope, b"")
            response = Response(content=result["body"], status_code=result["status"],
                                headers=result["headers"])
        await response(scope, receive, send)
