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
``tool_separator``, ``discover``, ``invoke``) and nothing else. Every route
takes the protocol signature ``(params, auth_tags)``.

- ``list`` walks the tree the engine's ``discover`` callback returns — a host
  application's ``discover`` (the request's channel and the caller's tags, the
  filters its execution resolves with), the default on the engine's router
  with its channel and ``auth_tags`` — so only the entries the caller can call
  on this channel are advertised. Tool names join the router path with
  ``tool_separator`` (default ``"."``, a
  character illegal in Python identifiers, so ``sub.ping`` <-> ``sub/ping``
  round-trips losslessly). Descriptors read ONLY the neutral blocks cached by
  genro-routes' pydantic plugin at decoration time: ``inputSchema`` from the
  entry's ``params.schema`` (aggregate ``request_schema``; fallback: an object
  schema assembled from ``params.fields``), ``outputSchema`` from
  ``result.schema`` (``response_schema``). Nothing is derived from the
  callable and pydantic is never imported here.
- ``call`` turns the tool name into its router path and hands it to the
  engine's ``invoke`` callback, which resolves and runs it — a host
  application through its execution point, the default on the engine's
  router — and an awaitable result is awaited here. The core HTTP exceptions
  a resolution raises map to JSON-RPC errors in one place: ``HTTPNotFound``
  -> -32601, ``HTTPUnauthorized``/``HTTPForbidden`` -> -32000. Input-validation
  failures are TOOL EXECUTION errors —
  ``{"isError": true, "content": [...]}`` results, not JSON-RPC protocol
  errors (SEP-1303, enables model self-correction). Validation runs INSIDE
  genro-routes (the pydantic plugin validates at call time; nothing is
  re-validated here). Every bad-argument error — a
  ``pydantic.ValidationError`` or an unbindable-argument ``TypeError`` alike —
  arrives as an ``HTTPBadRequest``, an ``HTTPException`` on a validation
  status (the execution point) or a local marker (the default invoke), so
  this module needs no pydantic import; the bare
  ``TypeError`` catch covers an async handler body raising at await time
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

from ..application import ERROR_CODES
from ..exceptions import HTTPException, HTTPForbidden, HTTPNotFound, HTTPUnauthorized
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
    async def tools_list(self, params: dict, auth_tags: Any = None) -> dict:
        """Enumerate the tools the caller can call on this channel.

        The tree comes from the engine's ``discover``: entries the channel does
        not expose or the caller's identity does not open are not in it.
        """
        if self.engine.router is None:
            return {"tools": []}
        nodes = self.engine.discover(auth_tags)
        if inspect.isawaitable(nodes):
            nodes = await nodes
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
        """Hand the tool's path to the engine's ``invoke``, wrap the result.

        Bad tool arguments come back as ``isError`` results — a 400 or the
        application's validation status from the execution point, the marker
        from the default invoke, and a ``TypeError`` an async handler body
        raises at await time; resolution failures raise :class:`McpError`.

        Raises:
            McpError: no router configured (-32603), unknown/unavailable tool
                (-32601), not authorized/authenticated (-32000).
        """
        if self.engine.router is None:
            raise McpError(JSONRPC_INTERNAL_ERROR, "No router configured")
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        # Reverse of _collect_tools: separator between segments -> path separator.
        path = name.replace(self.engine.tool_separator, "/")
        # Resolution is judged on the tree before the call, with the caller's
        # tags and the engine's channel: an unknown or off-channel tool is "not
        # found", a ruled one the caller may not use is "not authorized". An
        # HTTPNotFound raised later therefore comes from the handler's body and
        # is reported as the tool's own failure, not as a missing tool.
        probe = self.engine.router.node(
            path,
            auth_tags=",".join(auth_tags) if isinstance(auth_tags, list) else auth_tags,
            channel_channel=self.engine.channel,
        )
        if probe.error in ("not_found", "not_available"):
            raise McpError(JSONRPC_METHOD_NOT_FOUND, f"Tool not found: {name}")
        if probe.error in ("not_authenticated", "not_authorized"):
            raise McpError(JSONRPC_NOT_AUTHORIZED, "Not authorized")
        try:
            result = self.engine.invoke(path, arguments, auth_tags)
            if inspect.isawaitable(result):
                result = await result
        except HTTPNotFound as exc:
            raise McpError(JSONRPC_INTERNAL_ERROR, str(exc.detail or exc)) from exc
        except (HTTPUnauthorized, HTTPForbidden) as exc:
            raise McpError(JSONRPC_NOT_AUTHORIZED, "Not authorized") from exc
        except (HTTPException, _ToolArgumentsInvalid, TypeError) as exc:
            if isinstance(exc, HTTPException) and exc.status not in ERROR_CODES.values():
                raise
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
