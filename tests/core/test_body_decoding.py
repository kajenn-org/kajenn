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

"""Body decoding and error codes (issue #87).

Two options of the application, written in its own grammar
(``request(body=..., error_codes=...)``) and read back through
``self.config``: the body arrives decoded (default) or as the bytes the client
sent, and a value rejected by validation answers 400 (the strict reading,
default) or 422 (the FastAPI convention).

Every request drives a REAL ``AsgiServer`` composition at the ASGI level: a
body the core cannot decode in the declared format answers 400 with the real
reason, a content-type it has no decoder for answers 415, and neither escapes
as a 500.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from genro_routes import route

from kajenn import AsgiConfigBuilder, AsgiServer, OpenApiApplication, RoutedApplication
from kajenn.types import Message, Scope

MSGPACK_BROKEN = b"\xc1\xff\xff"


class BodyApp(RoutedApplication):
    """The application under test: typed fields, a whole body, a raw body."""

    code = "body"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.plug("pydantic")

    @route()
    def add(self, x: int = 0, y: int = 0) -> dict[str, int]:
        """Add two integers."""
        return {"sum": x + y}

    @route()
    def keep(self, body_data: Any = None) -> dict[str, Any]:
        """Keep the decoded body whole."""
        return {"kept": repr(body_data)}

    @route()
    def blob(self, body_raw: Any = None) -> dict[str, int]:
        """Count the bytes of an undecoded body."""
        return {"bytes": len(body_raw or b"")}

    @route()
    def upload(self, title: Any = None) -> dict[str, Any]:
        """Read one field of a form body."""
        return {"title": title}


class ApiApp(OpenApiApplication):
    """An OpenAPI application whose schema declares the code it answers."""

    code = "api"
    openapi_info = {"title": "Body API", "version": "1.0.0"}

    @route()
    def add(self, x: int = 0, y: int = 0) -> dict[str, int]:
        """Add two integers."""
        return {"sum": x + y}


def plain_server() -> AsgiServer:
    """A server with the application's options left at their defaults."""
    return AsgiServer(applications=[(BodyApp, {"mount": ""})])


def configured_server(**options: Any) -> AsgiServer:
    """A server whose recipe writes ``request(...)`` under the application."""

    class BodyConfig(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=8000)
            app = cfg.applications(default="body").application(
                code="body", mount="", app_class=BodyApp
            )
            app.request(**options)

    return AsgiServer(config=BodyConfig)


def api_server(**options: Any) -> AsgiServer:
    """A server hosting the OpenAPI application with the given request options."""

    class ApiConfig(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=8000)
            plugins = cfg.plugins()
            plugins.plugin(code="openapi")
            plugins.plugin(code="pydantic")
            app = cfg.applications(default="api").application(
                code="api", mount="", app_class=ApiApp
            )
            if options:
                app.request(**options)

    return AsgiServer(config=ApiConfig)


@pytest.fixture
def body_request() -> Callable[..., object]:
    """Fixture: drive one request carrying a body and a content-type."""

    async def _body_request(
        server: object,
        path: str,
        body: bytes,
        content_type: bytes | None = b"application/json",
        method: str = "POST",
        query: bytes = b"",
    ) -> list[Message]:
        headers = [(b"content-type", content_type)] if content_type is not None else []
        scope: Scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": headers,
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)  # type: ignore[operator]
        return sent

    return _body_request


class TestUndecodableBody:
    """A body that is not what its content-type declares answers 400."""

    async def test_broken_json_is_400(self, body_request, response_status, response_body) -> None:
        sent = await body_request(plain_server(), "/add", b'{"x": 1, ')
        assert response_status(sent) == 400
        assert b"json" in response_body(sent).lower()

    async def test_broken_xml_is_400(self, body_request, response_status) -> None:
        sent = await body_request(
            plain_server(), "/add", b"<add><x>1</x>", content_type=b"application/xml"
        )
        assert response_status(sent) == 400

    async def test_broken_msgpack_is_400(self, body_request, response_status) -> None:
        sent = await body_request(
            plain_server(), "/add", MSGPACK_BROKEN, content_type=b"application/msgpack"
        )
        assert response_status(sent) == 400

    async def test_broken_multipart_is_400(self, body_request, response_status) -> None:
        sent = await body_request(
            plain_server(),
            "/upload",
            b"--nope\r\nsomething that is not a part\r\n",
            content_type=b"multipart/form-data; boundary=abc",
        )
        assert response_status(sent) == 400

    async def test_a_json_list_where_fields_are_needed_is_400(
        self, body_request, response_status
    ) -> None:
        sent = await body_request(plain_server(), "/add", b"[1, 2]")
        assert response_status(sent) == 400


