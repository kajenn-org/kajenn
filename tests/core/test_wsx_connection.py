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

"""One websocket connection, from the handshake to the last message (#68 phase 2).

Contract tests. ``WsxConnection.serve()`` is the whole life of a socket: the
gate (the server's state, the Origin, the identity, the home application's
cookie), the accept, the read loop where every message becomes a request, and
the wait for what is still in flight.

The gate answers in ONE shape: accept first, then close with a code the browser
can read. The single exception is a hostile Origin, refused before the accept —
there is nothing to tell a caller that was never admitted.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kajenn import BaseApplication, BaseServer, MiddlewareMixin
from kajenn.exceptions import HTTPForbidden, HTTPUnauthorized
from kajenn.request import Request
from genro_tytx import to_msgpack, to_tytx

from kajenn.types import Message, Receive, Scope, Send
from kajenn.wsx import WsxConnection, WsxEnvelope

PREFIX = "WSX://"


class WsServer(MiddlewareMixin, BaseServer):
    """The composition a websocket needs: the chain, for the session layer."""


class EchoApp(BaseApplication):
    """Answers what it was asked, so a test can see the whole request."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] == "/boom":
            raise RuntimeError("boom")
        if scope["path"] == "/forbidden":
            raise HTTPForbidden("not yours")
        request = Request(scope, receive, server=self.server, application=self)
        await request.init()
        request.response.set_result(
            {
                "method": scope["method"],
                "path": scope["path"],
                "data": request.data,
                "identity": scope["auth"].identity if scope.get("auth") else None,
                "session": scope["session"].id if scope.get("session") else None,
                "host": request.headers.get("host"),
            }
        )
        await request.response(scope, receive, send)


