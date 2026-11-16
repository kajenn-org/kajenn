# Copyright 2026 Softwell S.r.l.
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

"""The MCP families of one application, counted by its ``McpTransport``.

Private to ``kajenn``: the application reaches it through
``mcp_transport.metrics``.

``McpMetrics`` is held by the ``McpTransport`` of a ``RoutedApplication`` and
owns ``prometheus_client`` metric objects created with ``registry=None``: they
belong to the application instance, never to the module-level ``REGISTRY``.
``McpTransport.handle`` counts every JSON-RPC message with an ``id`` it
dispatches; ``RoutedApplication.prometheus_metrics`` writes ``collect()`` into
its text, and a spawned application's text reaches ``_server/metrics``
through the forwarded hook.

The labels: ``method`` is one of ``MCP_METHODS``, ``other`` for anything else
(a missing method included); ``tool`` the name of the tool a ``tools/call``
resolved, ``""`` for every other call; ``outcome`` ``result``, ``tool_error``
(an ``isError`` result) or ``protocol_error`` (a JSON-RPC error answer).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram
from prometheus_client.metrics_core import Metric

__all__: list[str] = []

#: The methods the engine serves: the bounded values of the ``method`` label.
MCP_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call"})

OTHER_METHOD = "other"
TOOLS_CALL = "tools/call"


class McpMetrics:
    """The MCP call counter and the ``tools/call`` duration histogram of one application."""

    def __init__(self) -> None:
        self._calls = Counter(
            "kajenn_mcp_calls",
            "MCP JSON-RPC calls answered, per method, tool and outcome.",
            ["method", "tool", "outcome"], registry=None)
        self._tool_duration = Histogram(
            "kajenn_mcp_tool_call_duration_seconds",
            "Time to answer a tools/call whose tool resolved, in seconds.",
            ["tool"], registry=None)

    def record_call(self, method: object, tool: str, outcome: str, seconds: float) -> None:
        """Count one call; a ``tools/call`` whose ``tool`` resolved also gets its duration."""
        label = method if isinstance(method, str) and method in MCP_METHODS else OTHER_METHOD
        self._calls.labels(label, tool, outcome).inc()
        if label == TOOLS_CALL and tool:
            self._tool_duration.labels(tool).observe(seconds)

    def collect(self) -> list[Metric]:
        """The families of the two metrics, with their current values."""
        return [family
                for metric in (self._calls, self._tool_duration)
                for family in metric.collect()]
