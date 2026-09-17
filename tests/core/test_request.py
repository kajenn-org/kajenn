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

"""Request tests (Phase 1c/2): the HTTP request parses a real ASGI scope
(method/path/query/headers/cookies), body dispatch through ``handler_kwargs``
follows the content-type (form merges, hydrated body → ``body_data``, the bytes
of a raw application → ``body_raw``, empty body → query only), the request id
comes from the header or is generated, TYTX mode is read off the header, and
auth/session ride the scope.

Body decoding is exercised per content-type (xml/msgpack/json hydration, an
urlencoded form with typed values, a body split over several ASGI messages) and
multipart forms deliver text fields hydrated and file parts as ``UploadedFile``
kwargs. A content-type the core cannot decode is refused with 415 while
decoding and kept whole for an application whose recipe declares
``request(body="raw")`` (issues #87, #91).

The ``db`` preparation layer is exercised end-to-end through the server: a fake
app touches ``request.db`` and the server drains ``closeConnection`` at end of
request; ``get_db`` never registers a cleanup; an unregistered code answers
``None``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from genro_tytx import to_tytx

from kajenn import (
    AsgiServer,
    Avatar,
    BaseApplication,
    BaseServer,
    Request,
    Response,
    Session,
    UploadedFile,
)
from kajenn.config.templates import DefaultConfiguration
from kajenn.exceptions import HTTPUnsupportedMediaType
from kajenn.types import Receive, Scope, Send


class RawBodyApp(BaseApplication):
    """An application whose site declares ``request(body="raw")`` for it."""


class RawBodySite(DefaultConfiguration):
    """The one road to the option (#91): the recipe of the site that mounts it."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        self.server_section(cfg)
        self.storage_section(cfg)
        apps = cfg.applications()
        apps.application(code="raw", mount="", app_class=RawBodyApp).request(body="raw")


def raw_body_app() -> BaseApplication:
    """The mounted ``RawBodyApp``, reading its option off its own site."""
    return AsgiServer(config=RawBodySite).applications["raw"]


async def make_request(
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query: bytes = b"",
    body: bytes = b"",
    chunks: list[bytes] | None = None,
    method: str = "GET",
    path: str = "/",
    scope_extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Request:
    """Build a ``Request`` from a synthetic ASGI scope and init it.

    ``chunks`` delivers the body in several ``http.request`` messages (the last
    one alone with ``more_body`` false); ``body`` delivers it in a single one.
    """
    pending = list(chunks) if chunks is not None else [body]

    async def receive() -> dict[str, Any]:
        chunk = pending.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }
    if scope_extra:
        scope.update(scope_extra)
    request = Request(scope, receive, **kwargs)
    await request.init()
    return request


class TestParsing:
    async def test_parses_method_path_query_headers_cookies(self) -> None:
        request = await make_request(
            method="post",
            path="/users",
            query=b"page=2&q=hello",
            headers=[
                (b"content-type", b"application/json"),
                (b"x-request-id", b"req-123"),
                (b"cookie", b"sid=abc; theme=dark"),
            ],
        )
        assert request.method == "POST"  # uppercased
        assert request.path == "/users"
        assert request.query == {"page": 2, "q": "hello"}  # typed via TYTX
        assert request.content_type == "application/json"
        assert request.cookies == {"sid": "abc", "theme": "dark"}
        assert request.id == "req-123"

    async def test_response_is_bound_back_to_the_request(self) -> None:
        request = await make_request()
        assert isinstance(request.response, Response)
        assert request.response.request is request

    async def test_numeric_id_headers_are_coerced_to_str(self) -> None:
        # Header values are TYTX-hydrated: "123" arrives as int 123;
        # the id/external_id contract is str regardless.
        request = await make_request(
            headers=[(b"x-request-id", b"123"), (b"x-external-id", b"456")]
        )
        assert request.id == "123"
        assert request.external_id == "456"


class TestHandlerKwargs:
    async def test_json_body_passed_whole_as_body_data(self) -> None:
        request = await make_request(
            method="POST",
            query=b"page=1",
            headers=[(b"content-type", b"application/json")],
            body=b'{"name":"ada","age":36}',
        )
        assert request.data == {"name": "ada", "age": 36}
        assert request.handler_kwargs() == {"page": 1, "body_data": {"name": "ada", "age": 36}}

    async def test_urlencoded_body_merges_typed_and_wins_on_clash(self) -> None:
        request = await make_request(
            method="POST",
            query=b"a=9&page=2",
            headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            body=b"a=1&b=hello",
        )
        # The urlencoded body is hydrated via TYTX from_qs: it arrives as a
        # typed dict, not raw bytes.
        assert request.data == {"a": 1, "b": "hello"}
        kwargs = request.handler_kwargs()
        assert kwargs == {"a": 1, "b": "hello", "page": 2}  # body 'a' wins over query 'a'

    async def test_a_raw_body_is_passed_as_body_raw(self) -> None:
        request = await make_request(
            method="POST",
            query=b"x=1",
            headers=[(b"content-type", b"application/octet-stream")],
            body=b"\x00\x01\x02",
            application=raw_body_app(),
        )
        assert request.data == b"\x00\x01\x02"
        assert request.handler_kwargs() == {"x": 1, "body_raw": b"\x00\x01\x02"}

    async def test_empty_body_yields_query_only(self) -> None:
        request = await make_request(method="GET", query=b"x=1&y=two")
        assert request.data is None
        assert request.handler_kwargs() == {"x": 1, "y": "two"}


class TestBodyDecoding:
    async def test_xml_body_hydrated(self) -> None:
        payload = to_tytx({"root": {"attrs": {}, "value": Decimal("100.50")}}, transport="xml")
        request = await make_request(
            method="POST",
            headers=[(b"content-type", b"application/vnd.tytx+xml")],
            body=payload.encode("utf-8"),
        )
        assert request.data == {"root": {"attrs": {}, "value": Decimal("100.50")}}

    async def test_msgpack_body_hydrated(self) -> None:
        payload = to_tytx({"price": Decimal("100.50")}, transport="msgpack")
        request = await make_request(
            method="POST",
            headers=[(b"content-type", b"application/vnd.tytx+msgpack")],
            body=payload,
        )
        assert request.data == {"price": Decimal("100.50")}

    async def test_standard_json_media_type_hydrates_like_the_tytx_one(self) -> None:
        payload = to_tytx({"price": Decimal("100.50")}, transport="json")
        request = await make_request(
            method="POST",
            headers=[(b"content-type", b"application/json")],
            body=payload.encode("utf-8"),
        )
        assert request.data == {"price": Decimal("100.50")}

    async def test_unknown_content_type_is_refused_while_decoding(self) -> None:
        blob = b"\x89PNG\r\n\x1a\n binary image bytes"
        with pytest.raises(HTTPUnsupportedMediaType):
            await make_request(
                method="POST", headers=[(b"content-type", b"image/png")], body=blob
            )

    async def test_unknown_content_type_stays_raw_for_a_raw_application(self) -> None:
        blob = b"\x89PNG\r\n\x1a\n binary image bytes"
        request = await make_request(
            method="POST",
            headers=[(b"content-type", b"image/png")],
            body=blob,
            application=raw_body_app(),
        )
        assert request.data == blob
        assert request.handler_kwargs() == {"body_raw": blob}

    async def test_body_without_content_type_is_refused_while_decoding(self) -> None:
        with pytest.raises(HTTPUnsupportedMediaType):
            await make_request(method="POST", body=b"orphan payload")

    async def test_body_without_content_type_is_read_and_kept_raw(self) -> None:
        blob = b"orphan payload"
        request = await make_request(method="POST", body=blob, application=raw_body_app())
        assert request.data == blob
        assert request.handler_kwargs() == {"body_raw": blob}

    async def test_chunked_body_is_reassembled(self) -> None:
        request = await make_request(
            method="POST",
            headers=[(b"content-type", b"application/json")],
            chunks=[b'{"name":', b'"ada","age"', b":36}"],
        )
        assert request.data == {"name": "ada", "age": 36}

    async def test_multi_value_query_becomes_a_list_of_hydrated_values(self) -> None:
        request = await make_request(query=b"tag=100.50%3A%3AN&tag=200.75%3A%3AN")
        assert request.query == {"tag": [Decimal("100.50"), Decimal("200.75")]}

    async def test_urlencoded_typed_values_are_hydrated(self) -> None:
        request = await make_request(
            method="POST",
            headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            body=b"n=1::L&price=100.50::N&when=2025-01-15::D",
        )
        assert request.data == {"n": 1, "price": Decimal("100.50"), "when": date(2025, 1, 15)}

    async def test_empty_json_body_is_none_and_adds_no_kwargs(self) -> None:
        request = await make_request(
            method="POST",
            query=b"x=1",
            headers=[(b"content-type", b"application/json")],
            body=b"",
        )
        assert request.data is None
        assert request.handler_kwargs() == {"x": 1}


MULTIPART_BOUNDARY = "----kajenn"
MULTIPART_CONTENT_TYPE = f"multipart/form-data; boundary={MULTIPART_BOUNDARY}".encode()


def form_part(
    name: str,
    payload: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> bytes:
    """Render one multipart part: content-disposition, optional type, payload."""
    disposition = f'form-data; name="{name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    lines = [f"Content-Disposition: {disposition}".encode()]
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}".encode())
    return b"\r\n".join(lines) + b"\r\n\r\n" + payload


async def make_multipart_request(*parts: bytes) -> Request:
    """Build and init a POST request whose body is a multipart form of ``parts``."""
    marker = f"--{MULTIPART_BOUNDARY}".encode()
    body = b"".join(marker + b"\r\n" + part + b"\r\n" for part in parts) + marker + b"--\r\n"
    return await make_request(
        method="POST", headers=[(b"content-type", MULTIPART_CONTENT_TYPE)], body=body
    )


class TestMultipart:
    async def test_text_field_and_file_arrive_as_kwargs(self) -> None:
        request = await make_multipart_request(
            form_part("title", b"my doc"),
            form_part("doc", b"file bytes", filename="a.txt", content_type="text/plain"),
        )
        kwargs = request.handler_kwargs()
        assert kwargs["title"] == "my doc"
        uploaded = kwargs["doc"]
        assert isinstance(uploaded, UploadedFile)
        assert uploaded.name == "doc"
        assert uploaded.filename == "a.txt"
        assert uploaded.content_type == "text/plain"
        assert uploaded.data == b"file bytes"

    async def test_text_field_is_tytx_hydrated(self) -> None:
        request = await make_multipart_request(form_part("count", b"123"))
        assert request.handler_kwargs() == {"count": 123}

    async def test_two_files_under_different_names(self) -> None:
        request = await make_multipart_request(
            form_part("first", b"one", filename="one.txt", content_type="text/plain"),
            form_part("second", b"two", filename="two.txt", content_type="text/plain"),
        )
        kwargs = request.handler_kwargs()
        assert kwargs["first"].filename == "one.txt"
        assert kwargs["second"].filename == "two.txt"
        assert kwargs["first"].data == b"one"
        assert kwargs["second"].data == b"two"

    async def test_repeated_name_collects_a_list(self) -> None:
        request = await make_multipart_request(
            form_part("doc", b"one", filename="one.txt", content_type="text/plain"),
            form_part("doc", b"two", filename="two.txt", content_type="text/plain"),
        )
        docs = request.handler_kwargs()["doc"]
        assert [type(item) for item in docs] == [UploadedFile, UploadedFile]
        assert [item.filename for item in docs] == ["one.txt", "two.txt"]

    async def test_binary_payload_survives_intact(self) -> None:
        blob = b"\x89PNG\x00\xff\r\n\x1a\n tail"
        request = await make_multipart_request(
            form_part("doc", blob, filename="a.png", content_type="image/png")
        )
        assert request.handler_kwargs()["doc"].data == blob

    async def test_data_is_the_decoded_dict(self) -> None:
        request = await make_multipart_request(
            form_part("title", b"my doc"),
            form_part("doc", b"bytes", filename="a.bin", content_type="application/octet-stream"),
        )
        assert isinstance(request.data, dict)
        assert set(request.data) == {"title", "doc"}
        assert request.data["title"] == "my doc"

    async def test_each_file_keeps_its_own_content_type(self) -> None:
        request = await make_multipart_request(
            form_part("image", b"png", filename="a.png", content_type="image/png"),
            form_part("blob", b"raw", filename="a.bin", content_type="application/octet-stream"),
        )
        kwargs = request.handler_kwargs()
        assert kwargs["image"].content_type == "image/png"
        assert kwargs["blob"].content_type == "application/octet-stream"


class TestIdentityMetadata:
    async def test_request_id_generated_when_header_absent(self) -> None:
        request = await make_request()
        assert uuid.UUID(request.id)  # a valid uuid4, not empty

    async def test_external_id_from_header(self) -> None:
        request = await make_request(headers=[(b"x-external-id", b"corr-1")])
        assert request.external_id == "corr-1"

    async def test_tytx_mode_detected_from_header(self) -> None:
        request = await make_request(headers=[(b"x-tytx-transport", b"json")])
        assert request.tytx_mode is True
        assert request.tytx_transport == "json"

    async def test_no_tytx_header_means_plain_mode(self) -> None:
        request = await make_request()
        assert request.tytx_mode is False
        assert request.tytx_transport is None


class TestAuthSessionAccessors:
    async def test_avatar_and_tags_from_scope(self) -> None:
        avatar = Avatar("alice", ["admin", "staff"])
        request = await make_request(scope_extra={"auth": avatar})
        assert request.avatar() is avatar
        assert request.auth_tags == ["admin", "staff"]

    async def test_anonymous_when_no_avatar_on_scope(self) -> None:
        request = await make_request()
        assert request.avatar() is None
        assert request.auth_tags == []

    async def test_keyed_avatar_delegates_to_the_session(self) -> None:
        session = Session("tok", avatar=Avatar("alice"), ttl=3600)
        session.attach_avatar(Avatar("alice@erp"), "erp")
        request = await make_request(scope_extra={"session": session})
        assert request.avatar("erp").identity == "alice@erp"

    async def test_keyed_avatar_without_a_session_is_none(self) -> None:
        request = await make_request()
        assert request.avatar("erp") is None

    async def test_session_from_scope(self) -> None:
        session = object()
        request = await make_request(scope_extra={"session": session})
        assert request.session is session

    async def test_server_resolved_via_application(self) -> None:
        app = BaseApplication(mount="")
        server = BaseServer(applications=[app])
        request = await make_request(application=app)
        assert request.application is app
        assert request.server is server


class FakeDb:
    """A stand-in db handler recording how often it is closed."""

    def __init__(self) -> None:
        self.closed = 0

    def closeConnection(self) -> None:
        self.closed += 1


class DbApp(BaseApplication):
    """Primary app that touches ``request.db`` (twice) and records the handler."""

    def __init__(self, **kwargs: Any) -> None:
        self.db_name: str | None = kwargs.pop("db_name", None)
        self.seen: dict[str, Any] = kwargs.pop("seen")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive, application=self)
        await request.init()
        self.seen["db"] = request.db
        self.seen["db_again"] = request.db  # second access is cached
        await request.response(scope, receive, send)


