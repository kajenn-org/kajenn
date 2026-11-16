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

"""The channel travels in the scope.

Every face writes ``scope["kajenn.channel"]`` — ``api`` for an HTTP request,
``mcp`` for a tool call, ``wsx`` for a websocket message, whatever the frame
said for the bus — and ``RoutedApplication.auth_filters`` resolves the route on
it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


from genro_routes import route

from kajenn import AsgiServer, BaseApplication, RoutedApplication
from kajenn.types import Message, Scope
from kajenn.wsx import WsxConnection, WsxEnvelope
from tests.conftest import start_server


class Faces(RoutedApplication):
    @route(channel_channels="mcp,api")
    def channel(self, _request=None) -> dict[str, Any]:
        return {"channel": _request.scope.get("kajenn.channel")}


class Plain(RoutedApplication):
    @route(channel_channels="api,telegram")
    def channel(self, _request=None) -> dict[str, Any]:
        return {"channel": _request.scope.get("kajenn.channel")}


def faces_server() -> AsgiServer:
    return AsgiServer(
        applications=[
            (BaseApplication, {"mount": ""}),
            (Faces, {"code": "faces"}),
            (Plain, {"code": "plain"}),
        ],
        plugins={"openapi": True},
    )


async def drive(server: AsgiServer, path: str, *, method: str = "GET", body: bytes = b"") -> Any:
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")] if body else [],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await start_server(server)
    await server(scope, receive, send)
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(raw)


class TestTheScopeKey:
    async def test_a_rest_request_is_on_the_http_channel(self) -> None:
        # wf:contract: dispatch sets scope["kajenn.channel"] to the application's
        # wf:contract: http_channel ("api") when the scope carries none.
        assert await drive(faces_server(), "/faces/channel") == {"channel": "api"}
        assert await drive(faces_server(), "/plain/channel") == {"channel": "api"}

    async def test_a_tool_call_is_on_the_mcp_channel(self) -> None:
        # wf:contract: the scope built for a tools/call carries kajenn.channel = "mcp".
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "channel", "arguments": {}},
        }
        answer = await drive(
            faces_server(), "/faces/_meta/mcp", method="POST", body=json.dumps(envelope).encode()
        )
        assert answer["result"]["structuredContent"] == {"channel": "mcp"}

    async def test_a_bus_call_keeps_the_channel_of_the_frame(self) -> None:
        # wf:contract: serve_kbus_message writes the message's channel; without one the
        # wf:contract: application's http_channel applies.
        server = faces_server()
        await start_server(server)
        assert await server.kbus_call("/plain/channel", {}, channel="telegram") == {
            "channel": "telegram"
        }
        await start_server(server)
        assert await server.kbus_call("/plain/channel", {}) == {"channel": "api"}

    async def test_a_wsx_message_is_on_the_wsx_channel(self) -> None:
        # wf:contract: WsxConnection._request_scope sets kajenn.channel = "wsx" on the
        # wf:contract: synthetic scope of every message, and the handshake scope carries
        # wf:contract: the same key before authentication.

        seen: dict[str, Any] = {}

        class Watching(AsgiServer):
            async def authenticate(self, scope: Scope) -> None:
                seen["handshake"] = scope.get("kajenn.channel")
                return None

        server = Watching(applications=[(BaseApplication, {"mount": ""}), (Plain, {"code": "plain"})])
        envelope = WsxEnvelope(method="WSK", path="/plain/channel").encode()
        incoming: list[Message] = [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": envelope},
            {"type": "websocket.disconnect", "code": 1000},
        ]
        sent: list[Message] = []

        async def receive() -> Message:
            message = incoming.pop(0)
            if message["type"] == "websocket.disconnect":
                for _ in range(20):
                    await asyncio.sleep(0)
            return message

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {
            "type": "websocket",
            "path": "/plain/_wsx",
            "headers": [(b"host", b"example.org")],
            "query_string": b"",
            "subprotocols": [],
        }
        await start_server(server)
        connection = WsxConnection(server, scope, receive, send)
        await connection.serve()
        assert seen["handshake"] == "wsx"
        assert connection._request_scope(WsxEnvelope(envelope))["kajenn.channel"] == "wsx"

    def test_http_channel_is_a_class_attribute(self) -> None:
        # wf:contract: RoutedApplication.http_channel names the channel of a plain HTTP
        # wf:contract: request; the MCP/OpenAPI application resolves it to http_channel.
        assert RoutedApplication.http_channel == "api"
        assert RoutedApplication.http_channel == RoutedApplication.http_channel


class TestResolutionReadsTheScope:
    def test_auth_filters_carry_the_channel(self) -> None:
        # wf:contract: RoutedApplication.auth_filters(scope) returns channel_channel from
        # wf:contract: scope["kajenn.channel"].
        app = Plain(code="plain")
        filters = app.auth_filters({"type": "http", "kajenn.channel": "telegram"})
        assert filters["channel_channel"] == "telegram"

    async def test_a_tool_only_route_is_invisible_to_rest(self) -> None:
        # wf:contract: a route declared on channel "mcp" only is not resolved by a REST
        # wf:contract: request (404), and is resolved by a tool call.
        class McpOnly(RoutedApplication):
            @route(channel_channels="mcp")
            def hidden(self) -> dict[str, bool]:
                return {"hidden": True}

        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""}), (McpOnly, {"code": "m"})],
            plugins={"openapi": True},
        )
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/m/hidden",
            "query_string": b"",
            "headers": [],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await start_server(server)
        await server(scope, receive, send)
        assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 404
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hidden", "arguments": {}},
        }
        answer = await drive(server, "/m/_meta/mcp", method="POST", body=json.dumps(envelope).encode())
        assert answer["result"]["structuredContent"] == {"hidden": True}
