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


"""McpEngine — MCP (JSON-RPC 2.0) core over a genro-routes Router.

The engine turns a Router's ``@route`` entries into MCP tools and serves the
protocol methods. It is transport- and app-agnostic: it holds a Router and a
channel to filter on and never touches HTTP concerns (headers, Origin,
202-for-notifications belong to the host application). ``dispatch`` receives
the parsed JSON-RPC message, validates the envelope, and resolves ``method``
on a genro-routes tree of its own — :class:`McpDispatcher`, held as
``mcp_dispatcher`` — the same machinery the HTTP side uses: no
chain of ``if`` on the method name. A method nobody serves reads
``node.error`` (the stable genro-routes contract — resolution never raises)
and becomes -32601 THERE, in one place. What ``dispatch`` returns is the
RESULT object — envelope bookkeeping (``id``, ``jsonrpc``) stays with the
transport; protocol failures raise :class:`McpError` carrying the JSON-RPC
code for the transport to render. A list payload is rejected with -32600:
JSON-RPC batching entered the MCP spec in 2025-03-26 and was removed in
2025-06-18.

The tree — the protocol revisions it speaks are ``SUPPORTED_VERSIONS``, newest
first — every route taking the protocol signature ``(params, auth_tags)``:

- ``ping`` answers an empty result (spec MUST).
- ``initialize`` negotiates the version: the client's requested version is
  echoed when it appears in ``SUPPORTED_VERSIONS``, anything else is answered
  with the latest supported revision.
- ``tools`` is a branch, :class:`~kajenn.mcp.tools.McpTools`: ``list``
  and ``call`` with everything that builds their answers. A further family of
  the protocol attaches the same way: a class of its own, added with
  ``add_branches``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from .jsonrpc import JSONRPC_INVALID_REQUEST, JSONRPC_METHOD_NOT_FOUND, McpError
from .tools import McpTools

if TYPE_CHECKING:
    from genro_routes import Router, RouterNode

__all__ = ["McpDispatcher", "McpEngine"]


class McpDispatcher(RoutingClass):
    """The root of the methods the engine serves: ``ping``, ``initialize``, ``tools/…``.

    The tree is the table: ``route.nodes()`` lists what the engine answers
    without a message being dispatched. The branches are kept as attributes so
    a host can attach its own family beside them.

    Args:
        engine: the engine whose identity and versions ``initialize`` answers.
    """

    def __init__(self, engine: McpEngine) -> None:
        self.engine = engine
        self.tools = McpTools(engine)
        self.add_branches([{"name": "tools", "instance": self.tools}])

    @route()
    def ping(self, params: dict, auth_tags: Any = None) -> dict:
        """The empty result the spec requires."""
        return {}

    @route()
    def initialize(self, params: dict, auth_tags: Any = None) -> dict:
        """Negotiate the protocol version and return the server capabilities.

        The client's requested version is echoed when supported; any other
        request is answered with the latest supported revision (spec
        negotiation rule).
        """
        engine = self.engine
        requested = params.get("protocolVersion")
        version = requested if requested in engine.SUPPORTED_VERSIONS else engine.SUPPORTED_VERSIONS[0]
        return {
            "protocolVersion": version,
            # experimental.push: the host transport's SSE progress channel
            # (GET + Mcp-Session-Id); the engine itself stays transport-blind.
            "capabilities": {"tools": {}, "experimental": {"push": {}}},
            "serverInfo": {"name": engine.name, "version": engine.version},
        }


class McpEngine:
    """MCP JSON-RPC core over a router.

    Args:
        router: The genro-routes Router whose entries are exposed as tools.
        name / version: server identity returned by ``initialize``.
        tool_separator: joins router/method segments into a flat tool name.
        channel: channel to filter entries on (visibility per channel).
        invoke: callback ``(node, arguments) -> result`` running a resolved
            node; ``tools/call`` awaits an awaitable result. Host applications
            pass their own to interpose parameter adaptation (e.g.
            ``spread_over_params``) and pool dispatch for sync handlers; the
            default calls the node directly.
    """

    SUPPORTED_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")

    def __init__(
        self,
        router: Router | None = None,
        *,
        name: str = "genro-mcp",
        version: str = "1.0.0",
        tool_separator: str = ".",
        channel: str = "mcp",
        invoke: Callable[[Any, dict], Any] | None = None,
    ) -> None:
        self.router = router
        self.name = name
        self.version = version
        self.tool_separator = tool_separator
        self.channel = channel
        self.invoke = invoke or self._default_invoke
        self.mcp_dispatcher = McpDispatcher(self)

    def _default_invoke(self, node: RouterNode, arguments: dict) -> Any:
        """Raw invocation, no parameter adaptation; ``tools/call`` awaits it."""
        return node(**arguments)

    async def dispatch(self, payload: Any, auth_tags: Any = None) -> dict:
        """Validate the envelope, resolve ``method`` on the tree, answer.

        Returns the JSON-RPC RESULT object; the transport owns the envelope.

        Raises:
            McpError: invalid message shape (-32600, batching included) or
                unknown method (-32601); ``tools/call`` resolution failures
                bubble up from :meth:`McpTools.call`.
        """
        if isinstance(payload, list):
            raise McpError(JSONRPC_INVALID_REQUEST, "JSON-RPC batching is not supported")
        if not isinstance(payload, dict):
            raise McpError(JSONRPC_INVALID_REQUEST, "Invalid JSON-RPC message")
        method = payload.get("method")
        if not isinstance(method, str):
            raise McpError(JSONRPC_INVALID_REQUEST, "Missing method")
        params = payload.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise McpError(JSONRPC_INVALID_REQUEST, "params must be an object")
        node = self.mcp_dispatcher.route.node(method)
        if node.error:
            raise McpError(JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
        result = node(params, auth_tags)
        if inspect.isawaitable(result):
            result = await result
        return result
