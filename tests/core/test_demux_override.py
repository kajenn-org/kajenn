# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""``demux`` is an extension point of the server.

Contract tests on a real ``AsgiServer``. HTTP and websocket dispatch go
through the public ``demux``: a subclass that overrides it changes where a
request and a websocket go, and the HTTP metrics count the application it
chose.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kajenn import AsgiServer, BaseApplication
from kajenn.types import Message, Receive, Scope, Send
from tests.conftest import call, read_samples, start_server, value
from tests.storage_support import auth_application


class Named(BaseApplication):
    """Answers its own code over HTTP and on a socket of its own."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": self.code.encode()})

    async def serve_websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": self.code})
        while (await receive())["type"] != "websocket.disconnect":
            pass


class Rerouting(AsgiServer):
    """Sends every path under ``/alpha`` to the application mounted at ``beta``."""

    def demux(self, scope: Scope) -> tuple[Any, Scope]:
        if scope["path"].startswith("/alpha/"):
            scope = {**scope, "path": "/beta/" + scope["path"].removeprefix("/alpha/")}
        return super().demux(scope)


async def rerouting_server() -> AsgiServer:
    return await start_server(Rerouting(
        applications=[auth_application(), (Named, {"code": "alpha"}), (Named, {"code": "beta"})]))


async def test_an_overridden_demux_changes_where_a_request_goes() -> None:
    server = await rerouting_server()
    answer = await call(server, "GET", "/alpha/anything")
    assert (answer["status"], answer["body"]) == (200, b"beta")
    samples = await read_samples(server)
    assert value(samples, "kajenn_http_requests_total", application="beta", route="",
                 method="GET", status="200") == 1


async def test_an_overridden_demux_changes_where_a_websocket_goes() -> None:
    server = await rerouting_server()
    incoming: asyncio.Queue = asyncio.Queue()
    sent: list[Message] = []
    await incoming.put({"type": "websocket.connect"})
    await incoming.put({"type": "websocket.disconnect", "code": 1000})

    async def send(message: Message) -> None:
        sent.append(message)

    await server({"type": "websocket", "path": "/alpha/socket", "headers": [],
                  "query_string": b""}, incoming.get, send)
    assert [m["text"] for m in sent if m["type"] == "websocket.send"] == ["beta"]
