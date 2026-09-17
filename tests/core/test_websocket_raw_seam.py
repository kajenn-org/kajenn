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

"""The admitted mode: an application that wants the socket itself (#68 phase 5).

Contract tests. An application that defines ``serve_websocket`` is handed the
raw scope, receive and send: nothing of the WSX motor runs for it — no accept,
no Origin gate, no registry, no refusal on the server's state. An application
that takes the socket takes all of it.

It is the seam a hosted framework with a websocket protocol of its own reaches
the server by; the core builds nothing beyond the seam (decisions.md §11).
"""

from __future__ import annotations

from typing import Any

from kajenn import BaseApplication, BaseServer
from kajenn.lifespan import QUITTING
from kajenn.types import Message, Receive, Scope, Send


class EchoSocketApp(BaseApplication):
    """An application that speaks its own protocol on the socket."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: list[Scope] = []

    async def serve_websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.seen.append(scope)
        await receive()
        await send({"type": "websocket.accept", "subprotocol": "echo"})
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            await send({"type": "websocket.send", "text": f"echo:{message['text']}"})


class PlainApp(BaseApplication):
    """An application with no socket of its own: the motor serves it."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def drive(server: BaseServer, path: str, *texts: str) -> list[Message]:
    """Live one handshake at the ASGI level and hand back what was written."""
    incoming: list[Message] = [{"type": "websocket.connect"}]
    incoming += [{"type": "websocket.receive", "text": text} for text in texts]
    incoming.append({"type": "websocket.disconnect", "code": 1000})
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0) if incoming else {"type": "websocket.disconnect", "code": 1006}

    async def send(message: Message) -> None:
        sent.append(message)

    await server({"type": "websocket", "path": path, "headers": []}, receive, send)
    return sent


class TestAnApplicationThatTakesTheSocket:
    async def test_it_accepts_and_answers_in_its_own_protocol(self) -> None:
        server = BaseServer(applications=[EchoSocketApp(mount="raw")])
        sent = await drive(server, "/raw/live", "one", "two")
        assert sent == [
            {"type": "websocket.accept", "subprotocol": "echo"},
            {"type": "websocket.send", "text": "echo:one"},
            {"type": "websocket.send", "text": "echo:two"},
        ]

    async def test_the_path_arrives_without_the_mount(self) -> None:
        # The same demux an HTTP request goes through: the segment that named
        # the application is off, and the rest is the application's own.
        app = EchoSocketApp(mount="raw")
        await drive(BaseServer(applications=[app]), "/raw/live")
        assert app.seen[0]["path"] == "/live"

    async def test_nothing_of_the_motor_runs(self) -> None:
        # No accept of ours before its own, and the socket is in no registry:
        # the core does not half-serve a connection it does not hold.
        server = BaseServer(applications=[EchoSocketApp(mount="raw")])
        sent = await drive(server, "/raw/live")
        assert sent[0]["subprotocol"] == "echo"
        assert server.websockets.snapshot() == []

    async def test_a_server_that_is_not_running_never_reaches_it(self) -> None:
        # The state is judged above the demux, for every websocket alike
        # (owner, 2026-09-07): the machine refuses before the application is
        # even named, and the handshake is turned away with no accept — 1013
        # exists only after one, and here the accept would be the
        # application's.
        app = EchoSocketApp(mount="raw")
        server = BaseServer(applications=[app])
        server.state = QUITTING
        sent = await drive(server, "/raw/live", "one")
        assert sent == [{"type": "websocket.close", "code": 1013, "reason": "server restarting"}]
        assert app.seen == []


class TestAnApplicationThatDoesNot:
    async def test_the_motor_serves_it_as_ever(self) -> None:
        server = BaseServer(applications=[PlainApp(mount="")])
        sent = await drive(server, "/")
        assert sent == [{"type": "websocket.accept"}]

    async def test_a_server_that_is_not_running_refuses_it_the_same_way(self) -> None:
        # One refusal for both modes: the machine's state is judged before
        # anybody knows which application would have served the socket.
        server = BaseServer(applications=[PlainApp(mount="")])
        server.state = QUITTING
        sent = await drive(server, "/")
        assert sent == [{"type": "websocket.close", "code": 1013, "reason": "server restarting"}]

    async def test_the_base_application_defines_no_such_seam(self) -> None:
        # Absent by default, not None: what is not there cannot be called by
        # mistake, and a subclass declares the mode by defining it.
        assert not hasattr(BaseApplication(mount=""), "serve_websocket")
