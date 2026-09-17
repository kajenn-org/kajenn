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

"""MCP application tests (Macro 4 Phase 8).

Requests drive a REAL ``AsgiServer`` composition at the ASGI level (no uvicorn),
so the middleware chain (errors) turns the transport gates (405/400/403) into
responses. ``McpApplication`` is mounted at ``/mcp`` over an empty primary;
``McpOpenApiApplication`` runs in direct mode as the primary. The GET-side
helpers come from ``tests/conftest.py``; ``drive``/``mcp_post`` (local) add a
body and custom headers.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

import pytest
from genro_routes import RoutingClass, route

from kajenn import AsgiServer, McpApplication, McpOpenApiApplication, RoutedApplication
from kajenn.types import Message, Scope


class Empty(RoutedApplication):
    """A do-nothing primary so an MCP app can mount as a secondary."""


class Calc(RoutingClass):
    """External tool surface: pydantic plugged, one sync and one async tool."""

    def __init__(self) -> None:
        self.route.plug("pydantic")
        self.threads: list[int] = []

    @route()
    def add(self, x: int, y: int = 0) -> dict:
        """Add two numbers."""
        self.threads.append(threading.get_ident())
        return {"sum": x + y}

    @route()
    async def greet(self, name: str) -> str:
        """Greet someone."""
        return f"hi {name}"


@pytest.fixture
def drive() -> Callable[..., object]:
    """Fixture: drive one request through a server at the ASGI level."""

    async def _drive(
        server: object,
        path: str,
        *,
        method: str = "GET",
        query: bytes = b"",
        headers: list[tuple[bytes, bytes]] | None = None,
        body: bytes = b"",
    ) -> list[Message]:
        scope: Scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": list(headers or []),
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)  # type: ignore[operator]
        return sent

    return _drive


@pytest.fixture
def mcp_post(drive: Callable[..., Any]) -> Callable[..., object]:
    """Fixture: POST a JSON-RPC envelope (application/json) to an MCP endpoint."""

    async def _mcp_post(
        server: object,
        path: str,
        envelope: dict,
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> list[Message]:
        hdrs = [(b"content-type", b"application/json"), *(headers or [])]
        return await drive(server, path, method="POST", headers=hdrs, body=json.dumps(envelope).encode())

    return _mcp_post


def result_of(response_body: Callable[[list[Message]], bytes], sent: list[Message]) -> dict:
    """Parse the JSON-RPC envelope carried by the response body."""
    return json.loads(response_body(sent))


def mcp_server(entry: tuple[type, dict]) -> AsgiServer:
    """A server with *entry* mounted at its ``mount`` over an empty root app."""
    return AsgiServer(applications=[(Empty, {"mount": ""}), entry])


class TestMcpApplication:
    async def test_initialize_negotiates_version(
        self, mcp_post, response_status, response_body
    ) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app), "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        assert response_status(sent) == 200
        envelope = result_of(response_body, sent)
        assert envelope["id"] == 1
        assert envelope["result"]["protocolVersion"] == "2025-11-25"

    async def test_mcp_name_names_the_engine_the_client_sees(
        self, mcp_post, response_body
    ) -> None:
        app = (McpApplication, {"code": "mcp", "mcp_name": "demo", "routing_class": Calc()})
        server = mcp_server(app)
        assert server.applications["mcp"].mcp_engine.name == "demo"
        sent = await mcp_post(
            server, "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        server_info = result_of(response_body, sent)["result"]["serverInfo"]
        assert server_info["name"] == "demo"

    async def test_tools_list_enumerates_external_router(
        self, mcp_post, response_status, response_body
    ) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(mcp_server(app), "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert response_status(sent) == 200
        tools = result_of(response_body, sent)["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"add", "greet"}

    async def test_tools_call_sync_tool(self, mcp_post, response_body) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "add", "arguments": {"x": 2, "y": 3}}},
        )
        result = result_of(response_body, sent)["result"]
        assert result["structuredContent"] == {"sum": 5}

    async def test_tools_call_async_tool(self, mcp_post, response_body) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "greet", "arguments": {"name": "bob"}}},
        )
        result = result_of(response_body, sent)["result"]
        assert result["content"] == [{"type": "text", "text": "hi bob"}]

    async def test_sync_tool_runs_off_the_loop_thread(self, mcp_post) -> None:
        calc = Calc()
        app = (McpApplication, {"code": "mcp", "routing_class": calc})
        await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "add", "arguments": {"x": 1}}},
        )
        assert calc.threads and calc.threads[0] != threading.get_ident()

    async def test_notification_answers_202_empty(
        self, mcp_post, response_status, response_body
    ) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(mcp_server(app), "/mcp", {"jsonrpc": "2.0", "method": "initialize"})
        assert response_status(sent) == 202
        assert response_body(sent) == b""

    async def test_other_method_is_method_not_allowed(self, drive, response_status) -> None:
        # GET is the push stream since core 1e (test_mcp_push.py); DELETE keeps
        # the 405 gate covered.
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await drive(mcp_server(app), "/mcp", method="DELETE")
        assert response_status(sent) == 405

    async def test_unsupported_protocol_version_is_400(
        self, mcp_post, response_status
    ) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
            headers=[(b"mcp-protocol-version", b"1999-01-01")],
        )
        assert response_status(sent) == 400

    async def test_supported_protocol_version_passes(
        self, mcp_post, response_status
    ) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
            headers=[(b"mcp-protocol-version", b"2025-11-25")],
        )
        assert response_status(sent) == 200

    async def test_disallowed_origin_is_403(self, mcp_post, response_status) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc(), "allowed_origins": ["https://ok.example"]})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 8, "method": "tools/list"},
            headers=[(b"origin", b"https://evil.example")],
        )
        assert response_status(sent) == 403

    async def test_allowed_origin_passes(self, mcp_post, response_status) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc(), "allowed_origins": ["https://ok.example"]})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
            headers=[(b"origin", b"https://ok.example")],
        )
        assert response_status(sent) == 200

    async def test_unknown_method_is_a_jsonrpc_error(self, mcp_post, response_status, response_body) -> None:
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app), "/mcp", {"jsonrpc": "2.0", "id": 10, "method": "resources/list"}
        )
        # A protocol error rides a JSON-RPC error envelope with HTTP 200.
        assert response_status(sent) == 200
        envelope = result_of(response_body, sent)
        assert envelope["error"]["code"] == -32601
        assert envelope["id"] == 10

    async def test_without_router_lists_no_tools(self, mcp_post, response_body) -> None:
        app = (McpApplication, {"code": "mcp"})
        sent = await mcp_post(mcp_server(app), "/mcp", {"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
        assert result_of(response_body, sent)["result"] == {"tools": []}

    async def test_bad_arguments_are_a_tool_error(self, mcp_post, response_status, response_body) -> None:
        # A sync tool with an invalid annotated argument: the error travels back
        # through the run_sync future and lands as an isError result (SEP-1303),
        # never a JSON-RPC protocol error.
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await mcp_post(
            mcp_server(app),
            "/mcp",
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "add", "arguments": {"x": "nope"}}},
        )
        assert response_status(sent) == 200
        result = result_of(response_body, sent)["result"]
        assert result["isError"] is True

    async def test_non_object_body_is_invalid_request(self, drive, response_status, response_body) -> None:
        # A JSON body that is not an object is rejected by the engine with -32600
        # and rendered as a JSON-RPC error envelope (HTTP 200, id null).
        app = (McpApplication, {"code": "mcp", "routing_class": Calc()})
        sent = await drive(
            mcp_server(app),
            "/mcp",
            method="POST",
            headers=[(b"content-type", b"application/json")],
            body=b'"not-an-object"',
        )
        assert response_status(sent) == 200
        envelope = result_of(response_body, sent)
        assert envelope["error"]["code"] == -32600
        assert envelope["id"] is None


class BridgeApi(McpOpenApiApplication):
    """Direct-mode bridge: a dual method (REST + MCP) and a REST-only method."""

    openapi_info = {"title": "Bridge", "version": "1.0.0", "description": "dual-face"}

    @route(channel_channels="mcp,rest")
    def echo(self, msg: str = "hi") -> dict:
        """Echo a message."""
        return {"echo": msg}

    @route(channel_channels="rest")
    def only_rest(self) -> dict:
        """REST-only endpoint, never an MCP tool."""
        return {"rest": True}


def bridge_server() -> AsgiServer:
    """A server whose plugin config arms openapi on the bridge app."""
    return AsgiServer(applications=[(BridgeApi, {"mount": ""})], plugins={"openapi": True})


class TestMcpOpenApiApplication:
    async def test_dual_method_same_result_on_both_faces(
        self, drive, mcp_post, response_body
    ) -> None:
        server = bridge_server()
        rest = await drive(server, "/echo", method="GET", query=b"msg=hey")
        assert json.loads(response_body(rest)) == {"echo": "hey"}

        mcp = await mcp_post(
            server,
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo", "arguments": {"msg": "hey"}}},
        )
        result = result_of(response_body, mcp)["result"]
        assert result["structuredContent"] == {"echo": "hey"}

    async def test_rest_only_absent_from_tools_list(self, mcp_post, response_body) -> None:
        server = bridge_server()
        sent = await mcp_post(server, "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in result_of(response_body, sent)["result"]["tools"]}
        assert "echo" in names
        assert "only_rest" not in names

    async def test_openapi_schema_still_serves(
        self, http_request, response_status, response_body
    ) -> None:
        sent = await http_request(bridge_server(), "/_meta/schema_json")
        assert response_status(sent) == 200
        doc = json.loads(response_body(sent))
        assert doc["openapi"] == "3.1.0"
        assert doc["info"]["title"] == "Bridge"
        assert "/echo" in doc["paths"]

    async def test_mcp_other_method_is_method_not_allowed(self, drive, response_status) -> None:
        # GET on the mcp segment is the push stream since core 1e (test_mcp_push.py)
        sent = await drive(bridge_server(), "/mcp", method="DELETE")
        assert response_status(sent) == 405


class TestMcpOpenApiMountedMode:
    async def test_mounted_router_tools_and_rest(self, drive, mcp_post, response_body) -> None:
        class SubApi(RoutingClass):
            """External API mounted into the bridge under ``api_name``."""

            openapi_info = {"title": "Sub", "version": "2.0.0"}

            def __init__(self) -> None:
                self.route.plug("channel")
                self.route.channel.configure(channels="rest")

            @route(channel_channels="mcp,rest")
            def ping(self, name: str = "x") -> dict:
                """Ping."""
                return {"pong": name}

        app = (McpOpenApiApplication, {"code": "mount", "routing_class": SubApi()})
        server = AsgiServer(
            applications=[(Empty, {"mount": ""}), app], plugins={"openapi": True}
        )

        rest = await drive(server, "/mount/api/ping", method="GET", query=b"name=z")
        assert json.loads(response_body(rest)) == {"pong": "z"}

        mcp = await mcp_post(
            server,
            "/mount/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ping", "arguments": {"name": "z"}}},
        )
        assert result_of(response_body, mcp)["result"]["structuredContent"] == {"pong": "z"}


class TestExternalRouterAuthEnforcement:
    """``build_engine`` plugs auth: a ruled tool on an external router is enforced."""

    def _app(self) -> tuple[type, dict]:
        class Ruled(RoutingClass):
            @route()
            def open_tool(self) -> dict:
                """Unruled tool."""
                return {"ok": True}

            @route(auth_rule="admin")
            def secret(self) -> dict:
                """Admin-only tool."""
                return {"secret": True}

        return (McpApplication, {"code": "mcp", "routing_class": Ruled()})

    async def test_ruled_tool_hidden_from_anonymous_tools_list(
        self, mcp_post, response_body
    ) -> None:
        sent = await mcp_post(
            mcp_server(self._app()),
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        tools = {t["name"] for t in result_of(response_body, sent)["result"]["tools"]}
        assert "open_tool" in tools
        assert "secret" not in tools

    async def test_ruled_tool_call_denied_for_anonymous(
        self, mcp_post, response_body
    ) -> None:
        sent = await mcp_post(
            mcp_server(self._app()),
            "/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "secret", "arguments": {}}},
        )
        assert result_of(response_body, sent)["error"]["code"] == -32000
