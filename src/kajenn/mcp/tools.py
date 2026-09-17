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

"""McpTools — the ``tools/*`` family of the MCP protocol, served on the engine's tree.

The ``tools`` branch of :class:`~kajenn.mcp.engine.McpDispatcher`. Two
``@route`` methods, ``list`` and ``call``, and everything that builds their
answers; the engine is read for its configuration (``router``, ``channel``,
``tool_separator``, ``invoke``) and nothing else. Every route takes the
protocol signature ``(params, auth_tags)``.

- ``list`` walks ``router.nodes(forbidden=False, channel_channel=...)`` so
  only the entries reachable on the engine's channel are advertised. Tool
  names join the router path with ``tool_separator`` (default ``"."``, a
  character illegal in Python identifiers, so ``sub.ping`` <-> ``sub/ping``
  round-trips losslessly). Descriptors read ONLY the neutral blocks cached by
  genro-routes' pydantic plugin at decoration time: ``inputSchema`` from the
  entry's ``params.schema`` (aggregate ``request_schema``; fallback: an object
  schema assembled from ``params.fields``), ``outputSchema`` from
  ``result.schema`` (``response_schema``). Nothing is derived from the
  callable and pydantic is never imported here.
- ``call`` resolves the tool through ``router.node(path, ...)`` and reads the
  ``node.error`` STRING CODE (the stable genro-routes contract — resolution
  never raises): ``not_found``/``not_available`` -> -32601,
  ``not_authorized``/``not_authenticated`` -> -32000, any other code ->
  -32603. Execution is delegated to the engine's ``invoke`` callback so a host
  app can interpose parameter adaptation and pool dispatch; the default calls
  the node directly and an awaitable result is awaited here (async handlers),
  nothing more. Input-validation failures are TOOL EXECUTION errors —
  ``{"isError": true, "content": [...]}`` results, not JSON-RPC protocol
  errors (SEP-1303, enables model self-correction). Validation runs INSIDE
  genro-routes (the pydantic plugin validates at call time; nothing is
  re-validated here). Since genro-routes 0.28.0 every bad-argument error — a
  ``pydantic.ValidationError`` or an unbindable-argument ``TypeError`` alike —
  is channelled through the node's ``errors={"validation_error": ...}`` seam
  to a local marker, so this module needs no pydantic import; the bare
  ``TypeError`` catch remains for an async handler body raising at await time
  (a sync body's TypeError is already folded into the marker upstream). Both
  become ``isError`` results. A dict result is returned BOTH as
  ``structuredContent`` and as its JSON text rendering (the unstructured
  content SHOULD match the declared ``outputSchema``); any other result stays
  text-only.
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from .jsonrpc import JSONRPC_INTERNAL_ERROR, JSONRPC_METHOD_NOT_FOUND, JSONRPC_NOT_AUTHORIZED, McpError

if TYPE_CHECKING:
    from .engine import McpEngine

__all__ = ["McpTools"]


class _ToolArgumentsInvalid(Exception):
    """Marker raised by the node's ``validation_error`` exception mapping.

    genro-routes re-raises an escaping ``pydantic.ValidationError`` as the
    class mapped to the ``validation_error`` code, with the original error as
    ``__cause__`` — bad tool arguments are caught through this class without
    importing pydantic.
    """


class McpTools(RoutingClass):
    """The ``tools`` branch: ``list`` and ``call`` over the engine's router.

    Args:
        engine: the engine whose router, channel, separator and invoke
            callback these methods read.
    """

    def __init__(self, engine: McpEngine) -> None:
        self.engine = engine

    @route(name="list")
    def tools_list(self, params: dict, auth_tags: Any = None) -> dict:
        """Enumerate the tools visible on this channel.

        ``forbidden=False`` excludes entries the channel does not expose, so
        the tool list carries only what is reachable on this channel.
        """
        router = self.engine.router
        if router is None:
            return {"tools": []}
        nodes = router.nodes(forbidden=False, channel_channel=self.engine.channel)
        tools: list[dict] = []
        self._collect_tools(nodes, "", tools)
        return {"tools": tools}

    def _collect_tools(self, nodes: dict, prefix: str, tools: list) -> None:
        """Recursively collect tool descriptors from router nodes."""
        sep = self.engine.tool_separator
        for name, info in nodes.get("entries", {}).items():
            tool_name = name if not prefix else f"{prefix}{sep}{name}"
            tools.append(self._build_tool_descriptor(tool_name, info))
        for router_name, sub_nodes in nodes.get("routers", {}).items():
            sub_prefix = router_name if not prefix else f"{prefix}{sep}{router_name}"
            self._collect_tools(sub_nodes, sub_prefix, tools)

    def _build_tool_descriptor(self, tool_name: str, info: dict) -> dict:
        """Build an MCP tool descriptor from a nodes() entry info.

        Reads only the neutral blocks: ``inputSchema`` from the ``params``
        block, ``outputSchema`` from the ``result`` block (present when the
        pydantic plugin captured a return-type schema).
        """
        metadata = info.get("metadata") or {}
        description = metadata.get("meta", {}).get("description") or info.get("doc") or tool_name
        descriptor: dict = {
            "name": tool_name,
            "description": description,
            "inputSchema": self._input_schema(info),
        }
        output_schema = (info.get("result") or {}).get("schema")
        if output_schema is not None:
            descriptor["outputSchema"] = output_schema
        return descriptor

    def _input_schema(self, info: dict) -> dict:
        """Tool inputSchema from the neutral ``params`` block, never the callable.

        Primary source: the aggregate ``request_schema`` the pydantic plugin
        cached (copied before the ``type`` default so the cache is never
        mutated). Fallback: an object schema assembled from the cached
        per-parameter ``fields`` (untyped and var-parameters carry no schema
        and are skipped). No captured params -> empty object schema.
        """
        params_block = info.get("params") or {}
        schema = params_block.get("schema")
        if schema is not None:
            schema = dict(schema)
            schema.setdefault("type", "object")
            return schema
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in params_block.get("fields") or []:
            field_schema = field.get("schema")
            if field_schema is None:
                continue
            properties[field["name"]] = field_schema
            if field.get("required"):
                required.append(field["name"])
        assembled: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            assembled["required"] = required
        return assembled

    @route()
    async def call(self, params: dict, auth_tags: Any = None) -> dict:
        """Resolve a tool name to its router node, invoke it, wrap the result.

        Bad tool arguments come back as ``isError`` results — genro-routes
        0.28.0 folds validation failures AND unbindable arguments into the
        ``validation_error`` mapping, while the ``TypeError`` catch covers an
        async handler body raising at await time; resolution failures read
        ``node.error`` and raise :class:`McpError`.

        Raises:
            McpError: no router configured (-32603), unknown/unavailable tool
                (-32601), not authorized/authenticated (-32000), any other
                resolution code (-32603).
        """
        router = self.engine.router
        if router is None:
            raise McpError(JSONRPC_INTERNAL_ERROR, "No router configured")
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        # Reverse of _collect_tools: separator between segments -> path separator.
        path = name.replace(self.engine.tool_separator, "/")
        node = router.node(
            path,
            errors={"validation_error": _ToolArgumentsInvalid},
            auth_tags=",".join(auth_tags) if isinstance(auth_tags, list) else auth_tags,
            channel_channel=self.engine.channel,
        )
        if node.error in ("not_found", "not_available"):
            raise McpError(JSONRPC_METHOD_NOT_FOUND, f"Tool not found: {name}")
        if node.error in ("not_authorized", "not_authenticated"):
            raise McpError(JSONRPC_NOT_AUTHORIZED, "Not authorized")
        if node.error:
            raise McpError(JSONRPC_INTERNAL_ERROR, f"Router error: {node.error}")
        try:
            result = self.engine.invoke(node, arguments)
            if inspect.isawaitable(result):
                result = await result
        except (_ToolArgumentsInvalid, TypeError) as exc:
            detail = exc.__cause__ or exc
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Invalid tool arguments: {detail}"}],
            }
        return self._tool_result(result)

    def _tool_result(self, result: Any) -> dict:
        """Wrap a handler result as a ``tools/call`` result object.

        A dict rides BOTH as ``structuredContent`` and as JSON text content;
        anything else is text-only.
        """
        payload: dict[str, Any] = {
            "content": [{"type": "text", "text": self._serialize_result(result)}]
        }
        if isinstance(result, dict):
            payload["structuredContent"] = result
        return payload

    def _serialize_result(self, result: Any) -> str:
        """Serialize a result to the text content string."""
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)
