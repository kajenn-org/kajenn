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

"""The WSX envelope: one message, both ways (#68 phase 1).

Contract tests. A WSX message is the ``WSX://`` prefix followed by JSON, and
``WsxEnvelope`` is that message as an object: built from the text a socket
delivered, or from the fields somebody is about to send.

The round trips here are EXECUTABLE EXAMPLES, not illustrations: ``data``
travels as the TYTX string placed inside the JSON envelope, and the values
below — Decimal, date, Bag, bytes, a string full of characters JSON has to
escape — are the ones a page will actually put in a message. Bytes travel as
the ``RAW`` type of genro-tytx 0.14.0, which the core pins (owner, 2026-09-06,
revising the same evening's «resta >=0.13.0»: «subito a 14! tutto»).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from genro_bag import Bag

from kajenn.wsx import WsxEnvelope

PREFIX = "WSX://"


def body_of(text: str) -> dict[str, Any]:
    """The JSON body of a WSX message — proof the wire text is valid JSON."""
    assert text.startswith(PREFIX)
    return dict(json.loads(text[len(PREFIX) :]))


class TestReadingAMessage:
    def test_a_full_request_carries_its_five_fields(self) -> None:
        envelope = WsxEnvelope(
            PREFIX + json.dumps({"id": "m1", "method": "WSK", "path": "/main/save"})
        )
        assert (envelope.id, envelope.method, envelope.path) == ("m1", "WSK", "/main/save")

    def test_a_message_with_no_id_is_an_event(self) -> None:
        envelope = WsxEnvelope(PREFIX + json.dumps({"method": "WSK", "path": "/ping"}))
        assert envelope.id is None

    def test_the_page_and_the_reply_path_travel_when_present(self) -> None:
        envelope = WsxEnvelope(
            PREFIX
            + json.dumps(
                {
                    "id": "m1",
                    "method": "WSK",
                    "path": "/main/slow",
                    "page_id": "p1",
                    "reply_path": "/main/done",
                }
            )
        )
        assert (envelope.page_id, envelope.reply_path) == ("p1", "/main/done")

    def test_they_are_absent_when_the_message_did_not_carry_them(self) -> None:
        envelope = WsxEnvelope(PREFIX + json.dumps({"id": "m1", "path": "/x"}))
        assert (envelope.page_id, envelope.reply_path) == (None, None)

    def test_a_message_with_no_data_reads_none(self) -> None:
        envelope = WsxEnvelope(PREFIX + json.dumps({"id": "m1", "path": "/x"}))
        assert envelope.data is None

    def test_a_text_without_the_prefix_is_not_an_envelope(self) -> None:
        with pytest.raises(ValueError, match="WSX://"):
            WsxEnvelope("hello")

    def test_a_broken_body_is_not_an_envelope(self) -> None:
        with pytest.raises(ValueError, match="JSON"):
            WsxEnvelope(PREFIX + "{not json")

    def test_a_body_that_is_not_an_object_is_not_an_envelope(self) -> None:
        with pytest.raises(ValueError, match="object"):
            WsxEnvelope(PREFIX + "[1, 2]")


class TestWritingAMessage:
    def test_the_wire_text_is_the_prefix_and_valid_json(self) -> None:
        text = WsxEnvelope(id="m1", method="WSK", path="/main/save").encode()
        assert body_of(text) == {"id": "m1", "method": "WSK", "path": "/main/save"}

    def test_absent_fields_do_not_reach_the_wire(self) -> None:
        # A server message carries no id (W-12), and an envelope must not put a
        # null there: the client tells an event from an answer by its absence.
        body = body_of(WsxEnvelope(method="WSK", path="/main/refresh").encode())
        assert "id" not in body and "page_id" not in body and "status" not in body

    def test_the_page_and_the_reply_path_reach_the_wire(self) -> None:
        body = body_of(
            WsxEnvelope(
                method="WSK", path="/main/x", page_id="p1", reply_path="/main/done"
            ).encode()
        )
        assert (body["page_id"], body["reply_path"]) == ("p1", "/main/done")

    def test_an_answer_carries_the_id_it_answers_and_a_status(self) -> None:
        incoming = WsxEnvelope(PREFIX + json.dumps({"id": "m1", "path": "/main/save"}))
        body = body_of(WsxEnvelope(id=incoming.id, status=200, data={"saved": True}).encode())
        assert (body["id"], body["status"]) == ("m1", 200)

    def test_an_error_answer_is_the_same_shape(self) -> None:
        body = body_of(WsxEnvelope(id="m1", status=500, data="RuntimeError: boom").encode())
        assert (body["id"], body["status"]) == ("m1", 500)
        assert WsxEnvelope(PREFIX + json.dumps(body)).data == "RuntimeError: boom"

    def test_a_status_of_zero_would_still_reach_the_wire(self) -> None:
        # Guarding on truthiness instead of on absence would drop it.
        assert body_of(WsxEnvelope(id="m1", status=0).encode())["status"] == 0


class TestTheDataRoundTrip:
    def wire_back(self, value: Any) -> Any:
        """Send a value through the whole wire and read it back."""
        text = WsxEnvelope(id="m1", path="/x", data=value).encode()
        return WsxEnvelope(text).data

    def test_a_plain_mapping_comes_back_equal(self) -> None:
        assert self.wire_back({"a": 1, "b": [1, 2, 3]}) == {"a": 1, "b": [1, 2, 3]}

    def test_a_decimal_comes_back_exact(self) -> None:
        assert self.wire_back({"total": Decimal("3.14")}) == {"total": Decimal("3.14")}

    def test_a_date_and_a_datetime_keep_their_type(self) -> None:
        sent = {"day": date(2026, 9, 6), "ts": datetime(2026, 9, 6, 21, 30)}
        back = self.wire_back(sent)
        assert isinstance(back["day"], date) and back["day"] == date(2026, 9, 6)
        assert isinstance(back["ts"], datetime)

    def test_null_survives_as_null(self) -> None:
        assert self.wire_back({"nothing": None}) == {"nothing": None}

    def test_a_string_full_of_escapes_comes_back_character_for_character(self) -> None:
        hard = 'he said "x"\nsecond\tline\\end — èùñ 中文  '
        assert self.wire_back({"text": hard}) == {"text": hard}

    def test_a_bag_comes_back_a_bag_with_its_values_and_attributes(self) -> None:
        bag = Bag()
        bag.set_item("order.total", Decimal("9.99"), currency="EUR")
        back = self.wire_back({"bag": bag})["bag"]
        assert isinstance(back, Bag)
        assert back["order.total"] == Decimal("9.99")
        assert back.get_attr("order.total") == {"currency": "EUR"}

    def test_bytes_come_back_bytes(self) -> None:
        assert self.wire_back({"blob": b"\x00\x01\xff"}) == {"blob": b"\x00\x01\xff"}

    def test_empty_bytes_are_not_lost(self) -> None:
        assert self.wire_back({"blob": b""}) == {"blob": b""}

    def test_bytes_travel_base64_inside_the_json_body(self) -> None:
        body = body_of(WsxEnvelope(id="m1", path="/x", data={"blob": b"ab"}).encode())
        assert "YWI=::RAW" in body["data"]

    def test_a_nested_bag_keeps_its_depth(self) -> None:
        inner = Bag()
        inner.set_item("leaf", "v")
        bag = Bag()
        bag.set_item("branch", inner)
        back = self.wire_back({"bag": bag})["bag"]
        assert isinstance(back["branch"], Bag)
        assert back["branch.leaf"] == "v"

    def test_data_travels_as_a_string_inside_the_json_body(self) -> None:
        # The shape the contract promises to whoever writes a client: the body
        # is JSON, and `data` inside it is a TYTX string to hydrate, not a
        # nested object to read field by field.
        body = body_of(WsxEnvelope(id="m1", path="/x", data={"d": Decimal("1.5")}).encode())
        assert isinstance(body["data"], str)
        assert "1.5" in body["data"]


class TestHowAnEnvelopeReadsInALog:
    def test_a_request_tells_its_method_and_path(self) -> None:
        told = repr(WsxEnvelope(id="m1", method="WSK", path="/main/save"))
        assert "WSK /main/save" in told and "m1" in told

    def test_an_answer_tells_its_status(self) -> None:
        assert "status 403" in repr(WsxEnvelope(id="m1", status=403))


class TestTheRoundTripOfTheWholeEnvelope:
    def test_what_is_written_is_what_is_read(self) -> None:
        sent = WsxEnvelope(
            id="m1",
            method="WSK",
            path="/main/save",
            data={"n": 1},
            page_id="p1",
            reply_path="/main/done",
        )
        back = WsxEnvelope(sent.encode())
        assert (back.id, back.method, back.path) == ("m1", "WSK", "/main/save")
        assert (back.page_id, back.reply_path, back.data) == ("p1", "/main/done", {"n": 1})

    def test_an_answer_read_back_carries_its_status(self) -> None:
        back = WsxEnvelope(WsxEnvelope(id="m1", status=403, data="not yours").encode())
        assert (back.id, back.status, back.data) == ("m1", 403, "not yours")
