# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The mount of an application that runs in another process.

``RemoteApplication`` is built by the server for every application declared
with ``spawner=``: it forwards each buffered http or WSK request as a CALL
carrying an ``HttpRecord`` to the hub member named after the application code,
and answers 503 while that member is not registered.
"""

import asyncio
import json
from typing import Any

from .application import BaseApplication
from .asgi_endpoint import BufferedAsgiEndpoint
from .http_record import HttpRecord
from .kbus import CALL_METHOD, Frame, KBusCallFailed
from .response import Response
from .transport_limits import FrameTooLarge, HttpBodyTooLarge, http_max_body_size


class RemoteApplication(BaseApplication):
    """Forward one mounted application to the hub member named after its code."""

    #: Read by ``WsxConnection``: the answers this mount hands back were
    #: already adapted at the endpoint that produced them, so they travel on
    #: without a second conversion.
    forwards_payloads = True

    def __init__(self, *, spawner: str, request_timeout: float = 30.0,
                 max_calls: int = 16, **kwargs: Any) -> None:
        """Build the mount of an external application.

        Args:
            spawner: the backend that starts the process (``"subprocess"``).
            request_timeout: seconds one forwarded call may take.
            max_calls: how many calls may be in flight at once.
        """
        super().__init__(**kwargs)
        self.spawner = spawner
        self.request_timeout = request_timeout
        self.max_calls = max_calls
        self._slots = asyncio.Semaphore(max_calls)

    async def __call__(self, scope, receive, send) -> None:
        """Forward one buffered request to the member and write its answer.

        A body over the policy ceiling answers 413, a member that failed
        answers 502, a member that is not registered or too slow answers 503.
        A client that disconnects while the body is being read ends the call
        silently.

        Raises:
            ValueError: the scope is not ``http``.
        """
        if scope["type"] != "http":
            raise ValueError("remote applications support buffered HTTP/WSK only")
        local_response = True
        try:
            async with self._slots:
                async with asyncio.timeout(self.request_timeout):
                    body = bytearray()
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > http_max_body_size():
                            await self._send_local_response(
                                413, "Request too large", scope, receive, send)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
                    hub = self.server.children_kbus
                    if hub.resolve(self.code) is None:
                        raise KBusCallFailed("member not registered", outcome="not_sent")
                    info: dict[str, Any] = {"format": "http"}
                    avatar = scope.get("auth")
                    if avatar is not None:
                        info["auth"] = {"identity": avatar.identity, "tags": list(avatar.tags)}
                    path = "/" + (self.mount or "") + scope["path"] if self.mount else scope["path"]
                    record = HttpRecord().encode_request(
                        {**scope, "path": path, "raw_path": path.encode(), "root_path": ""},
                        bytes(body))
                    reply = await hub.call_frame(self.code, Frame(
                        method=CALL_METHOD, path=path, info=info, payload=record))
                    if "error" in reply.info:
                        status, text = 502, "Remote application failed"
                    else:
                        result = HttpRecord().decode_response(reply.payload)
                        local_response = False
                        response = Response(content=result["body"], status_code=result["status"],
                                            headers=result["headers"])
        except (FrameTooLarge, HttpBodyTooLarge):
            status, text = 413, "Request too large"
        except (KBusCallFailed, TimeoutError):
            status, text = 503, "Remote application unavailable"
        if local_response:
            await self._send_local_response(status, text, scope, receive, send)
            return
        await response(scope, receive, send)

    async def _send_local_response(self, status, text, scope, receive, send) -> None:
        """Write an answer this mount built itself, as a JSON string.

        A ``WSK`` scope gets the answer through ``BufferedAsgiEndpoint``, so it
        reaches the browser in the same json shape a forwarded answer has.
        """
        response = Response(content=json.dumps(text), status_code=status,
                            media_type="application/json")
        if scope.get("method") == "WSK":
            result = await BufferedAsgiEndpoint(response).serve(scope, b"")
            response = Response(content=result["body"], status_code=result["status"],
                                headers=result["headers"])
        await response(scope, receive, send)
