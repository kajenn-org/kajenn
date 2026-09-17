# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The server speaks first (#68 phase 3).

Contract tests. ``BaseServer.send_message(page_id, path, data)`` writes one
message onto the socket a page speaks on, without that page having asked for
anything. The message has the shape of a request and carries NO ``id``: it is
not an answer, and nobody answers it — a client that wants to reply sends an
rpc of its own (W-12).

**Delivered means written.** ``True`` says the message reached the socket, not
that the page ran it. A page nobody bound, one whose socket already closed:
``False``.

Who binds a page to its socket is `openchannel`, which arrives in phase 4;
here the test binds it, because no scope key carries the live socket to an
application (the core writes no live objects into a scope).
"""

from __future__ import annotations

import asyncio
from typing import Any

from kajenn import BaseApplication, MiddlewareMixin, BaseServer
from kajenn.types import Message, Scope, Receive, Send
from kajenn.websocket import WebSocket
from kajenn.wsx import WsxConnection, WsxEnvelope

PREFIX = "WSX://"


class WsServer(MiddlewareMixin, BaseServer):
    """The composition a websocket needs."""


class QuietApp(BaseApplication):
    """Serves nothing: this phase never sends a message up."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class XT_Browser:
    """A browser that stays connected until the test lets it go."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self._incoming: asyncio.Queue[Message] = asyncio.Queue()
        self._incoming.put_nowait({"type": "websocket.connect"})

    async def receive(self) -> Message:
        return await self._incoming.get()

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    def leave(self) -> None:
        """Disconnect, the way a browser closing its tab does."""
        self._incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    @property
    def messages(self) -> list[WsxEnvelope]:
        return [
            WsxEnvelope(m["text"])
            for m in self.sent
            if m["type"] == "websocket.send" and m.get("text", "").startswith(PREFIX)
        ]


class XT_LiveConnection:
    """One connection served in the background, as a real one is."""

    def __init__(self, server: BaseServer) -> None:
        self.server = server
        self.browser = XT_Browser()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> XT_LiveConnection:
        scope: Scope = {
            "type": "websocket",
            "path": "/app/_wsx",
            "headers": [(b"host", b"example.org")],
            "subprotocols": [],
        }
        connection = WsxConnection(self.server, scope, self.browser.receive, self.browser.send)
        self._task = asyncio.create_task(connection.serve())
        await self.settle()
        return self

    async def __aexit__(self, *failure: Any) -> None:
        self.browser.leave()
        if self._task is not None:
            await self._task

    async def settle(self) -> None:
        """Give the connection's task the turns it needs to move on."""
        for _ in range(5):
            await asyncio.sleep(0)

    @property
    def socket(self) -> WebSocket:
        """The facade of this live connection, as the server holds it."""
        return self.server.websockets.snapshot()[0]


def live_server() -> WsServer:
    return WsServer(applications=[QuietApp(mount="app")])


class TestTheServerWritesToAPage:
    async def test_a_bound_page_receives_the_message(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("p1", live.socket)
            assert await server.send_message("p1", "/main/refresh", {"n": 1}) is True
            assert [(m.path, m.data) for m in live.browser.messages] == [
                ("/main/refresh", {"n": 1})
            ]

    async def test_the_message_carries_no_id_and_names_its_page(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("p1", live.socket)
            await server.send_message("p1", "/main/refresh")
            message = live.browser.messages[0]
            assert (message.id, message.page_id, message.status) == (None, "p1", None)

    async def test_it_has_the_shape_of_a_request(self) -> None:
        # The client routes it on the path, the way the server routes what the
        # client sends: one codec, both ways.
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("p1", live.socket)
            await server.send_message("p1", "/main/refresh")
            assert live.browser.messages[0].method == "WSK"

    async def test_a_message_with_no_data_carries_none(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("p1", live.socket)
            await server.send_message("p1", "/main/ring")
            assert live.browser.messages[0].data is None

    async def test_two_pages_on_one_socket_are_told_apart(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("root", live.socket)
            server.websockets.bind_page("frame", live.socket)
            await server.send_message("root", "/main/one")
            await server.send_message("frame", "/main/two")
            assert [(m.page_id, m.path) for m in live.browser.messages] == [
                ("root", "/main/one"),
                ("frame", "/main/two"),
            ]


class TestWhenThereIsNobodyToWriteTo:
    async def test_a_page_that_speaks_on_no_socket_is_told_so(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server):
            assert await server.send_message("p1", "/main/refresh") is False

    async def test_a_page_whose_browser_left_is_told_so(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("p1", live.socket)
            assert await server.send_message("p1", "/main/one") is True
        # Out of the block the browser is gone: the socket was unregistered,
        # and with it the page it carried.
        assert await server.send_message("p1", "/main/two") is False

    async def test_nothing_is_written_after_the_browser_left(self) -> None:
        server = live_server()
        async with XT_LiveConnection(server) as live:
            server.websockets.bind_page("p1", live.socket)
            browser = live.browser
        written = len(browser.sent)
        await server.send_message("p1", "/main/late")
        assert len(browser.sent) == written
