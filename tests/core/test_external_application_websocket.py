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

"""A raw websocket reaches an external application that takes the socket itself.

An application defining ``serve_websocket`` owns its sockets (server.py,
``on_websocket``). Spawned, it owns them in its own process: every message of
the client reaches it, every message it sends reaches the client, and either
side may close. The client is the ASGI interface: ``receive`` feeds the client's
events, ``send`` records what the client would receive.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from kajenn import AsgiServer

from .test_external_application import RECIPE, answering, request, running
from .test_external_application_streaming import wait_for_file

SOCKETS = '''

class Sockets(Billing):
    async def serve_websocket(self, scope, receive, send):
        marker = scope["query_string"].decode().partition("=")[2]
        message = await receive()
        assert message["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        closed = "none"
        try:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    closed = str(message.get("code"))
                    return
                text = message.get("text")
                if text == "close":
                    await send({"type": "websocket.close", "code": 4000})
                    closed = "by-app"
                    return
                if text == "pid":
                    await send({"type": "websocket.send", "text": str(os.getpid())})
                elif text is not None:
                    await send({"type": "websocket.send", "text": text})
                else:
                    await send({"type": "websocket.send", "bytes": message["bytes"]})
        finally:
            if marker:
                with open(marker, "w") as handle:
                    handle.write(closed)
'''


def write_socket_recipe(tmp_path, *, declaration: str = 'spawner="subprocess"') -> str:
    text = (RECIPE.replace("\nclass Local(", SOCKETS + "\nclass Local(")
            .replace("SERVER_SECTION", "")
            .replace("app_class=Billing, DECLARATION", f"app_class=Sockets, {declaration}")
            .replace("STARTUP_DELAY", "0.2"))
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


class Socket:
    """One websocket driven through the server's ASGI interface."""

    def __init__(self, server: AsgiServer, path: str, query: bytes = b"") -> None:
        self.scope = {
            "type": "websocket", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "scheme": "ws", "path": path, "raw_path": path.encode(), "root_path": "",
            "query_string": query, "headers": [(b"host", b"example.org")],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80), "subprotocols": [],
        }
        self.server = server
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None

    async def send(self, message: dict[str, Any]) -> None:
        await self.sent.put(message)

    async def open(self) -> dict[str, Any]:
        await self.incoming.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(self.server(self.scope, self.incoming.get, self.send))
        return await self.next()

    async def say(self, text: str | None = None, data: bytes | None = None) -> None:
        message: dict[str, Any] = {"type": "websocket.receive"}
        if text is not None:
            message["text"] = text
        else:
            message["bytes"] = data
        await self.incoming.put(message)

    async def next(self, seconds: float = 10) -> dict[str, Any]:
        return await asyncio.wait_for(self.sent.get(), seconds)


async def test_messages_travel_both_ways(tmp_path):
    # wf:contract: with a spawned application defining serve_websocket, a websocket on its
    # wf:contract: mount is accepted, a text message comes back as the same text and a
    # wf:contract: binary message as the same bytes; the handler runs in the spawned
    # wf:contract: process.
    async with running(write_socket_recipe(tmp_path)) as server:
        socket = Socket(server, "/billing/socket")
        assert (await socket.open())["type"] == "websocket.accept"
        await socket.say("hello")
        assert await socket.next() == {"type": "websocket.send", "text": "hello"}
        await socket.say(data=b"\x00\x01\xff")
        assert await socket.next() == {"type": "websocket.send", "bytes": b"\x00\x01\xff"}
        await socket.say("pid")
        pid = int((await socket.next())["text"])
        await socket.incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(socket.task, 10)
    assert pid != os.getpid()


async def test_the_client_closing_reaches_the_application(tmp_path):
    # wf:contract: a client disconnect with code 1000 reaches the spawned serve_websocket
    # wf:contract: as websocket.disconnect carrying that code, and the server's websocket
    # wf:contract: call returns.
    marker = tmp_path / "marker"
    async with running(write_socket_recipe(tmp_path)) as server:
        socket = Socket(server, "/billing/socket", f"marker={marker}".encode())
        await socket.open()
        await socket.incoming.put({"type": "websocket.disconnect", "code": 1000})
        assert await wait_for_file(marker) == "1000"
        await asyncio.wait_for(socket.task, 10)


async def test_the_application_closing_reaches_the_client(tmp_path):
    # wf:contract: a websocket.close with code 4000 sent by the spawned application reaches
    # wf:contract: the client as websocket.close with code 4000.
    async with running(write_socket_recipe(tmp_path)) as server:
        socket = Socket(server, "/billing/socket")
        await socket.open()
        await socket.say("close")
        closing = await socket.next()
        await socket.incoming.put({"type": "websocket.disconnect", "code": 4000})
        await asyncio.wait_for(socket.task, 10)
    assert (closing["type"], closing["code"]) == ("websocket.close", 4000)


async def test_a_process_dying_mid_session_closes_the_socket(tmp_path):
    # wf:contract: when the spawned process is killed while a socket is open, the client
    # wf:contract: receives websocket.close with code 1011, and the relaunched process
    # wf:contract: answers the next HTTP request.
    async with running(write_socket_recipe(tmp_path)) as server:
        socket = Socket(server, "/billing/socket")
        await socket.open()
        await socket.say("pid")
        pid = int((await socket.next())["text"])
        os.kill(pid, signal.SIGKILL)
        closing = await socket.next()
        await socket.incoming.put({"type": "websocket.disconnect", "code": 1011})
        await asyncio.wait_for(socket.task, 10)
        assert (closing["type"], closing["code"]) == ("websocket.close", 1011)
        await answering(server, other_than=pid)
        assert await request(server, "/billing/total", query=b"order=3") == (200, {"total": 6})


async def test_the_socket_behaves_the_same_spawned_and_inprocess(tmp_path):
    # wf:contract: the same exchange (accept, echo of a text, close by the application with
    # wf:contract: 4000) gives the client the same messages spawned and in-process.
    async def exchange(config: str) -> list[dict[str, Any]]:
        async with running(config) as server:
            socket = Socket(server, "/billing/socket")
            seen = [await socket.open()]
            await socket.say("hello")
            seen.append(await socket.next())
            await socket.say("close")
            seen.append(await socket.next())
            await socket.incoming.put({"type": "websocket.disconnect", "code": 4000})
            await asyncio.wait_for(socket.task, 10)
        return seen

    spawned = await exchange(write_socket_recipe(tmp_path))
    (tmp_path / "inproc").mkdir()
    inprocess = await exchange(write_socket_recipe(tmp_path / "inproc",
                                                   declaration='mount="billing"'))
    assert spawned == inprocess
