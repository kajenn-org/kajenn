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

"""Response tests (Macro 4 Phase 1): the one flat, buffered, TYTX-aware class.

Drives the Response as an ASGI app with a recording ``send`` and asserts the
wire shape (exactly two messages), then covers ``set_header``/``set_cookie``/
``set_result``/``set_error`` and the TYTX branch through a stub request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from genro_tytx import json_dumps, to_tytx

from kajenn import HTTPForbidden, HTTPNotFound, HTTPUnauthorized, Response
from kajenn.media_types import TRANSPORT_MIME
from kajenn.types import Message


class StubRequest:
    """Minimal request stand-in exposing the TYTX contract Response reads."""

    def __init__(self, tytx_mode: bool = False, tytx_transport: str | None = None) -> None:
        self.tytx_mode = tytx_mode
        self.tytx_transport = tytx_transport


async def drive(response: Response) -> list[Message]:
    """Run ``response`` as an ASGI app; return the messages it sent."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await response({}, receive, send)
    return sent


def start_headers(sent: list[Message]) -> dict[bytes, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"])


class TestConstruction:
    def test_content_status_media_type(self) -> None:
        response = Response(content="Hello", status_code=201, media_type="text/plain")
        assert response.status_code == 201
        assert response.body == b"Hello"

    def test_str_content_encoded_utf8(self) -> None:
        response = Response(content="cafè")
        assert response.body == "cafè".encode()

    def test_bytes_content_as_is(self) -> None:
        response = Response(content=b"\x00\x01\x02")
        assert response.body == b"\x00\x01\x02"

    def test_none_content_is_empty(self) -> None:
        response = Response()
        assert response.body == b""

    def test_headers_from_list_preserved(self) -> None:
        response = Response(content="x", headers=[("x-a", "1")])
        assert ("x-a", "1") in response._headers

    def test_headers_from_mapping_preserved(self) -> None:
        response = Response(content="x", headers={"x-b": "2"})
        assert ("x-b", "2") in response._headers

    def test_text_media_type_gets_charset(self) -> None:
        response = Response(content="hi", media_type="text/plain")
        headers = dict(response._headers)
        assert headers["content-type"] == "text/plain; charset=utf-8"

    def test_non_text_media_type_no_charset(self) -> None:
        response = Response(content=b"{}", media_type="application/json")
        headers = dict(response._headers)
        assert headers["content-type"] == "application/json"

    def test_content_length_set(self) -> None:
        response = Response(content="hello")
        headers = dict(response._headers)
        assert headers["content-length"] == "5"


class TestAsgiCall:
    async def test_emits_exactly_start_and_body(self) -> None:
        sent = await drive(Response(content="hi", media_type="text/plain"))
        assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]

    async def test_wire_status_headers_body(self) -> None:
        sent = await drive(Response(content="hi", status_code=202, media_type="text/plain"))
        start = sent[0]
        assert start["status"] == 202
        headers = dict(start["headers"])
        assert headers[b"content-type"] == b"text/plain; charset=utf-8"
        assert headers[b"content-length"] == b"2"
        assert sent[1]["body"] == b"hi"

    async def test_header_names_lowercased_latin1(self) -> None:
        response = Response(content="x")
        response.set_header("X-Custom", "value")
        sent = await drive(response)
        headers = dict(sent[0]["headers"])
        assert headers[b"x-custom"] == b"value"


