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

"""McpEngine tests (Macro 4 Phase 7).

Drives ``dispatch`` directly over a small plugged router (pydantic + channel +
auth): initialize version negotiation, tools/list descriptors read from the
neutral cached blocks, tools/call with both handler natures and both
bad-argument escape paths (isError results, never JSON-RPC errors), the
``node.error`` string-code mapping and the message-shape rejections
(batching, unknown method).
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from genro_routes import RoutingClass, route

from kajenn import McpEngine, McpError
from kajenn.mcp import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_NOT_AUTHORIZED,
)


class SubTools(RoutingClass):
    """Nested service proving the tool-name separator."""

    @route(channel_channels="mcp")
    def ping(self) -> dict:
        """Ping."""
        return {"ok": True}


class ToolService(RoutingClass):
    """MCP-facing service: pydantic + channel + auth plugged."""

    def __init__(self) -> None:
        self.route.plug("pydantic")
        self.route.plug("channel")
        self.route.plug("auth")
        self.route.add_branches({"name": "sub", "instance": SubTools()})

    @route(channel_channels="mcp,rest")
    def add(self, x: int, y: int = 0) -> dict:
        """Add two numbers."""
        return {"sum": x + y}

    @route(channel_channels="mcp")
    async def greet(self, name: str) -> str:
        """Greet someone."""
        return f"hello {name}"

    @route(channel_channels="rest")
    def rest_only(self) -> dict:
        """REST-only endpoint, invisible on the mcp channel."""
        return {"rest": True}

    @route(channel_channels="mcp", auth_rule="admin")
    def secret(self) -> dict:
        """Admin-only tool."""
        return {"secret": True}


@pytest.fixture
def engine() -> McpEngine:
    return McpEngine(ToolService().route, name="test-server", version="9.9.9")


async def call_tool(engine: McpEngine, name: str, arguments: dict, auth_tags: Any = None) -> dict:
    payload = {"method": "tools/call", "params": {"name": name, "arguments": arguments}}
    return await engine.dispatch(payload, auth_tags)


class TestInitialize:
    async def test_echoes_a_supported_requested_version(self, engine: McpEngine) -> None:
        result = await engine.dispatch(
            {"method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        )
        assert result["protocolVersion"] == "2025-06-18"

    async def test_unknown_version_answers_latest(self, engine: McpEngine) -> None:
        result = await engine.dispatch(
            {"method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
        )
        assert result["protocolVersion"] == "2025-11-25"

    async def test_missing_version_answers_latest(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "initialize"})
        assert result["protocolVersion"] == "2025-11-25"

    async def test_capabilities_and_server_info_shape(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "initialize", "params": {}})
        assert result["capabilities"] == {"tools": {}, "experimental": {"push": {}}}
        assert result["serverInfo"] == {"name": "test-server", "version": "9.9.9"}


class TestToolsList:
    async def test_lists_only_the_mcp_channel_tools(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "tools/list"})
        names = {tool["name"] for tool in result["tools"]}
        # rest_only is off-channel; secret is auth-ruled and the anonymous
        # walk carries no tags, so the auth plugin denies it.
        assert names == {"add", "greet", "sub.ping"}

    async def test_nested_tool_name_uses_the_separator(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "tools/list"})
        by_name = {tool["name"]: tool for tool in result["tools"]}
        assert "sub.ping" in by_name

    async def test_input_schema_from_the_cached_request_schema(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "tools/list"})
        schema = {tool["name"]: tool for tool in result["tools"]}["add"]["inputSchema"]
        assert schema["type"] == "object"
        assert schema["properties"]["x"]["type"] == "integer"
        assert schema["required"] == ["x"]

    async def test_output_schema_from_the_cached_response_schema(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "tools/list"})
        by_name = {tool["name"]: tool for tool in result["tools"]}
        assert by_name["add"]["outputSchema"]["type"] == "object"
        assert by_name["greet"]["outputSchema"] == {"type": "string"}

    async def test_description_comes_from_the_docstring(self, engine: McpEngine) -> None:
        result = await engine.dispatch({"method": "tools/list"})
        by_name = {tool["name"]: tool for tool in result["tools"]}
        assert by_name["add"]["description"] == "Add two numbers."

    async def test_engine_without_router_lists_nothing(self) -> None:
        result = await McpEngine().dispatch({"method": "tools/list"})
        assert result == {"tools": []}

    def test_input_schema_fallback_assembles_from_fields(self, engine: McpEngine) -> None:
        info = {
            "params": {
                "schema": None,
                "fields": [
                    {"name": "x", "schema": {"type": "integer"}, "required": True, "kind": "pk"},
                    {"name": "y", "schema": {"type": "string"}, "required": False, "kind": "pk"},
                    {"name": "kwargs", "schema": None, "required": False, "kind": "var_keyword"},
                ],
            }
        }
        schema = engine.mcp_dispatcher.tools._input_schema(info)
        assert schema == {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "string"}},
            "required": ["x"],
        }

    def test_input_schema_without_params_block_is_empty_object(self, engine: McpEngine) -> None:
        assert engine.mcp_dispatcher.tools._input_schema({}) == {"type": "object", "properties": {}}


class TestToolsCall:
    async def test_sync_handler_dict_result_is_structured_and_text(
        self, engine: McpEngine
    ) -> None:
        result = await call_tool(engine, "add", {"x": 2, "y": 3})
        assert result["structuredContent"] == {"sum": 5}
        assert json.loads(result["content"][0]["text"]) == {"sum": 5}
        assert result["content"][0]["type"] == "text"
        assert "isError" not in result

    async def test_async_handler_scalar_result_is_text_only(self, engine: McpEngine) -> None:
        result = await call_tool(engine, "greet", {"name": "bob"})
        assert result["content"] == [{"type": "text", "text": "hello bob"}]
        assert "structuredContent" not in result

    async def test_nested_tool_resolves_through_the_separator(self, engine: McpEngine) -> None:
        result = await call_tool(engine, "sub.ping", {})
        assert result["structuredContent"] == {"ok": True}

    async def test_custom_async_invoke_callback(self) -> None:
        seen: list[dict] = []

        async def invoke(node: Any, arguments: dict) -> Any:
            seen.append(arguments)
            result = node(**arguments)
            if inspect.isawaitable(result):
                result = await result
            return result

        engine = McpEngine(ToolService().route, invoke=invoke)
        result = await call_tool(engine, "greet", {"name": "eve"})
        assert result["content"][0]["text"] == "hello eve"
        assert seen == [{"name": "eve"}]

    async def test_invalid_annotated_argument_is_a_tool_error(self, engine: McpEngine) -> None:
        # pydantic.ValidationError escape path -> isError result, no JSON-RPC error.
        result = await call_tool(engine, "add", {"x": "not-a-number"})
        assert result["isError"] is True
        assert "Invalid tool arguments" in result["content"][0]["text"]

    async def test_unknown_extra_argument_is_a_tool_error(self, engine: McpEngine) -> None:
        # sig.bind TypeError escape path -> isError result, no JSON-RPC error.
        result = await call_tool(engine, "add", {"x": 1, "z": 9})
        assert result["isError"] is True
        assert "Invalid tool arguments" in result["content"][0]["text"]

    async def test_anonymous_call_of_ruled_tool_is_not_authorized(
        self, engine: McpEngine
    ) -> None:
        with pytest.raises(McpError) as excinfo:
            await call_tool(engine, "secret", {})
        assert excinfo.value.code == JSONRPC_NOT_AUTHORIZED

    async def test_wrong_tags_call_of_ruled_tool_is_not_authorized(
        self, engine: McpEngine
    ) -> None:
        with pytest.raises(McpError) as excinfo:
            await call_tool(engine, "secret", {}, auth_tags="user")
        assert excinfo.value.code == JSONRPC_NOT_AUTHORIZED

    async def test_matching_tags_call_of_ruled_tool_succeeds(self, engine: McpEngine) -> None:
        result = await call_tool(engine, "secret", {}, auth_tags=["admin", "user"])
        assert result["structuredContent"] == {"secret": True}

    async def test_unknown_tool_is_method_not_found(self, engine: McpEngine) -> None:
        with pytest.raises(McpError) as excinfo:
            await call_tool(engine, "nope", {})
        assert excinfo.value.code == JSONRPC_METHOD_NOT_FOUND

    async def test_off_channel_tool_is_method_not_found(self, engine: McpEngine) -> None:
        # not_available (channel mismatch) maps to unknown tool, same as not_found.
        with pytest.raises(McpError) as excinfo:
            await call_tool(engine, "rest_only", {})
        assert excinfo.value.code == JSONRPC_METHOD_NOT_FOUND

    async def test_engine_without_router_is_internal_error(self) -> None:
        with pytest.raises(McpError) as excinfo:
            await call_tool(McpEngine(), "add", {"x": 1})
        assert excinfo.value.code == JSONRPC_INTERNAL_ERROR


class TestPing:
    async def test_ping_returns_empty_result(self, engine: McpEngine) -> None:
        # MCP 2025-11-25: servers MUST answer ping promptly with an empty result.
        assert await engine.dispatch({"method": "ping"}) == {}


class TestMessageShape:
    async def test_unknown_method_is_method_not_found(self, engine: McpEngine) -> None:
        with pytest.raises(McpError) as excinfo:
            await engine.dispatch({"method": "resources/list"})
        assert excinfo.value.code == JSONRPC_METHOD_NOT_FOUND

    async def test_list_payload_is_rejected(self, engine: McpEngine) -> None:
        # JSON-RPC batching (added 2025-03-26, removed 2025-06-18) is not supported.
        with pytest.raises(McpError) as excinfo:
            await engine.dispatch([{"method": "tools/list"}])
        assert excinfo.value.code == JSONRPC_INVALID_REQUEST

    async def test_non_object_payload_is_rejected(self, engine: McpEngine) -> None:
        with pytest.raises(McpError) as excinfo:
            await engine.dispatch("tools/list")
        assert excinfo.value.code == JSONRPC_INVALID_REQUEST

    async def test_missing_method_is_rejected(self, engine: McpEngine) -> None:
        with pytest.raises(McpError) as excinfo:
            await engine.dispatch({"params": {}})
        assert excinfo.value.code == JSONRPC_INVALID_REQUEST

    async def test_non_object_params_are_rejected(self, engine: McpEngine) -> None:
        with pytest.raises(McpError) as excinfo:
            await engine.dispatch({"method": "tools/list", "params": []})
        assert excinfo.value.code == JSONRPC_INVALID_REQUEST


class TestMcpError:
    def test_carries_code_and_message(self) -> None:
        error = McpError(JSONRPC_INTERNAL_ERROR, "boom")
        assert error.code == JSONRPC_INTERNAL_ERROR
        assert error.message == "boom"
        assert str(error) == "boom"