class TellingApp(EchoApp):
    """Answers with what the synthetic scope carried, and with other shapes."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] == "/keys":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            keys = {
                "page_id": scope.get("genro.page_id"),
                "reply_path": scope.get("genro.reply_path"),
            }
            await send({"type": "http.response.body", "body": to_tytx(keys, "json").encode()})
            return
        if scope["path"] == "/plain":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"just text"})
            return
        if scope["path"] == "/stream":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            await send({"type": "http.response.body", "body": b"second"})
            return
        if scope["path"] == "/msgpack":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/vnd.tytx+msgpack")],
                }
            )
            await send({"type": "http.response.body", "body": to_msgpack({"n": 7})})
            return
        if scope["path"] == "/binary":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"image/png")],
                }
            )
            await send({"type": "http.response.body", "body": b"\x89PNG\xff"})
            return
        if scope["path"] == "/silent":
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await super().__call__(scope, receive, send)


class GatedApp(EchoApp):
    """An application that admits no handshake without its cookie."""

    @property
    def handshake_cookie(self) -> str | None:
        return "spa_connection_id"


class XT_Socket:
    """One scripted browser: texts in, ASGI messages out."""

    def __init__(self, *texts: str) -> None:
        self.incoming: list[Message] = [{"type": "websocket.connect"}]
        self.incoming += [{"type": "websocket.receive", "text": text} for text in texts]
        self.incoming.append({"type": "websocket.disconnect", "code": 1000})
        self.sent: list[Message] = []

    async def receive(self) -> Message:
        if not self.incoming:
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect", "code": 1006}
        message = self.incoming.pop(0)
        if message["type"] == "websocket.disconnect":
            # A real browser does not vanish the instant after it spoke: give
            # the messages in flight the turns they need to answer.
            for _ in range(10):
                await asyncio.sleep(0)
        return message

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    @property
    def accepted(self) -> bool:
        return any(m["type"] == "websocket.accept" for m in self.sent)

    @property
    def closed(self) -> tuple[int, str] | None:
        for message in self.sent:
            if message["type"] == "websocket.close":
                return message["code"], message.get("reason", "")
        return None

    @property
    def answers(self) -> list[WsxEnvelope]:
        return [
            WsxEnvelope(m["text"])
            for m in self.sent
            if m["type"] == "websocket.send" and m.get("text", "").startswith(PREFIX)
        ]


def request_message(path: str, **fields: Any) -> str:
    """One WSX request as the browser would write it."""
    return WsxEnvelope(method="WSK", path=path, **fields).encode()


async def drive(
    server: BaseServer,
    socket: XT_Socket,
    path: str = "/echo/main",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> XT_Socket:
    """Live one whole connection and hand back what the browser saw."""
    scope: Scope = {
        "type": "websocket",
        "path": path,
        "headers": headers if headers is not None else [(b"host", b"example.org")],
        "query_string": b"",
        "subprotocols": [],
    }
    await WsxConnection(server, scope, socket.receive, socket.send).serve()
    return socket


def echo_server(**kwargs: Any) -> WsServer:
    return WsServer(applications=[EchoApp(mount="echo")], **kwargs)


def closure(socket: XT_Socket) -> tuple[int, str]:
    """The close the browser saw; a socket that was never closed is a failure."""
    closed = socket.closed
    assert closed is not None, "the socket was never closed"
    return closed


class TestTheGate:
    async def test_a_handshake_on_a_path_no_application_serves_is_closed_1008(self) -> None:
        socket = await drive(echo_server(), XT_Socket(), path="/nowhere/x")
        assert socket.accepted
        assert closure(socket)[0] == 1008 and "no application" in closure(socket)[1]

    async def test_the_home_application_may_demand_a_cookie(self) -> None:
        server = WsServer(applications=[GatedApp(mount="spa")])
        socket = await drive(server, XT_Socket(), path="/spa/_wsx")
        assert socket.accepted
        assert closure(socket)[0] == 1008 and "cookie" in closure(socket)[1]

    async def test_the_handshake_passes_when_the_cookie_is_there(self) -> None:
        # Nothing closes it: the client left on its own, and there is nobody
        # left to tell.
        server = WsServer(applications=[GatedApp(mount="spa")])
        socket = await drive(
            server,
            XT_Socket(),
            path="/spa/_wsx",
            headers=[(b"cookie", b"spa_connection_id=c1")],
        )
        assert socket.accepted and socket.closed is None

    async def test_an_application_that_gates_nothing_lets_every_handshake_in(self) -> None:
        socket = await drive(echo_server(), XT_Socket())
        assert socket.accepted and socket.closed is None


class TestTheIdentity:
    async def test_an_invalid_credential_is_accepted_then_closed_1008(self) -> None:
        class RefusingServer(WsServer):
            def authenticate(self, request: Any) -> Any:
                raise HTTPUnauthorized("bad token")

        server = RefusingServer(applications=[EchoApp(mount="echo")])
        socket = await drive(server, XT_Socket())
        assert socket.accepted
        assert closure(socket)[0] == 1008 and "bad token" in closure(socket)[1]


class TestWhatTheSyntheticScopeCarries:
    def telling_server(self) -> WsServer:
        return WsServer(applications=[TellingApp(mount="echo")])

    async def test_the_page_and_the_reply_path_reach_the_application(self) -> None:
        message = request_message("/echo/keys", id="m1", page_id="p1", reply_path="/echo/done")
        socket = await drive(self.telling_server(), XT_Socket(message))
        assert socket.answers[0].data == {"page_id": "p1", "reply_path": "/echo/done"}

    async def test_a_message_without_them_leaves_them_out_of_the_scope(self) -> None:
        socket = await drive(
            self.telling_server(), XT_Socket(request_message("/echo/keys", id="m1"))
        )
        assert socket.answers[0].data == {"page_id": None, "reply_path": None}

    async def test_a_plain_text_answer_travels_as_its_text(self) -> None:
        socket = await drive(
            self.telling_server(), XT_Socket(request_message("/echo/plain", id="m1"))
        )
        assert socket.answers[0].data == "just text"

    async def test_an_empty_answer_carries_no_data(self) -> None:
        socket = await drive(
            self.telling_server(), XT_Socket(request_message("/echo/silent", id="m1"))
        )
        assert (socket.answers[0].status, socket.answers[0].data) == (204, None)

    async def test_a_msgpack_answer_comes_back_hydrated(self) -> None:
        # The third transport a TYTX answer may use: the mirror of what
        # `Request.decode_body` understands on the way in.
        socket = await drive(
            self.telling_server(), XT_Socket(request_message("/echo/msgpack", id="m1"))
        )
        assert socket.answers[0].data == {"n": 7}

    async def test_a_binary_answer_travels_as_its_bytes(self) -> None:
        # Now that the codec carries bytes, an answer nobody can decode as text
        # reaches the page as the bytes it is.
        socket = await drive(
            self.telling_server(), XT_Socket(request_message("/echo/binary", id="m1"))
        )
        assert socket.answers[0].data == b"\x89PNG\xff"

    async def test_a_streaming_answer_is_refused_out_loud(self) -> None:
        socket = await drive(
            self.telling_server(), XT_Socket(request_message("/echo/stream", id="m1"))
        )
        assert socket.answers[0].status == 500
        assert "streaming" in socket.answers[0].data


class TestTheDrain:
    async def test_what_is_still_in_flight_when_the_client_leaves_is_cut(
        self, monkeypatch: Any
    ) -> None:
        # The wait is bounded: a message whose handler hangs must not keep the
        # connection's own task alive for ever.
        import kajenn.wsx as wsx_module

        monkeypatch.setattr(wsx_module, "DRAIN_TIMEOUT_SECONDS", 0.01)
        started = asyncio.Event()

        class HangingApp(EchoApp):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                started.set()
                await asyncio.sleep(30)

        server = WsServer(applications=[HangingApp(mount="echo")])
        socket = await drive(server, XT_Socket(request_message("/echo/main", id="m1")))
        assert started.is_set()
        assert socket.answers == []


class TestAnAnswerNobodyIsThereFor:
    async def test_a_handler_that_ends_after_the_client_left_writes_nothing(self) -> None:
        # The message was served, but the socket is gone: writing to it would
        # raise, and there is nobody to read the answer anyway.
        class SlowApp(EchoApp):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                await asyncio.sleep(0.02)
                await super().__call__(scope, receive, send)

        server = WsServer(applications=[SlowApp(mount="echo")])
        socket = await drive(server, XT_Socket(request_message("/echo/main", id="m1")))
        assert socket.answers == []


class TestTheOrigin:
    async def test_no_origin_header_passes(self) -> None:
        socket = await drive(echo_server(websocket={"origins": ["https://app.example.org"]}), XT_Socket())
        assert socket.accepted

    async def test_a_listed_origin_passes(self) -> None:
        server = echo_server(websocket={"origins": ["https://app.example.org"]})
        socket = await drive(
            server,
            XT_Socket(),
            headers=[(b"host", b"example.org"), (b"origin", b"https://app.example.org")],
        )
        assert socket.accepted

    async def test_an_unlisted_origin_is_refused_without_an_accept(self) -> None:
        # The one refusal with no accept: nothing was admitted, so there is
        # nobody to tell.
        server = echo_server(websocket={"origins": ["https://app.example.org"]})
        socket = await drive(
            server,
            XT_Socket(),
            headers=[(b"host", b"example.org"), (b"origin", b"https://evil.example.com")],
        )
        assert not socket.accepted
        assert closure(socket)[0] == 1008 and "origin" in closure(socket)[1].lower()

    async def test_a_star_admits_every_origin(self) -> None:
        server = echo_server(websocket={"origins": ["*"]})
        socket = await drive(
            server,
            XT_Socket(),
            headers=[(b"host", b"example.org"), (b"origin", b"https://anywhere.example.com")],
        )
        assert socket.accepted

    async def test_with_no_list_the_origin_must_match_the_host(self) -> None:
        server = echo_server()
        same = await drive(
            server,
            XT_Socket(),
            headers=[(b"host", b"example.org"), (b"origin", b"https://example.org")],
        )
        other = await drive(
            server,
            XT_Socket(),
            headers=[(b"host", b"example.org"), (b"origin", b"https://evil.example.com")],
        )
        assert same.accepted
        assert not other.accepted and closure(other)[0] == 1008


class TestAMessageIsARequest:
    async def test_the_answer_carries_the_id_of_the_message(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/echo/main", id="m1")))
        assert [(a.id, a.status) for a in socket.answers] == [("m1", 200)]

    async def test_the_application_sees_the_method_wsk_and_the_path(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/echo/main", id="m1")))
        answered = socket.answers[0].data
        assert (answered["method"], answered["path"]) == ("WSK", "/main")

    async def test_the_data_of_the_message_reaches_the_application_hydrated(self) -> None:
        message = request_message("/echo/main", id="m1", data={"n": 41, "text": "ok"})
        socket = await drive(echo_server(), XT_Socket(message))
        assert socket.answers[0].data["data"] == {"n": 41, "text": "ok"}

    async def test_the_headers_of_the_handshake_travel_with_every_message(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/echo/main", id="m1")))
        assert socket.answers[0].data["host"] == "example.org"

    async def test_a_message_with_no_id_is_answered_by_nothing(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/echo/main")))
        assert socket.answers == []

    async def test_a_binary_frame_is_ignored_and_the_socket_lives_on(self) -> None:
        socket = XT_Socket(request_message("/echo/main", id="m1"))
        socket.incoming.insert(1, {"type": "websocket.receive", "bytes": b"\x00\x01"})
        await drive(echo_server(), socket)
        assert [a.id for a in socket.answers] == ["m1"]

    async def test_a_text_that_is_not_wsx_is_ignored_and_the_socket_lives_on(self) -> None:
        socket = await drive(
            echo_server(), XT_Socket("hello", request_message("/echo/main", id="m1"))
        )
        assert [a.id for a in socket.answers] == ["m1"]

    async def test_two_messages_are_answered_each_with_its_own_id(self) -> None:
        socket = await drive(
            echo_server(),
            XT_Socket(request_message("/echo/main", id="m1"), request_message("/echo/main", id="m2")),
        )
        assert sorted(a.id for a in socket.answers) == ["m1", "m2"]


class TestWhenTheApplicationRefuses:
    async def test_an_http_exception_becomes_the_status_of_the_answer(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/echo/forbidden", id="m1")))
        assert socket.answers[0].status == 403

    async def test_any_other_failure_is_a_500_and_the_socket_survives(self) -> None:
        socket = await drive(
            echo_server(),
            XT_Socket(request_message("/echo/boom", id="m1"), request_message("/echo/main", id="m2")),
        )
        assert [(a.id, a.status) for a in socket.answers] == [("m1", 500), ("m2", 200)]

    async def test_a_path_no_application_serves_is_a_404(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/nowhere/x", id="m1")))
        assert socket.answers[0].status == 404


class TestTheControlPing:
    async def test_the_ping_is_answered_by_the_server_itself(self) -> None:
        socket = await drive(echo_server(), XT_Socket(request_message("/_wsx/ping", id="m1")))
        assert [(a.id, a.status) for a in socket.answers] == [("m1", 200)]

    async def test_it_does_not_reach_any_application(self) -> None:
        # `/_wsx` is a reserved segment: no application is mounted there, and
        # a ping must be answered anyway.
        socket = await drive(echo_server(), XT_Socket(request_message("/_wsx/ping", id="m1")))
        assert socket.answers[0].data == "pong"


class TestWhatTheRegistryHolds:
    async def test_the_socket_is_in_the_registry_while_it_lives(self) -> None:
        server = echo_server()
        seen: list[int] = []

        class WatchingApp(EchoApp):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                seen.append(len(server.websockets.snapshot()))
                await super().__call__(scope, receive, send)

        server = WsServer(applications=[WatchingApp(mount="echo")])
        await drive(server, XT_Socket(request_message("/echo/main", id="m1")))
        assert seen == [1]
        assert server.websockets.snapshot() == []

    async def test_a_handshake_refused_at_the_gate_leaves_nothing_behind(self) -> None:
        server = echo_server()
        await drive(server, XT_Socket(), path="/nowhere/x")
        assert server.websockets.snapshot() == []


class TestWhatTheRequestRegistryCounts:
    async def test_a_message_with_an_id_is_a_registered_request(self) -> None:
        server = echo_server()
        counted: list[int] = []

        class CountingApp(EchoApp):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                counted.append(server.requests.in_flight)
                await super().__call__(scope, receive, send)

        server = WsServer(applications=[CountingApp(mount="echo")])
        await drive(server, XT_Socket(request_message("/echo/main", id="m1")))
        assert counted == [1]
        assert server.requests.in_flight == 0

    async def test_an_event_is_served_and_counted_nowhere(self) -> None:
        server = echo_server()
        counted: list[int] = []

        class CountingApp(EchoApp):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                counted.append(server.requests.in_flight)
                await super().__call__(scope, receive, send)

        server = WsServer(applications=[CountingApp(mount="echo")])
        await drive(server, XT_Socket(request_message("/echo/main")))
        assert counted == [0]
