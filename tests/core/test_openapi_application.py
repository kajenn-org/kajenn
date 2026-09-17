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

"""OpenApiApplication tests (Macro 4 Phase 6).

Requests drive a REAL ``AsgiServer`` composition at the ASGI level (no
uvicorn). The pydantic + openapi plugins are armed through the server's
``plugins`` config (direct mode) or plugged on the mounted class by the app
(mounted mode). The GET-side helpers come from ``tests/conftest.py``;
``json_request`` (local) adds a JSON body.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from genro_routes import RoutingClass, route

from kajenn import AsgiServer, OpenApiApplication, RoutedApplication
from kajenn.types import Message, Scope


class SubApi(RoutingClass):
    """External API mounted into an OpenApiApplication under ``api_name``."""

    openapi_info = {"title": "Sub API", "version": "3.0.0", "description": "mounted"}

    @route()
    def ping(self, name: str = "x") -> dict[str, str]:
        """Echo a name back."""
        return {"pong": name}

    @route()
    def make(self, payload: dict) -> dict[str, Any]:
        """Build something from a JSON body."""
        return {"made": payload}


class DirectApi(OpenApiApplication):
    """Direct mode: typed @route methods on the app itself."""

    openapi_info = {"title": "Direct API", "version": "2.0.0", "description": "direct"}

    @route()
    def add(self, x: int = 0, y: int = 0) -> dict[str, int]:
        """Add two integers."""
        return {"sum": x + y}

    @route()
    def store(self, body_data: dict | None = None) -> dict[str, Any]:
        """Keep the whole JSON body."""
        return {"stored": body_data}


class Empty(RoutedApplication):
    """A do-nothing primary so an OpenApiApplication can mount as a secondary."""


def direct_server() -> AsgiServer:
    """A server whose plugin config arms pydantic + openapi on routed apps."""
    return AsgiServer(
        applications=[(DirectApi, {"mount": ""})], plugins={"openapi": True, "pydantic": True}
    )


def mounted_server(entry: tuple[type, dict]) -> AsgiServer:
    """A server with *entry* mounted at its ``mount`` over an empty root app."""
    return AsgiServer(
        applications=[(Empty, {"mount": ""}), entry],
        plugins={"openapi": True, "pydantic": True},
    )


@pytest.fixture
def json_request() -> Callable[..., object]:
    """Fixture: drive one JSON-body request through a server at the ASGI level."""

    async def _json_request(
        server: object, path: str, body: bytes, method: str = "POST"
    ) -> list[Message]:
        scope: Scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)  # type: ignore[operator]
        return sent

    return _json_request


class TestDirectMode:
    async def test_schema_is_valid_openapi(
        self, http_request, response_status, response_body
    ) -> None:
        sent = await http_request(direct_server(), "/_meta/schema_json")
        assert response_status(sent) == 200
        doc = json.loads(response_body(sent))
        assert doc["openapi"] == "3.1.0"
        assert doc["info"] == {
            "title": "Direct API",
            "version": "2.0.0",
            "description": "direct",
        }
        assert "/add" in doc["paths"]

    async def test_operation_and_input_schema_present(self, http_request, response_body) -> None:
        sent = await http_request(direct_server(), "/_meta/schema_json")
        doc = json.loads(response_body(sent))
        operation = doc["paths"]["/add"]["get"]
        assert operation["operationId"] == "add"
        param_names = {p["name"] for p in operation["parameters"]}
        assert {"x", "y"} <= param_names

    async def test_endpoint_is_served(self, http_request, response_status, response_body) -> None:
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/add",
            "query_string": b"x=1&y=2",
            "headers": [],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        await direct_server()(scope, receive, send)
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"sum": 3}

    async def test_docs_served(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        sent = await http_request(direct_server(), "/_meta/docs")
        assert response_status(sent) == 200
        assert response_headers(sent)[b"content-type"] == b"text/html; charset=utf-8"
        body = response_body(sent).decode()
        assert "swagger-ui" in body
        assert '"/_meta/schema_json"' in body

    async def test_index_served(self, http_request, response_status, response_body) -> None:
        sent = await http_request(direct_server(), "/_meta/index")
        assert response_status(sent) == 200
        body = response_body(sent).decode()
        assert "Direct API" in body
        assert 'href="/_meta/docs"' in body


class TestDocsOff:
    async def test_docs_off_is_404(self, http_request, response_status) -> None:
        server = AsgiServer(
            applications=[(DirectApi, {"mount": "", "docs": "off"})], plugins={"openapi": True, "pydantic": True}
        )
        sent = await http_request(server, "/_meta/docs")
        assert response_status(sent) == 404


class TestMountedMode:
    async def test_endpoint_under_mount_and_api(
        self, http_request, response_status, response_body
    ) -> None:
        app = (OpenApiApplication, {"code": "mount", "routing_class": SubApi()})
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/mount/api/ping",
            "query_string": b"name=z",
            "headers": [],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        await mounted_server(app)(scope, receive, send)
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"pong": "z"}

    async def test_schema_covers_mounted_endpoints(
        self, http_request, response_status, response_body
    ) -> None:
        app = (OpenApiApplication, {"code": "mount", "routing_class": SubApi()})
        sent = await http_request(mounted_server(app), "/mount/_meta/schema_json")
        assert response_status(sent) == 200
        doc = json.loads(response_body(sent))
        # The mounted class's own openapi_info wins over the app default.
        assert doc["info"]["title"] == "Sub API"
        assert "/api/ping" in doc["paths"]
        assert doc["paths"]["/api/ping"]["get"]["operationId"] == "ping"

    async def test_module_import_mode(self) -> None:
        app = mounted_server(
            (OpenApiApplication, {"code": "m", "module": f"{SubApi.__module__}:SubApi"})
        ).applications["m"]
        assert app.api_name == "api"
        node = app.route.node("/api/ping")
        assert node.error is None, node.error


class TestBodyBinding:
    async def test_json_body_spread_over_scalar_params(
        self, json_request, response_status, response_body
    ) -> None:
        sent = await json_request(direct_server(), "/add", b'{"x": 1, "y": 2, "extra": 9}')
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"sum": 3}

    async def test_body_kept_whole_when_declared(self, json_request, response_body) -> None:
        sent = await json_request(direct_server(), "/store", b'{"x": 1}')
        assert json.loads(response_body(sent)) == {"stored": {"x": 1}}
