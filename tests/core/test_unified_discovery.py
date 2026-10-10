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

"""One discovery, filtered by the same filters as execution, for both formats.

The OpenAPI schema (``_meta/schema_json``) and the MCP ``tools/list`` read the
application's discovery method with the request's scope: the identity is
resolved exactly as ``execute`` resolves it, and the tree is filtered by the
channel of the request and by the caller's tags. What a caller sees listed is
what the same caller can execute, on REST and on ``tools/call``; an invalid
credential on the schema request is the 401 execution answers. A bare
``McpEngine`` (a router, no application) lists by its channel and by the
``auth_tags`` it receives.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from genro_routes import RoutingClass, route

from kajenn import AsgiServer, BaseApplication, McpEngine, McpOpenApiApplication
from kajenn.types import Message, Scope
from kajenn.server_app import ServerApplication

ROUTES = {"public", "admin_only", "user_only"}

AUTH = {
    "bearer": {
        "boss": {"token": "tk_admin", "tags": "admin"},
        "clerk": {"token": "tk_user", "tags": "user"},
    }
}


class Api(McpOpenApiApplication):
    @route(channel_channels="mcp,rest")
    def public(self) -> dict[str, str]:
        return {"route": "public"}

    @route(channel_channels="mcp,rest", auth_rule="admin")
    def admin_only(self) -> dict[str, str]:
        return {"route": "admin_only"}

    @route(channel_channels="mcp,rest", auth_rule="user")
    def user_only(self) -> dict[str, str]:
        return {"route": "user_only"}


def api_server() -> AsgiServer:
    return AsgiServer(
        applications=[ServerApplication, (BaseApplication, {"mount": ""}), (Api, {"code": "api"})],
        auth=AUTH,
        plugins={"openapi": True},
    )


async def drive(
    server: AsgiServer,
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    token: str | None = None,
) -> tuple[int, Any]:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    if body:
        headers.append((b"content-type", b"application/json"))
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    data = json.loads(raw) if raw and start["status"] < 400 else None
    return start["status"], data


async def rpc(server: AsgiServer, method: str, params: dict, token: str | None) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    status, data = await drive(
        server, "/api/mcp", method="POST", body=json.dumps(payload).encode(), token=token
    )
    assert status == 200
    return data


async def schema_routes(server: AsgiServer, token: str | None) -> set[str]:
    status, schema = await drive(server, "/api/_meta/schema_json", token=token)
    assert status == 200
    names = {path.rstrip("/").rpartition("/")[2] for path in schema["paths"]}
    return names & ROUTES


async def tool_routes(server: AsgiServer, token: str | None) -> set[str]:
    data = await rpc(server, "tools/list", {}, token)
    return {tool["name"] for tool in data["result"]["tools"]} & ROUTES


async def rest_callable(server: AsgiServer, token: str | None) -> set[str]:
    callable_routes = set()
    for name in ROUTES:
        status, data = await drive(server, f"/api/{name}", token=token)
        if status == 200:
            assert data == {"route": name}
            callable_routes.add(name)
        else:
            assert status in (401, 403)
    return callable_routes


async def mcp_callable(server: AsgiServer, token: str | None) -> set[str]:
    callable_routes = set()
    for name in ROUTES:
        data = await rpc(server, "tools/call", {"name": name, "arguments": {}}, token)
        if "result" in data:
            assert data["result"]["structuredContent"] == {"route": name}
            callable_routes.add(name)
    return callable_routes


CALLERS = [
    pytest.param(None, {"public"}, id="anonymous"),
    pytest.param("tk_user", {"public", "user_only"}, id="user"),
    pytest.param("tk_admin", {"public", "admin_only"}, id="admin"),
]


class TestListedEqualsCallable:
    @pytest.mark.parametrize(("token", "expected"), CALLERS)
    async def test_openapi_schema_lists_what_the_caller_may_call(
        self, token: str | None, expected: set[str]
    ) -> None:
        assert await schema_routes(api_server(), token) == expected

    @pytest.mark.parametrize(("token", "expected"), CALLERS)
    async def test_mcp_tools_list_lists_what_the_caller_may_call(
        self, token: str | None, expected: set[str]
    ) -> None:
        assert await tool_routes(api_server(), token) == expected

    @pytest.mark.parametrize(("token", "expected"), CALLERS)
    async def test_listed_equals_executable_on_both_faces(
        self, token: str | None, expected: set[str]
    ) -> None:
        server = api_server()
        listed_rest = await schema_routes(server, token)
        listed_mcp = await tool_routes(server, token)
        assert listed_rest == await rest_callable(server, token) == expected
        assert listed_mcp == await mcp_callable(server, token) == expected


class TestInvalidCredential:
    async def test_schema_request_with_an_invalid_bearer_is_401(self) -> None:
        status, _ = await drive(api_server(), "/api/_meta/schema_json", token="nope")
        assert status == 401

    async def test_execution_with_an_invalid_bearer_is_401(self) -> None:
        status, _ = await drive(api_server(), "/api/public", token="nope")
        assert status == 401


class Tools(RoutingClass):
    def __init__(self) -> None:
        self.route.plug("pydantic")
        self.route.plug("channel")
        self.route.plug("auth")

    @route(channel_channels="mcp")
    def public(self) -> dict[str, str]:
        return {"route": "public"}

    @route(channel_channels="mcp", auth_rule="admin")
    def admin_only(self) -> dict[str, str]:
        return {"route": "admin_only"}

    @route(channel_channels="mcp", auth_rule="user")
    def user_only(self) -> dict[str, str]:
        return {"route": "user_only"}

    @route(channel_channels="rest")
    def rest_only(self) -> dict[str, str]:
        return {"route": "rest_only"}


class TestBareEngine:
    @pytest.mark.parametrize(
        ("auth_tags", "expected"),
        [
            pytest.param(None, {"public"}, id="anonymous"),
            pytest.param(["user"], {"public", "user_only"}, id="user"),
            pytest.param("admin", {"public", "admin_only"}, id="admin"),
        ],
    )
    async def test_tools_list_filters_by_channel_and_tags(
        self, auth_tags: Any, expected: set[str]
    ) -> None:
        engine = McpEngine(Tools().route)
        result = await engine.dispatch({"method": "tools/list"}, auth_tags)
        assert {tool["name"] for tool in result["tools"]} == expected