class TestSetHeaderAndCookie:
    def test_set_header_appends(self) -> None:
        response = Response(content="x")
        response.set_header("x-one", "a")
        response.set_header("x-one", "b")
        values = [v for k, v in response._headers if k == "x-one"]
        assert values == ["a", "b"]

    def test_set_cookie_basic(self) -> None:
        response = Response(content="x")
        response.set_cookie("session", "abc")
        cookie = dict(response._headers)["set-cookie"]
        assert cookie == "session=abc; Path=/; SameSite=Lax"

    def test_set_cookie_all_attributes(self) -> None:
        response = Response(content="x")
        response.set_cookie(
            "k",
            "v",
            max_age=3600,
            path="/app",
            domain="example.com",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        cookie = dict(response._headers)["set-cookie"]
        assert cookie == (
            "k=v; Max-Age=3600; Path=/app; Domain=example.com; "
            "Secure; HttpOnly; SameSite=Strict"
        )

    def test_set_cookie_value_url_encoded(self) -> None:
        response = Response(content="x")
        response.set_cookie("k", "a b/c")
        cookie = dict(response._headers)["set-cookie"]
        assert cookie.startswith(f"k={quote('a b/c', safe='')}")

    def test_set_cookie_samesite_none_omitted(self) -> None:
        response = Response(content="x")
        response.set_cookie("k", "v", samesite=None)
        cookie = dict(response._headers)["set-cookie"]
        assert "SameSite" not in cookie


class TestSetResult:
    def test_dict_to_json_via_json_dumps(self) -> None:
        response = Response()
        response.set_result({"a": 1, "b": 2})
        assert response.body == json_dumps({"a": 1, "b": 2})
        assert dict(response._headers)["content-type"] == "application/json"

    def test_list_to_json(self) -> None:
        response = Response()
        response.set_result([1, 2, 3])
        assert response.body == json_dumps([1, 2, 3])

    def test_path_to_bytes_octet_stream(self, tmp_path: Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x01\x02\x03")
        response = Response()
        response.set_result(f)
        assert response.body == b"\x01\x02\x03"
        assert dict(response._headers)["content-type"] == "application/octet-stream"

    def test_bytes_result(self) -> None:
        response = Response()
        response.set_result(b"raw")
        assert response.body == b"raw"
        assert dict(response._headers)["content-type"] == "application/octet-stream"

    def test_str_result_text_plain(self) -> None:
        response = Response()
        response.set_result("hello")
        assert response.body == b"hello"
        assert dict(response._headers)["content-type"] == "text/plain; charset=utf-8"

    def test_none_result_empty_body(self) -> None:
        response = Response()
        response.set_result(None)
        assert response.body == b""
        assert dict(response._headers)["content-length"] == "0"

    def test_other_result_str_conversion(self) -> None:
        response = Response()
        response.set_result(42)
        assert response.body == b"42"

    def test_metadata_media_type_override(self) -> None:
        response = Response()
        response.set_result({"a": 1}, metadata={"media_type": "application/vnd.custom+json"})
        assert dict(response._headers)["content-type"] == "application/vnd.custom+json"

    def test_content_headers_replaced_not_duplicated(self) -> None:
        response = Response(content="old", media_type="text/plain")
        response.set_result({"a": 1})
        content_types = [v for k, v in response._headers if k.lower() == "content-type"]
        content_lengths = [v for k, v in response._headers if k.lower() == "content-length"]
        assert content_types == ["application/json"]
        assert content_lengths == [str(len(b'{"a":1}'))]


class TestTytxBranch:
    def test_tytx_json_transport(self) -> None:
        request = StubRequest(tytx_mode=True, tytx_transport="json")
        response = Response(request=request)
        response.set_result({"a": 1})
        assert response.body.decode("utf-8") == to_tytx({"a": 1}, "json")
        # media type is sourced from genro-tytx, never a local literal
        assert dict(response._headers)["content-type"] == TRANSPORT_MIME["json"]

    def test_tytx_msgpack_transport_bytes(self) -> None:
        request = StubRequest(tytx_mode=True, tytx_transport="msgpack")
        response = Response(request=request)
        response.set_result({"a": 1})
        assert response.body == to_tytx({"a": 1}, "msgpack")
        assert dict(response._headers)["content-type"] == TRANSPORT_MIME["msgpack"]

    def test_tytx_default_transport_json_when_missing(self) -> None:
        request = StubRequest(tytx_mode=True, tytx_transport=None)
        response = Response(request=request)
        response.set_result([1, 2])
        assert dict(response._headers)["content-type"] == TRANSPORT_MIME["json"]

    def test_no_request_falls_back_to_json(self) -> None:
        response = Response(request=None)
        response.set_result({"a": 1})
        assert response.body == b'{"a":1}'
        assert dict(response._headers)["content-type"] == "application/json"

    def test_tytx_mode_off_uses_json(self) -> None:
        request = StubRequest(tytx_mode=False)
        response = Response(request=request)
        response.set_result({"a": 1})
        assert response.body == b'{"a":1}'


class TestSetError:
    def test_http_exception_carries_own_status(self) -> None:
        response = Response()
        response.set_error(HTTPNotFound("missing"))
        assert response.status_code == 404
        assert json.loads(response.body) == {"error": "missing"}

    def test_http_unauthorized_status(self) -> None:
        response = Response()
        response.set_error(HTTPUnauthorized())
        assert response.status_code == 401

    def test_http_forbidden_status(self) -> None:
        response = Response()
        response.set_error(HTTPForbidden())
        assert response.status_code == 403

    def test_value_error_maps_400(self) -> None:
        response = Response()
        response.set_error(ValueError("bad"))
        assert response.status_code == 400
        assert json.loads(response.body) == {"error": "bad"}

    def test_type_error_maps_400(self) -> None:
        response = Response()
        response.set_error(TypeError("wrong type"))
        assert response.status_code == 400

    def test_file_not_found_maps_404(self) -> None:
        response = Response()
        response.set_error(FileNotFoundError("nope"))
        assert response.status_code == 404

    def test_permission_error_maps_403(self) -> None:
        response = Response()
        response.set_error(PermissionError("denied"))
        assert response.status_code == 403

    def test_unknown_exception_maps_500(self) -> None:
        response = Response()

        class Boom(Exception):
            pass

        response.set_error(Boom("kaboom"))
        assert response.status_code == 500
        assert json.loads(response.body) == {"error": "kaboom"}

    async def test_error_body_wire_shape(self) -> None:
        response = Response()
        response.set_error(HTTPNotFound("gone"))
        sent = await drive(response)
        assert sent[0]["status"] == 404
        headers: dict[bytes, Any] = dict(sent[0]["headers"])
        assert headers[b"content-type"] == b"application/json"