class GetDbApp(BaseApplication):
    """Primary app that resolves a db via ``get_db`` (no cleanup registration)."""

    def __init__(self, **kwargs: Any) -> None:
        self.seen: dict[str, Any] = kwargs.pop("seen")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive, application=self)
        await request.init()
        self.seen["db"] = request.get_db("default")
        await request.response(scope, receive, send)


async def drive(server: BaseServer, path: str = "/") -> None:
    """Drive one http request through the full server dispatch."""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        pass

    await server(
        {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []},
        receive,
        send,
    )


class TestDbPreparationLayer:
    async def test_db_returns_default_handler_and_closes_at_request_end(self) -> None:
        seen: dict[str, Any] = {}
        fake = FakeDb()
        server = BaseServer(applications=[DbApp(mount="", seen=seen)])
        server.add_database("default", fake)

        await drive(server)

        assert seen["db"] is fake
        assert seen["db_again"] is fake  # cached, same object
        assert fake.closed == 1  # cleanup registered once, drained by the server

    async def test_db_resolves_named_handler_from_app_db_name(self) -> None:
        seen: dict[str, Any] = {}
        fake = FakeDb()
        server = BaseServer(applications=[DbApp(mount="", db_name="shop", seen=seen)])
        server.add_database("shop", fake)

        await drive(server)

        assert seen["db"] is fake
        assert fake.closed == 1

    async def test_db_is_none_when_code_absent(self) -> None:
        seen: dict[str, Any] = {}
        server = BaseServer(applications=[DbApp(mount="", seen=seen)])  # no database registered

        await drive(server)

        assert seen["db"] is None

    async def test_get_db_does_not_register_cleanup(self) -> None:
        seen: dict[str, Any] = {}
        fake = FakeDb()
        server = BaseServer(applications=[GetDbApp(mount="", seen=seen)])
        server.add_database("default", fake)

        await drive(server)

        assert seen["db"] is fake
        assert fake.closed == 0  # get_db never queues closeConnection

    async def test_get_db_is_none_when_code_absent(self) -> None:
        seen: dict[str, Any] = {}
        server = BaseServer(applications=[GetDbApp(mount="", seen=seen)])  # nothing registered

        await drive(server)

        assert seen["db"] is None
