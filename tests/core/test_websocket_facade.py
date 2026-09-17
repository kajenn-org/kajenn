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

"""The websocket facade: accept, close, read, write, iterate (#68 phase 1).

Contract tests. ``WebSocket`` is the neutral object over the ASGI websocket
protocol — it knows nothing of WSX — so the behaviours here are the ones both
the WSX motor and the admitted raw seam stand on: an accept that consumes the
connect, a close that happens once, reads that refuse the wrong kind of
payload, writes that refuse a socket nobody accepted, and a disconnect that
arrives as an exception rather than as a value.

The behaviours are the ones the old repo's facade covered
(`genro-asgi-legacy/src/kajenn/websocket.py:194-441`); nothing is carried
over as code (decisions.md §12).
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn.exceptions import WebSocketDisconnect
from kajenn.websocket import WebSocket


class XT_Wire:
    """One scripted ASGI websocket wire: messages in, messages out."""

    def __init__(self, *incoming: dict[str, Any]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        if not self.incoming:
            return {"type": "websocket.disconnect", "code": 1006, "reason": ""}
        return self.incoming.pop(0)

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def socket_on(wire: XT_Wire, **scope: Any) -> WebSocket:
    """A facade over that wire, on a plausible handshake scope."""
    full: dict[str, Any] = {
        "type": "websocket",
        "path": "/spa/_wsx",
        "headers": [(b"host", b"example.org"), (b"cookie", b"spa_connection_id=c1")],
        "subprotocols": [],
    }
    full.update(scope)
    return WebSocket(full, wire.receive, wire.send)


class TestAccept:
    async def test_the_connect_is_consumed_and_the_accept_is_sent(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        assert wire.sent == [{"type": "websocket.accept"}]
        assert wire.incoming == []

    async def test_a_subprotocol_travels_in_the_accept(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire, subprotocols=["wsx"])
        await ws.accept(subprotocol="wsx")
        assert wire.sent == [{"type": "websocket.accept", "subprotocol": "wsx"}]
        assert ws.accepted_subprotocol == "wsx"

    async def test_headers_travel_as_byte_pairs(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept(headers={"Set-Cookie": "spa_connection_id=c1"})
        assert wire.sent[0]["headers"] == [(b"Set-Cookie", b"spa_connection_id=c1")]

    async def test_a_second_accept_is_refused(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        with pytest.raises(RuntimeError, match="accept"):
            await ws.accept()

    async def test_a_first_message_that_is_not_the_connect_is_refused(self) -> None:
        wire = XT_Wire({"type": "websocket.receive", "text": "too early"})
        ws = socket_on(wire)
        with pytest.raises(RuntimeError, match="websocket.connect"):
            await ws.accept()


class TestConnected:
    async def test_it_is_false_before_the_accept_and_true_after(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        assert ws.connected is False
        await ws.accept()
        assert ws.connected is True

    async def test_it_is_false_again_after_the_close(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        await ws.close()
        assert ws.connected is False


class TestClose:
    async def test_the_code_and_the_reason_travel(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        await ws.close(1008, "connection cookie required")
        assert wire.sent[-1] == {
            "type": "websocket.close",
            "code": 1008,
            "reason": "connection cookie required",
        }

    async def test_the_default_close_is_1000_with_no_reason(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        await ws.close()
        assert wire.sent[-1] == {"type": "websocket.close", "code": 1000, "reason": ""}

    async def test_closing_twice_writes_once(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        await ws.close(1013)
        await ws.close(1000)
        assert [m for m in wire.sent if m["type"] == "websocket.close"] == [
            {"type": "websocket.close", "code": 1013, "reason": ""}
        ]

    async def test_closing_before_the_accept_is_refused(self) -> None:
        # The refusal of a handshake still accepts first, THEN closes: a close
        # with nothing accepted leaves the client with no readable answer.
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        with pytest.raises(RuntimeError, match="not accepted"):
            await ws.close()


class TestRefuse:
    async def test_the_handshake_is_turned_away_with_no_accept(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.refuse(1008, "origin not allowed")
        assert wire.sent == [
            {"type": "websocket.close", "code": 1008, "reason": "origin not allowed"}
        ]
        assert ws.connected is False

    async def test_the_connect_is_consumed_first(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        await socket_on(wire).refuse()
        assert wire.incoming == []

    async def test_refusing_what_was_accepted_is_itself_refused(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        with pytest.raises(RuntimeError, match="accepted already"):
            await ws.refuse()

    async def test_nothing_can_be_written_to_a_refused_socket(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.refuse()
        with pytest.raises(RuntimeError, match="not connected"):
            await ws.send_text("late")


class TestReceive:
    async def test_text_comes_back_as_text(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": "WSX://{}"},
        )
        ws = socket_on(wire)
        await ws.accept()
        assert await ws.receive_text() == "WSX://{}"

    async def test_bytes_come_back_as_bytes(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
        )
        ws = socket_on(wire)
        await ws.accept()
        assert await ws.receive_bytes() == b"\x00\x01"

    async def test_binary_read_as_text_is_refused(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "bytes": b"\x00"},
        )
        ws = socket_on(wire)
        await ws.accept()
        with pytest.raises(TypeError, match="binary"):
            await ws.receive_text()

    async def test_text_read_as_binary_is_refused(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": "hello"},
        )
        ws = socket_on(wire)
        await ws.accept()
        with pytest.raises(TypeError, match="text"):
            await ws.receive_bytes()

    async def test_reading_before_the_accept_is_refused(self) -> None:
        ws = socket_on(XT_Wire({"type": "websocket.connect"}))
        with pytest.raises(RuntimeError, match="not connected"):
            await ws.receive_text()

    async def test_a_disconnect_raises_with_its_code_and_reason(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1001, "reason": "going away"},
        )
        ws = socket_on(wire)
        await ws.accept()
        with pytest.raises(WebSocketDisconnect) as caught:
            await ws.receive_text()
        assert (caught.value.code, caught.value.reason) == (1001, "going away")
        assert ws.connected is False

    async def test_a_disconnect_with_no_code_reads_1000(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect"},
        )
        ws = socket_on(wire)
        await ws.accept()
        with pytest.raises(WebSocketDisconnect) as caught:
            await ws.receive_text()
        assert (caught.value.code, caught.value.reason) == (1000, "")


class TestSend:
    async def test_text_and_bytes_travel_in_their_own_key(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        await ws.send_text("WSX://{}")
        await ws.send_bytes(b"\xff")
        assert wire.sent[1:] == [
            {"type": "websocket.send", "text": "WSX://{}"},
            {"type": "websocket.send", "bytes": b"\xff"},
        ]

    async def test_writing_before_the_accept_is_refused(self) -> None:
        ws = socket_on(XT_Wire({"type": "websocket.connect"}))
        with pytest.raises(RuntimeError, match="not connected"):
            await ws.send_text("early")

    async def test_writing_after_the_close_is_refused(self) -> None:
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        await ws.close()
        with pytest.raises(RuntimeError, match="not connected"):
            await ws.send_text("late")


class TestIteration:
    async def test_the_texts_arrive_in_order_and_the_disconnect_ends_the_loop(self) -> None:
        wire = XT_Wire(
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": "one"},
            {"type": "websocket.receive", "text": "two"},
            {"type": "websocket.disconnect", "code": 1000},
        )
        ws = socket_on(wire)
        await ws.accept()
        assert [message async for message in ws] == ["one", "two"]
        assert ws.connected is False

    async def test_the_loop_ends_on_a_wire_that_died(self) -> None:
        # XT_Wire answers 1006 when its script runs out: an abrupt death is
        # the ordinary end of a read loop, not an error to report.
        wire = XT_Wire({"type": "websocket.connect"})
        ws = socket_on(wire)
        await ws.accept()
        assert [message async for message in ws] == []


class TestTheHandshakeFacts:
    async def test_the_path_and_the_headers_are_read_off_the_scope(self) -> None:
        ws = socket_on(XT_Wire())
        assert ws.path == "/spa/_wsx"
        assert ws.headers["host"] == "example.org"

    async def test_the_cookie_header_becomes_the_cookies(self) -> None:
        ws = socket_on(XT_Wire())
        assert ws.cookies["spa_connection_id"] == "c1"

    async def test_a_scope_with_no_headers_reads_empty(self) -> None:
        ws = socket_on(XT_Wire(), headers=[])
        assert (ws.headers, ws.cookies) == ({}, {})

    async def test_the_subprotocols_the_client_offered_are_readable(self) -> None:
        ws = socket_on(XT_Wire(), subprotocols=["wsx", "json"])
        assert ws.subprotocols == ("wsx", "json")
        assert ws.accepted_subprotocol is None