class TestUnsupportedContentType:
    """A content-type the core has no decoder for answers 415, never a 500."""

    async def test_text_body_is_415(self, body_request, response_status) -> None:
        sent = await body_request(
            plain_server(), "/keep", b"just words", content_type=b"text/plain"
        )
        assert response_status(sent) == 415

    async def test_body_without_content_type_is_415(self, body_request, response_status) -> None:
        sent = await body_request(plain_server(), "/keep", b"\x00\x01\x02", content_type=None)
        assert response_status(sent) == 415

    async def test_an_empty_body_is_not_refused(self, body_request, response_status) -> None:
        sent = await body_request(plain_server(), "/keep", b"", content_type=b"text/plain")
        assert response_status(sent) == 200


class TestStrictCodes:
    """The default reading: every failure of the core is a 400, never a 422."""

    async def test_rejected_value_is_400(self, body_request, response_status) -> None:
        sent = await body_request(plain_server(), "/add", b'{"x": "abc", "y": 2}')
        assert response_status(sent) == 400

    async def test_kwargs_the_signature_refuses_are_400(
        self, body_request, response_status
    ) -> None:
        sent = await body_request(plain_server(), "/add", b'{"x": 1, "y": 2}', query=b"z=99")
        assert response_status(sent) == 400

    async def test_rejected_value_is_400_when_the_recipe_says_strict(
        self, body_request, response_status
    ) -> None:
        server = configured_server(error_codes="strict")
        sent = await body_request(server, "/add", b'{"x": "abc", "y": 2}')
        assert response_status(sent) == 400


class TestFastApiCodes:
    """The declared convention: a rejected value answers 422, the rest stays 400."""

    async def test_rejected_value_is_422(self, body_request, response_status) -> None:
        server = configured_server(error_codes="fastapi")
        sent = await body_request(server, "/add", b'{"x": "abc", "y": 2}')
        assert response_status(sent) == 422

    async def test_broken_json_is_still_400(self, body_request, response_status) -> None:
        server = configured_server(error_codes="fastapi")
        sent = await body_request(server, "/add", b'{"x": 1, ')
        assert response_status(sent) == 400

    async def test_kwargs_the_signature_refuses_are_still_400(
        self, body_request, response_status
    ) -> None:
        server = configured_server(error_codes="fastapi")
        sent = await body_request(server, "/add", b'{"x": 1, "y": 2}', query=b"z=99")
        assert response_status(sent) == 400

    async def test_unsupported_content_type_is_still_415(
        self, body_request, response_status
    ) -> None:
        server = configured_server(error_codes="fastapi")
        sent = await body_request(server, "/keep", b"just words", content_type=b"text/plain")
        assert response_status(sent) == 415


class TestRawBody:
    """An application declaring a raw body receives the bytes untouched."""

    async def test_broken_json_reaches_the_handler_as_bytes(
        self, body_request, response_status, response_body
    ) -> None:
        server = configured_server(body="raw")
        sent = await body_request(server, "/blob", b'{"x": 1, ')
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"bytes": 9}

    async def test_a_content_type_without_a_decoder_is_served(
        self, body_request, response_status, response_body
    ) -> None:
        server = configured_server(body="raw")
        sent = await body_request(server, "/blob", b"just words", content_type=b"text/plain")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"bytes": 10}

    async def test_a_form_body_is_not_split_into_fields(
        self, body_request, response_status, response_body
    ) -> None:
        server = configured_server(body="raw")
        sent = await body_request(
            server,
            "/blob",
            b"title=hello",
            content_type=b"application/x-www-form-urlencoded",
        )
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"bytes": 11}

    async def test_the_decoded_default_splits_the_same_form(
        self, body_request, response_status, response_body
    ) -> None:
        sent = await body_request(
            plain_server(),
            "/upload",
            b"title=hello",
            content_type=b"application/x-www-form-urlencoded",
        )
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"title": "hello"}

    async def test_the_decode_helpers_stay_public(self) -> None:
        """A raw application decodes by itself with the core's own helpers."""
        from kajenn.request import Request

        request = Request({"type": "http", "method": "POST", "path": "/"}, None)
        assert request.decode_body(b'{"x": 1}', "application/json") == {"x": 1}
        assert request.get_transport("application/vnd.tytx+msgpack") == "msgpack"


class TestSchemaDeclaresTheCode:
    """The generated OpenAPI document declares the code the application chose."""

    async def test_strict_schema_declares_400(self, http_request, response_body) -> None:
        sent = await http_request(api_server(), "/_meta/schema_json")
        document = json.loads(response_body(sent))
        assert "400" in document["paths"]["/add"]["get"]["responses"]
        assert "422" not in document["paths"]["/add"]["get"]["responses"]

    async def test_fastapi_schema_declares_422(self, http_request, response_body) -> None:
        sent = await http_request(api_server(error_codes="fastapi"), "/_meta/schema_json")
        document = json.loads(response_body(sent))
        assert "422" in document["paths"]["/add"]["get"]["responses"]
        assert "400" not in document["paths"]["/add"]["get"]["responses"]
