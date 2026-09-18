# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Serve an application factory in a fresh process over UDS or loopback TCP.

``RemoteApplicationRunner`` imports a ``module:callable`` factory, calls it,
runs that application's startup and only then starts listening — readiness
answers ``/_ready`` after startup, never before. SIGTERM or SIGINT closes the
listener, so no further call is admitted, drains the calls in flight within
``shutdown_timeout`` and cancels what is left, then runs the shutdown of this
application alone.

The listener never unlinks a pathname it did not create, and at the end it
removes only the one still naming its own socket. It holds no authority over a
process it did not start. ``RemoteRunnerCommand`` is the ``python -m`` entry
point that wires the command line to the runner.
"""

import argparse
import asyncio
import contextlib
import importlib
import inspect
import os
import signal
from typing import Any

from .application import BaseApplication
from .asgi_endpoint import BufferedAsgiEndpoint
from .channel.frame import Frame, FrameStream
from .http_record import HttpRecord
from .exceptions import HTTPException
from .response import Response
from .remote_connection import RemoteAddress
from .server import BaseServer
from .session.avatar import Avatar


class RemoteApplicationRunner:
    """One listening service, its application lifecycle, and bounded calls."""

    def __init__(self, factory: str, address: str, *, mount: str = "demo",
                 shutdown_timeout: float = 5.0, request_timeout: float = 30.0,
                 max_calls: int = 16, allow_network_listener: bool = False,
                 instance_id: str | None = None) -> None:
        """Import the factory, build the application and prepare the listener.

        A factory answering a ``BaseApplication`` is given the mount and a
        ``BaseServer`` of its own; any other ASGI callable is driven through
        the raw lifespan protocol instead.

        Args:
            factory: ``module:callable`` answering the application to serve.
            address: ``uds:<path>`` or ``tcp:<loopback-ip>:<port>``.
            mount: the mount the caller forwards under; it must match the
                routing metadata of every call.
            shutdown_timeout: seconds to drain the calls in flight.
            request_timeout: seconds one call may take, read and serve alike.
            max_calls: how many calls and how many connections are admitted.
            allow_network_listener: bind a non-loopback IP.
            instance_id: the launch identity to present on ``/_ready``.

        Raises:
            ValueError: a timeout is not positive, ``max_calls`` is below 1,
                the factory is not a ``module:callable``, or the address is
                not one of the two forms.
        """
        if shutdown_timeout <= 0 or request_timeout <= 0 or max_calls < 1:
            raise ValueError("timeouts and max_calls must be positive")
        module, separator, name = factory.partition(":")
        if not separator:
            raise ValueError("factory must be an importable module:callable")
        mount = mount or ""
        self.application = getattr(importlib.import_module(module), name)()
        if isinstance(self.application, BaseApplication):
            self.application.mount = mount
            self.server = BaseServer(applications=[self.application])
        self.address = RemoteAddress(address, allow_network_listener=allow_network_listener)
        self.mount = mount
        self.instance_id = instance_id
        self.shutdown_timeout = shutdown_timeout
        self.request_timeout = request_timeout
        self.endpoint = BufferedAsgiEndpoint(self.application)
        self.max_connections = max_calls
        self._slots = asyncio.Semaphore(max_calls)
        self._calls: set[asyncio.Task[None]] = set()
        self._connections: set[asyncio.Task[None]] = set()
        self._streams: set[FrameStream] = set()
        self._stop = asyncio.Event()
        self._lifespan_task: asyncio.Task[None] | None = None
        self._lifespan_input: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._lifespan_output: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def run(self) -> None:
        """Live this service: startup, listen, wait for the stop, drain, shut down.

        Returns when SIGTERM or SIGINT has been handled and the application's
        shutdown has run. Whatever happened, the ``finally`` closes the
        listener, unlinks the socket it owns, drains the calls in flight
        within ``shutdown_timeout``, cancels the connections and runs the
        shutdown.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop.set)
        listener = None
        try:
            await self._lifecycle("startup")
            listener = await self.address.listen(self._accept)
            await self._stop.wait()
        finally:
            if listener is not None:
                listener.close()
                await listener.wait_closed()
                self.address.unlink_owned_socket()
            if self._calls:
                _, pending = await asyncio.wait(self._calls, timeout=self.shutdown_timeout)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            for stream in list(self._streams):
                await stream.close()
            for task in list(self._connections):
                task.cancel()
            await asyncio.gather(*self._connections, return_exceptions=True)
            await self._lifecycle("shutdown")

    async def _lifecycle(self, phase: str) -> None:
        """Run ``startup`` or ``shutdown`` on the application.

        A ``BaseApplication`` gets its ``on_<phase>`` hook. Any other ASGI
        callable is driven through the lifespan protocol: startup creates the
        lifespan task, shutdown awaits it.

        Raises:
            RuntimeError: the application did not complete the phase, or a
                shutdown was asked for without a startup.
            TimeoutError: the phase outlasted its bound — ``shutdown_timeout``
                for a shutdown, ten seconds for a startup.
        """
        async with asyncio.timeout(self.shutdown_timeout if phase == "shutdown" else 10):
            if isinstance(self.application, BaseApplication):
                result = getattr(self.application, "on_" + phase)()
                if inspect.isawaitable(result):
                    await result
                return
            if phase == "startup":
                self._lifespan_task = asyncio.create_task(self.application(
                    {"type": "lifespan", "asgi": {"version": "3.0"}},
                    self._lifespan_input.get, self._lifespan_output.put))
            await self._lifespan_input.put({"type": "lifespan." + phase})
            message = await self._lifespan_output.get()
            if message.get("type") != "lifespan." + phase + ".complete":
                raise RuntimeError("application lifespan refused " + phase)
            if phase == "shutdown":
                if self._lifespan_task is None:
                    raise RuntimeError("application lifespan was not started")
                await self._lifespan_task

    async def _accept(self, reader, writer) -> None:
        """Read frames off one accepted connection, each served on its own task.

        A connection arriving past ``max_calls`` connections, or after the
        stop, is closed without being read. Admission is taken before the read,
        so a peer that sends slowly holds a slot instead of buffering behind
        one. A read that fails, times out or ends closes the connection.

        Raises:
            RuntimeError: this callback runs outside a task.
        """
        if len(self._connections) >= self.max_connections or self._stop.is_set():
            writer.close()
            await writer.wait_closed()
            return
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("remote connection has no owning task")
        self._connections.add(task)
        stream = FrameStream(reader, writer)
        self._streams.add(stream)
        try:
            while not self._stop.is_set():
                # Global admission before reading a frame bounds buffered payloads
                # across connections, including peers that send too slowly.
                await self._slots.acquire()
                try:
                    async with asyncio.timeout(self.request_timeout):
                        frame = await stream.read()
                    if frame is None:
                        self._slots.release()
                        break
                    service = asyncio.create_task(self._serve(stream, frame))
                    self._calls.add(service)
                    service.add_done_callback(self._calls.discard)
                except BaseException:
                    self._slots.release()
                    raise
        except (OSError, ValueError, TimeoutError):
            pass
        finally:
            self._connections.discard(task)
            self._streams.discard(stream)
            await stream.close()

    async def _serve(self, stream: FrameStream, frame: Frame) -> None:
        """Serve one frame and write its reply, releasing the admission slot.

        ``/_ready`` answers readiness and the launch identity. ``/http``
        decodes the request record, rebuilds the identity the caller vouched
        for and serves it through the buffered endpoint; an ``HTTPException``
        becomes that status as a plain-text answer. Anything else — another
        method, another path, routing metadata that does not match this mount,
        an identity of the wrong shape — replies with the error's type name
        under ``error`` instead of failing the connection.
        """
        try:
            if frame.method != "CALL":
                raise ValueError("remote service expects CALL")
            if frame.path == "/_ready":
                info: dict[str, Any] = {"ready": True}
                if self.instance_id is not None:
                    info["instance_id"] = self.instance_id
                reply = Frame(id=frame.id, method="REPLY", path=frame.path, info=info)
            else:
                if frame.path != "/http" or frame.info.get("format") != "http":
                    raise ValueError("unsupported remote operation")
                info = frame.info
                if set(info) - {"format", "mount", "auth"} or info.get("mount") != self.mount:
                    raise ValueError("invalid remote HTTP routing metadata")
                scope, body = HttpRecord().decode_request(frame.payload)
                auth = info.get("auth")
                if auth is not None:
                    if (not isinstance(auth, dict) or set(auth) != {"identity", "tags"}
                            or not isinstance(auth["identity"], str)
                            or not isinstance(auth["tags"], list)
                            or not all(isinstance(tag, str) for tag in auth["tags"])):
                        raise ValueError("invalid trusted authentication context")
                    scope["auth"] = Avatar(auth["identity"], auth["tags"])
                async with asyncio.timeout(self.request_timeout):
                    try:
                        result = await self.endpoint.serve(scope, body)
                    except HTTPException as refused:
                        result = await BufferedAsgiEndpoint(Response(
                            content=refused.detail, status_code=refused.status,
                            media_type="text/plain")).serve(scope, body)
                reply = Frame(id=frame.id, method="REPLY", path=frame.path,
                              info={"format": "http"}, payload=HttpRecord().encode_response(result))
            await stream.write(reply)
        except Exception as exc:
            with contextlib.suppress(OSError):
                await stream.write(Frame(id=frame.id, method="REPLY", path=frame.path,
                                         info={"error": type(exc).__name__}))
        finally:
            self._slots.release()


class RemoteRunnerCommand:
    """Command-line wiring for the reusable application runner."""

    def run(self) -> None:
        """Parse the command line and run one ``RemoteApplicationRunner``.

        The launch identity is taken out of the environment, so it is not
        inherited by anything this process starts in turn.
        """
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--factory", required=True)
        parser.add_argument("--address", required=True)
        parser.add_argument("--mount", default="demo")
        parser.add_argument("--shutdown-timeout", type=float, default=5)
        parser.add_argument("--request-timeout", type=float, default=30)
        parser.add_argument("--max-calls", type=int, default=16)
        parser.add_argument(
            "--allow-network-listener", action="store_true",
            help="Allow a non-loopback listener, e.g. 0.0.0.0 inside a private container network",
        )
        options = parser.parse_args()
        asyncio.run(RemoteApplicationRunner(options.factory, options.address,
                    mount=options.mount, shutdown_timeout=options.shutdown_timeout,
                    request_timeout=options.request_timeout, max_calls=options.max_calls,
                    allow_network_listener=options.allow_network_listener,
                    instance_id=os.environ.pop("GNR_ASGI_REMOTE_INSTANCE_ID", None)).run())


if __name__ == "__main__":
    RemoteRunnerCommand().run()
