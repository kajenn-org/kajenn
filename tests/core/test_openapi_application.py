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

"""RoutedApplication tests (Macro 4 Phase 6).

Requests drive a REAL ``AsgiServer`` composition at the ASGI level (no
uvicorn). The openapi plugin is armed through the server's ``plugins``
config. The GET-side helpers come from ``tests/conftest.py``;
``json_request`` (local) adds a JSON body.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from genro_routes import RoutingClass, route

from kajenn import AsgiServer, RoutedApplication
from kajenn.types import Message, Scope
from tests.conftest import start_server


class SubApi(RoutingClass):
    """A routing class that exists on its own; an application attaches it."""

    @route()
    def ping(self, name: str = "x") -> dict[str, str]:
        """Echo a name back."""
        return {"pong": name}

    @route()
    def make(self, payload: dict) -> dict[str, Any]:
        """Build something from a JSON body."""
        return {"made": payload}


class DirectApi(RoutedApplication):
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
    """A do-nothing primary so a RoutedApplication can mount as a secondary."""


class Holder(RoutedApplication):
    """Exposes ``SubApi`` by attaching it in its own constructor."""

    openapi_info = {"title": "Holder", "version": "3.0.0", "description": "holds"}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.add_branches({"name": "api", "instance": SubApi()})


class WithMcpRoute(RoutedApplication):
    """An application whose own route is named ``mcp``."""

    @route()
    def mcp(self) -> dict[str, bool]:
        return {"own": True}


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

        await start_server(server)
        await server(scope, receive, send)  # type: ignore[operator]
        return sent

    return _json_request


class TestDirectMode:
    async def test_schema_is_valid_openapi(
        self, http_request, response_status, response_body
    ) -> None:
        sent = await http_request(direct_server(), "/_meta/api/schema_json")
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
        sent = await http_request(direct_server(), "/_meta/api/schema_json")
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

        server = await start_server(direct_server())
        await server(scope, receive, send)
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"sum": 3}

    async def test_docs_served(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        sent = await http_request(direct_server(), "/_meta/api/docs")
        assert response_status(sent) == 200
        assert response_headers(sent)[b"content-type"] == b"text/html; charset=utf-8"
        body = response_body(sent).decode()
        assert "swagger-ui" in body
        assert '"/_meta/api/schema_json"' in body

    async def test_index_served(self, http_request, response_status, response_body) -> None:
        sent = await http_request(direct_server(), "/_meta/index")
        assert response_status(sent) == 200
        body = response_body(sent).decode()
        assert "Direct API" in body
        assert 'href="/_meta/api/docs"' in body


class TestDocsOff:
    async def test_docs_off_is_404(self, http_request, response_status) -> None:
        server = AsgiServer(
            applications=[(DirectApi, {"mount": "", "docs": "off"})], plugins={"openapi": True, "pydantic": True}
        )
        sent = await http_request(server, "/_meta/api/docs")
        assert response_status(sent) == 404


class TestRoutingClassInsideAnApplication:
    async def test_routing_class_kwarg_is_refused(self) -> None:
        with pytest.raises(TypeError, match="routing_class"):
            mounted_server((RoutedApplication, {"code": "mount", "routing_class": SubApi()}))

    async def test_an_application_attaches_its_routing_class(
        self, http_request, response_status, response_body
    ) -> None:
        server = mounted_server((Holder, {"code": "mount"}))
        sent = await http_request(server, "/mount/api/ping")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"pong": "x"}
        sent = await http_request(server, "/mount/_meta/api/schema_json")
        doc = json.loads(response_body(sent))
        assert doc["info"]["title"] == "Holder"
        assert doc["paths"]["/api/ping"]["get"]["operationId"] == "ping"


class TestReservedMeta:
    def test_a_route_named_meta_is_refused(self) -> None:
        class Clash(RoutedApplication):
            @route(name="_meta")
            def meta(self) -> dict[str, bool]:
                return {}

        with pytest.raises(ValueError, match="_meta"):
            Clash(code="clash")


class TestMovedUrls:
    @pytest.mark.parametrize(
        ("old", "new"),
        [("/_meta/schema_json", "/_meta/api/schema_json"), ("/_meta/docs", "/_meta/api/docs")],
    )
    async def test_an_old_url_answers_as_the_new_one_with_deprecation(
        self, old, new, http_request, response_status, response_headers, response_body
    ) -> None:
        server = direct_server()
        moved = await http_request(server, old)
        current = await http_request(server, new)
        assert response_status(moved) == 200
        assert response_body(moved) == response_body(current)
        headers = response_headers(moved)
        assert headers[b"deprecation"] == b"true"
        assert headers[b"link"] == f'<{new}>; rel="successor-version"'.encode()
        assert b"deprecation" not in response_headers(current)

    async def test_an_own_route_named_mcp_is_not_moved(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        server = mounted_server((WithMcpRoute, {"code": "w"}))
        sent = await http_request(server, "/w/mcp")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"own": True}
        assert b"deprecation" not in response_headers(sent)


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
