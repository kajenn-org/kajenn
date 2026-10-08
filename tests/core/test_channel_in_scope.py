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

"""The channel travels in the scope; ``server.channels()`` is configured.

Every face writes ``scope["kajenn.channel"]`` — ``rest`` for an HTTP request,
``mcp`` for a tool call, ``wsx`` for a websocket message, whatever the frame
said for the bus — and ``RoutedApplication.auth_filters`` resolves the route on
it. The configuration declares, per channel, the route that authenticates.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from genro_routes import route

from kajenn import AsgiServer, BaseApplication, McpOpenApiApplication, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.types import Message, Scope


class Faces(McpOpenApiApplication):
    @route(channel_channels="mcp,rest")
    def channel(self, _request=None) -> dict[str, Any]:
        return {"channel": _request.scope.get("kajenn.channel")}


class Plain(RoutedApplication):
    @route()
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

    await server(scope, receive, send)
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(raw)


class TestTheScopeKey:
    async def test_a_rest_request_is_on_the_rest_channel(self) -> None:
        # wf:contract: dispatch sets scope["kajenn.channel"] to the application's
        # wf:contract: http_channel ("rest") when the scope carries none.
        assert await drive(faces_server(), "/faces/channel") == {"channel": "rest"}
        assert await drive(faces_server(), "/plain/channel") == {"channel": "rest"}

    async def test_a_tool_call_is_on_the_mcp_channel(self) -> None:
        # wf:contract: the scope built for a tools/call carries kajenn.channel = "mcp".
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "channel", "arguments": {}},
        }
        answer = await drive(
            faces_server(), "/faces/mcp", method="POST", body=json.dumps(envelope).encode()
        )
        assert answer["result"]["structuredContent"] == {"channel": "mcp"}

    async def test_a_bus_call_keeps_the_channel_of_the_frame(self) -> None:
        # wf:contract: serve_kbus_frame writes the frame's channel; without one the
        # wf:contract: application's http_channel applies.
        server = faces_server()
        assert await server.kbus_call("/plain/channel", {}, channel="telegram") == {
            "channel": "telegram"
        }
        assert await server.kbus_call("/plain/channel", {}) == {"channel": "rest"}

    async def test_a_wsx_message_is_on_the_wsx_channel(self) -> None:
        # wf:contract: WsxConnection._request_scope sets kajenn.channel = "wsx" on the
        # wf:contract: synthetic scope of every message, and the handshake scope carries
        # wf:contract: the same key before authentication.
        from kajenn.wsx import WsxConnection, WsxEnvelope

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
        connection = WsxConnection(server, scope, receive, send)
        await connection.serve()
        assert seen["handshake"] == "wsx"
        assert connection._request_scope(WsxEnvelope(envelope))["kajenn.channel"] == "wsx"

    def test_http_channel_is_a_class_attribute(self) -> None:
        # wf:contract: RoutedApplication.http_channel names the channel of a plain HTTP
        # wf:contract: request; the MCP/OpenAPI application resolves it to rest_channel.
        assert RoutedApplication.http_channel == "rest"
        assert McpOpenApiApplication.http_channel == McpOpenApiApplication.rest_channel


class TestResolutionReadsTheScope:
    def test_auth_filters_carry_the_channel(self) -> None:
        # wf:contract: RoutedApplication.auth_filters(scope) returns channel_channel from
        # wf:contract: scope["kajenn.channel"]; the MCP application no longer overrides it.
        app = Plain(code="plain")
        filters = app.auth_filters({"type": "http", "kajenn.channel": "telegram"})
        assert filters["channel_channel"] == "telegram"
        assert "auth_filters" not in McpOpenApiApplication.__dict__

    async def test_a_tool_only_route_is_invisible_to_rest(self) -> None:
        # wf:contract: a route declared on channel "mcp" only is not resolved by a REST
        # wf:contract: request (404), and is resolved by a tool call.
        class McpOnly(McpOpenApiApplication):
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

        await server(scope, receive, send)
        assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 404
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hidden", "arguments": {}},
        }
        answer = await drive(server, "/m/mcp", method="POST", body=json.dumps(envelope).encode())
        assert answer["result"]["structuredContent"] == {"hidden": True}


class ChannelsRecipe(AsgiConfigBuilder):
    """A recipe declaring which route authenticates the ``mcp`` channel."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        channels = cfg.channels()
        channels.channel(name="mcp", authentication_route="/idp/check")
        apps = cfg.applications(default="plain")
        apps.application(code="plain", mount="", app_class=Plain)


class RouteLessRecipe(AsgiConfigBuilder):
    """A recipe declaring a channel without its authentication route."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        cfg.channels().channel(name="telegram")
        apps = cfg.applications(default="plain")
        apps.application(code="plain", mount="", app_class=Plain)


class TestTheConfiguration:
    def test_channels_reach_the_server(self) -> None:
        # wf:contract: configuration.channels().channel(name, authentication_route) is
        # wf:contract: lifted to the server kwarg `channels` and exposed as server.channels.
        server = AsgiServer(config=ChannelsRecipe)
        assert server.channels == {"mcp": {"authentication_route": "/idp/check"}}

    def test_no_channels_means_an_empty_mapping(self) -> None:
        # wf:contract: without the element server.channels is {}.
        server = faces_server()
        assert server.channels == {}

    def test_a_channel_without_route_is_a_configuration_error(self) -> None:
        # wf:contract: a channel declared without authentication_route is refused when
        # wf:contract: the server is built, with an error naming the channel.
        with pytest.raises(ValueError, match="telegram"):
            AsgiServer(config=RouteLessRecipe)
