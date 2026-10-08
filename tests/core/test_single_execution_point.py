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

"""The single execution point (#37): REST, MCP, WSX and the bus share one path.

``RoutedApplication.execute(request)`` resolves the route, builds the call with
``make_callable`` and runs it on the loop or through the pool; ``dispatch``
writes the response around it. An MCP ``tools/call`` builds a ``Request`` and
goes through the same method, so a tool sees its ``_request`` and its
``route_cleanup`` runs after a sync call — what Sourcerer v2 needs to close
its db connection after every tool.
"""

from __future__ import annotations

import inspect
import json
from typing import Any


from genro_routes import RoutingClass, route

from kajenn import AsgiServer, BaseApplication, McpOpenApiApplication, RoutedApplication
from kajenn.request import Request
from kajenn.mcp.engine import McpEngine
from kajenn.types import Message, Scope


class Tools(McpOpenApiApplication):
    """A dual-face application whose handlers record what they see."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cleanups = 0
        self.seen: list[dict[str, Any]] = []

    @route(channel_channels="mcp,rest")
    def whoami(self, _request=None) -> dict[str, Any]:
        avatar = _request.avatar()
        seen = {
            "path": _request.path,
            "identity": avatar.identity if avatar is not None else None,
            "has_request": _request is not None,
        }
        self.seen.append(seen)
        return seen

    @route(channel_channels="mcp,rest")
    def add(self, x: int = 0, y: int = 0) -> dict[str, int]:
        return {"sum": x + y}

    @route(channel_channels="mcp,rest", auth_rule="admin")
    def secret(self) -> dict[str, str]:
        return {"secret": "s"}

    @route(channel_channels="mcp,rest")
    async def agreet(self, name: str = "") -> dict[str, str]:
        return {"hello": name}

    @route(channel_channels="mcp,rest", openapi_method="post")
    def save(self, name: str = "", body_data: dict | None = None) -> dict[str, Any]:
        return {"name": name, "body_data": body_data}

    def route_cleanup(self) -> None:
        self.cleanups += 1


def tools_server() -> AsgiServer:
    return AsgiServer(
        applications=[(BaseApplication, {"mount": ""}), (Tools, {"code": "tools"})],
        plugins={"openapi": True},
    )


def tools(server: AsgiServer) -> Tools:
    return server.applications["tools"]


async def drive(
    server: AsgiServer, path: str, *, method: str = "GET", body: bytes = b"", query: bytes = b""
) -> list[Message]:
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": [(b"content-type", b"application/json")] if body else [],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def body_of(sent: list[Message]) -> Any:
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(raw)


def status_of(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def tools_call(server: AsgiServer, name: str, arguments: dict | None = None) -> Any:
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    sent = await drive(server, "/tools/mcp", method="POST", body=json.dumps(envelope).encode())
    return body_of(sent)


class TestTheExecutionPoint:
    def test_execute_exists_and_is_a_coroutine_function(self) -> None:
        # wf:contract: RoutedApplication.execute(request) is the single execution point,
        # wf:contract: async, resolving the route and running the handler.

        assert inspect.iscoroutinefunction(RoutedApplication.execute)

    async def test_execute_runs_a_route_from_a_request(self) -> None:
        # wf:contract: execute resolves request.path on the app router and returns the
        # wf:contract: handler's result; a sync handler runs its route_cleanup.
        server = tools_server()
        app = tools(server)
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/add",
            "query_string": b"x=2&y=3",
            "headers": [],
        }

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, receive, server=server, application=app)
        await request.init()
        before = app.cleanups
        assert await app.execute(request) == {"sum": 5}
        assert app.cleanups == before + 1


class TestMcpGoesThroughIt:
    async def test_a_tool_sees_its_request(self) -> None:
        # wf:contract: a tool handler declaring _request receives the live Request of
        # wf:contract: the tool call, whose path is the tool's route path.
        server = tools_server()
        answer = await tools_call(server, "whoami")
        content = answer["result"]["structuredContent"]
        assert content["has_request"] is True
        assert content["path"].endswith("whoami")

    async def test_a_sync_tool_runs_its_route_cleanup(self) -> None:
        # wf:contract: tools/call on a sync handler runs the application's route_cleanup
        # wf:contract: exactly once, after the handler (the db-connection seam of #37).
        server = tools_server()
        before = tools(server).cleanups
        answer = await tools_call(server, "add", {"x": 1, "y": 1})
        assert answer["result"]["structuredContent"] == {"sum": 2}
        assert tools(server).cleanups == before + 1

    async def test_an_async_tool_still_answers(self) -> None:
        # wf:contract: an async handler runs on the loop through the execution point.
        server = tools_server()
        answer = await tools_call(server, "agreet", {"name": "bob"})
        assert answer["result"]["structuredContent"] == {"hello": "bob"}

    async def test_a_protected_tool_without_identity_is_not_authorized(self) -> None:
        # wf:contract: a ruled tool called anonymously answers JSON-RPC error -32000.
        server = tools_server()
        answer = await tools_call(server, "secret")
        assert answer["error"]["code"] == -32000

    async def test_an_unknown_tool_is_method_not_found(self) -> None:
        # wf:contract: a tool name resolving to nothing answers JSON-RPC error -32601.
        server = tools_server()
        answer = await tools_call(server, "nothing")
        assert answer["error"]["code"] == -32601

    async def test_bad_arguments_are_an_is_error_result(self) -> None:
        # wf:contract: arguments the handler cannot bind answer an isError result,
        # wf:contract: not a JSON-RPC error.
        server = tools_server()
        answer = await tools_call(server, "add", {"x": "nope"})
        assert answer["result"]["isError"] is True

    async def test_arguments_bind_as_handler_kwargs_even_with_body_data(self) -> None:
        # wf:contract: MCP arguments are the handler's kwargs: a handler declaring
        # wf:contract: body_data receives the argument of that name, never the whole
        # wf:contract: arguments dict (orchestra's save(name, body_data) over tools/call).
        server = tools_server()
        answer = await tools_call(server, "save", {"name": "cpu50", "body_data": {"cpu": 50}})
        assert answer["result"]["structuredContent"] == {
            "name": "cpu50",
            "body_data": {"cpu": 50},
        }

    async def test_rest_keeps_the_whole_body_as_body_data(self) -> None:
        # wf:contract: the REST face is unchanged: a JSON body reaches a handler that
        # wf:contract: declares body_data whole, under that name.
        server = tools_server()
        sent = await drive(
            server, "/tools/save", method="POST", body=json.dumps({"name": "x", "cpu": 1}).encode()
        )
        assert status_of(sent) == 200
        assert body_of(sent) == {"name": "", "body_data": {"name": "x", "cpu": 1}}

    async def test_rest_and_mcp_share_the_cleanup_count(self) -> None:
        # wf:contract: the REST face and the MCP face increment the same route_cleanup
        # wf:contract: counter: one execution path, two faces.
        server = tools_server()
        before = tools(server).cleanups
        rest = await drive(server, "/tools/add", query=b"x=1&y=2")
        assert status_of(rest) == 200
        await tools_call(server, "add", {"x": 1, "y": 2})
        assert tools(server).cleanups == before + 2


class TestTheEngineContract:
    def test_invoke_receives_the_tool_path(self) -> None:
        # wf:contract: McpEngine.invoke is called as invoke(path, arguments, auth_tags);
        # wf:contract: the default invoke resolves the path on the engine's router.


        params = list(inspect.signature(McpEngine._default_invoke).parameters)
        assert params == ["self", "path", "arguments", "auth_tags"]

    async def test_the_default_invoke_resolves_on_the_router(self) -> None:
        # wf:contract: an engine without an application still answers tools/call by
        # wf:contract: resolving the path on its router with the engine's channel.


        class Calc(RoutingClass):
            @route()
            def add(self, x: int = 0, y: int = 0) -> dict[str, int]:
                return {"sum": int(x) + int(y)}

        engine = McpEngine(Calc().route)
        result = await engine.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "add", "arguments": {"x": 2, "y": 2}},
            }
        )
        assert result["structuredContent"] == {"sum": 4}

