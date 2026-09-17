"""Malformed internal records fail as protocol errors, never uncontrolled recursion."""

import json
import struct

import pytest
from genro_tytx import to_tytx

from kajenn.channel.frame import FrameCodec
from kajenn.http_record import HttpRecord
from kajenn.wsx import WsxEnvelope
from kajenn.wsx_payload import SerializedWsxPayload


@pytest.mark.parametrize("record", ["frame", "http"])
def test_deep_bounded_json_is_a_protocol_error(record):
    nested = b'{"v":' + b"[" * 3000 + b"0" + b"]" * 3000 + b"}"
    with pytest.raises(ValueError):
        if record == "frame":
            FrameCodec().get_frame(struct.pack("!4sBII", b"GNRF", 1, len(nested), 0) + nested)
        elif record == "http":
            HttpRecord().decode(b"HTTP\x01" + struct.pack("!I", len(nested)) + nested)


def test_wsx_serialized_null_is_distinct_from_absent_without_changing_legacy_alias():
    absent = WsxEnvelope("WSX://{}")
    legacy_null = WsxEnvelope('WSX://{"data":null}')
    serialized = WsxEnvelope(serialized_data=SerializedWsxPayload(to_tytx(None, "json")))
    assert "data" not in json.loads(absent.encode()[6:])
    assert legacy_null.encode() == absent.encode()
    assert "data" in json.loads(serialized.encode()[6:])
    assert serialized.data is None
    assert WsxEnvelope(serialized.encode()).serialized_data.text == serialized.serialized_data.text
