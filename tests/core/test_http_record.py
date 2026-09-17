import struct

import pytest

from kajenn.http_record import HttpRecord


def test_generic_record_roundtrip_and_wire_header():
    record = HttpRecord()
    payload = record.encode({"route": "/thing", "count": 2}, b"\x00body\xff")

    assert payload[:5] == b"HTTP\x01"
    metadata_size = struct.unpack(">I", payload[5:9])[0]
    assert payload[9 : 9 + metadata_size].startswith(b'{"route"')
    assert record.decode(payload) == ({"route": "/thing", "count": 2}, b"\x00body\xff")


def test_request_roundtrip_preserves_raw_bytes_duplicate_headers_and_addresses():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": "/caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "raw_path": b"/caf\xc3\xa9",
        "root_path": "/mounted",
        "query_string": b"value=\xff&value=2",
        "headers": [(b"x-repeat", b"one"), (b"x-repeat", b"\xfftwo")],
        "scheme": "https",
        "server": ("example.test", 443),
        "client": ("127.0.0.1", 32100),
        "http_version": "1.1",
        "state": {"must": "not cross"},
    }

    decoded, body = HttpRecord().decode_request(HttpRecord().encode_request(scope, b"request"))

    assert body == b"request"
    assert decoded == {key: value for key, value in scope.items() if key in HttpRecord._REQUEST_FIELDS} | {
        "type": "http"
    }
    assert decoded["headers"][0][0] == decoded["headers"][1][0]


def test_response_roundtrip_preserves_order_and_duplicate_headers():
    response = {
        "status": 201,
        "headers": [["set-cookie", "a=1"], ["set-cookie", "b=2"]],
        "body": b"\x00result\xff",
    }
    assert HttpRecord().decode_response(HttpRecord().encode_response(response)) == response


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"HTTP\x01\x00\x00\x00",
        b"NOPE\x01\x00\x00\x00\x02{}",
        b"HTTP\x02\x00\x00\x00\x02{}",
        b"HTTP\x01\x00\x00\x00\x05{}",
    ],
)
def test_decode_rejects_bad_or_truncated_records(payload):
    with pytest.raises(ValueError):
        HttpRecord().decode(payload)


@pytest.mark.parametrize(
    "json_bytes",
    [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b'{"x":'],
)
def test_decode_rejects_duplicate_nonfinite_nonobject_and_malformed_json(json_bytes):
    payload = b"HTTP\x01" + struct.pack(">I", len(json_bytes)) + json_bytes
    with pytest.raises(ValueError):
        HttpRecord().decode(payload)


def test_metadata_and_body_limits_are_enforced_on_both_paths():
    record = HttpRecord(max_body_size=3)
    with pytest.raises(ValueError):
        record.encode({}, b"four")
    with pytest.raises(ValueError):
        record.decode(b"HTTP\x01\x00\x00\x00\x02{}four")
    metadata = {"large": "x" * (64 * 1024)}
    assert HttpRecord().decode(HttpRecord().encode(metadata, b"")) == (metadata, b"")
    with pytest.raises(ValueError):
        HttpRecord().decode(b"HTTP\x01" + struct.pack(">I", 64 * 1024 + 1))


def test_generic_encode_rejects_unknown_python_types_and_nonfinite_numbers():
    with pytest.raises(ValueError):
        HttpRecord().encode({"value": object()}, b"")
    with pytest.raises(ValueError):
        HttpRecord().encode({"value": float("inf")}, b"")
    with pytest.raises(ValueError):
        HttpRecord().encode({1: "not a JSON object key"}, b"")


def test_request_rejects_invalid_bytes_headers_and_reserved_metadata_injection():
    with pytest.raises(TypeError):
        HttpRecord().encode_request({"headers": [("text", b"value")]}, b"")
    with pytest.raises(ValueError):
        HttpRecord().encode_request({"type": "websocket"}, b"")

    injected = HttpRecord().encode(
        {"record_type": "request", "scope": {"method": "GET", "identity": "admin"}}, b""
    )
    with pytest.raises(ValueError):
        HttpRecord().decode_request(injected)


@pytest.mark.parametrize("status", [True, 99, 600, "200"])
def test_response_rejects_invalid_status(status):
    with pytest.raises((TypeError, ValueError)):
        HttpRecord().encode_response({"status": status, "headers": [], "body": b""})


def test_response_decoder_rejects_wrong_record_type_and_unknown_fields():
    record = HttpRecord()
    with pytest.raises(ValueError):
        record.decode_response(record.encode({"record_type": "request", "status": 200, "headers": []}, b""))
    with pytest.raises(ValueError):
        record.decode_response(
            record.encode(
                {"record_type": "response", "status": 200, "headers": [], "identity": "injected"}, b""
            )
        )


def test_body_and_payload_require_exact_bytes():
    with pytest.raises(TypeError):
        HttpRecord().encode({}, bytearray())
    with pytest.raises(TypeError):
        HttpRecord().decode(bytearray(b"HTTP\x01\x00\x00\x00\x02{}"))
