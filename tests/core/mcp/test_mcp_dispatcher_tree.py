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

"""Implementation test: the engine's method tree is a table, readable without a message.

Photographs the shape of :class:`McpDispatcher` — the root routes, the
``tools`` branch and its class — and may be rewritten with it.
"""

from __future__ import annotations

from kajenn.mcp import McpDispatcher, McpEngine, McpTools


class TestMcpDispatcherTree:
    def test_root_lists_ping_initialize_and_the_tools_branch(self) -> None:
        engine = McpEngine()
        nodes = engine.mcp_dispatcher.route.nodes()
        assert set(nodes["entries"]) == {"ping", "initialize"}
        assert set(nodes["routers"]["tools"]["entries"]) == {"list", "call"}

    def test_the_tools_branch_is_an_mcp_tools_reading_the_engine(self) -> None:
        engine = McpEngine()
        assert isinstance(engine.mcp_dispatcher, McpDispatcher)
        assert isinstance(engine.mcp_dispatcher.tools, McpTools)
        assert engine.mcp_dispatcher.tools.engine is engine

    def test_a_method_nobody_serves_resolves_to_not_found(self) -> None:
        engine = McpEngine()
        assert engine.mcp_dispatcher.route.node("resources/list").error == "not_found"
        assert engine.mcp_dispatcher.route.node("tools/nope").error == "not_found"
