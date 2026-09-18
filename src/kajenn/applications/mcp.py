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

"""MCP applications: the stateless Streamable HTTP transport over ``McpEngine``.

Two ready-to-mount apps expose a genro-routes router as MCP tools over JSON-RPC
2.0. Both delegate the HTTP shell to a :class:`McpTransport` — the helper that
owns everything the transport-agnostic :class:`McpEngine` does not:
method/header/Origin gating, the JSON-RPC envelope, the 202-for-notifications
rule, and the sync/async invoke callback (async handlers stay on the loop, sync
handlers go through the server pool via ``run_sync``). The transport holds its
owning application as ``self.application`` (dual-parent) and reaches its
``spread_over_params`` and ``server`` through it.

- :class:`McpApplication` — the whole app is one MCP endpoint. It holds an
  engine over an EXTERNAL router (``routing_class=`` or ``module=``); every
  request is a JSON-RPC message. Without a router ``initialize`` still answers
  and ``tools/list`` is empty.
- :class:`McpOpenApiApplication` — one router, two faces: it inherits the whole
  OpenApiApplication machinery (``_meta`` docs, pydantic plug, REST dispatch)
  and adds an MCP face under ``mcp_name_segment`` (default ``"mcp"``). The
  ``channel`` plugin drives per-face visibility: a method is an MCP tool only on
  channel ``"mcp"`` (``@route(channel_channels="mcp,rest")`` for a dual method);
  undeclared methods default to REST-only (``channels=rest_channel``).

Transport conformance (MCP Streamable HTTP): a JSON-RPC POST answers with a
JSON response; a notification (no ``id``) answers HTTP 202 with an empty body; a
GET opens the SSE push stream (below); any other method answers 405; an
``MCP-Protocol-Version`` header that is present but unsupported answers 400 (an
absent header is not gated, per the spec's backwards-compat rule); an
``Origin`` header present and not in ``allowed_origins`` answers 403 (the
``allowed_origins`` option defaults to ``None`` — no restriction, a dev-mode
default: production fronting owns the Origin gate). The transport gates raise
core HTTP exceptions answered by the server's ``ErrorMiddleware``.

The push half: a GET opens a ``text/event-stream`` keyed by ``Mcp-Session-Id``
— echoed when the client supplies one, minted otherwise
(``secrets.token_urlsafe``, decoupled from the cookie session: MCP clients
carry no cookie) — and follows the server's task hub live
(``server.tasks.hub``). A ``Last-Event-ID`` header replays the session's
current ``progress.json`` snapshots first: the baseline is the snapshot, there
is no durable event log. A server composed without tasks answers GET with 405.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from ..exceptions import HTTPBadRequest, HTTPException, HTTPForbidden
from ..mcp import JSONRPC_INTERNAL_ERROR, McpEngine, McpError
from ..request import Request
from ..routed_application import RoutedApplication
from ..sse import SseStream
from .openapi import OpenApiApplication

if TYPE_CHECKING:
    from genro_routes import Router, RouterNode, RoutingClass

    from ..types import Receive, Scope, Send

__all__ = ["McpApplication", "McpOpenApiApplication"]


class McpTransport:
    """Stateless MCP Streamable HTTP transport bound to a ``RoutedApplication``.

    Owns the HTTP shell (method/header/Origin gating, JSON-RPC envelope,
    202-for-notifications) and the sync/async invoke callback, driving an
    :class:`McpEngine` built over a router the host application supplies. The
    host is held as ``self.application`` (dual-parent): the transport reads its
    ``spread_over_params`` and ``server`` through it.
    """

    def __init__(
        self,
        application: RoutedApplication,
        *,
        name: str,
        version: str,
        tool_separator: str,
        channel: str,
        allowed_origins: list[str] | None,
    ) -> None:
        self.application = application
        self.channel = channel
        self.allowed_origins = allowed_origins
        self._name = name
        self._version = version
        self._tool_separator = tool_separator
        self._engine: McpEngine | None = None

    @property
    def engine(self) -> McpEngine | None:
        """The MCP engine driving the tool surface (``None`` until built)."""
        return self._engine

    def build_engine(self, router: Router | None) -> None:
        """Build the engine over ``router``, ensuring pydantic and auth.

        The pydantic plugin caches the neutral ``params``/``result`` blocks the
        engine reads for ``inputSchema``/``outputSchema``; the auth plugin
        enforces any ``auth_rule`` the router's handlers declare — without it
        a ruled tool would be listed and callable by anonymous clients. Each
        is plugged only if absent (plugging is not idempotent).
        """
        if router is not None:
            self.plug_if_absent(router, "pydantic")
            self.plug_if_absent(router, "auth")
        self._engine = McpEngine(
            router,
            name=self._name,
            version=self._version,
            tool_separator=self._tool_separator,
            channel=self.channel,
            invoke=self._invoke_tool,
        )

    def plug_if_absent(self, router: Router, name: str, **options: Any) -> None:
        """Plug ``name`` on ``router`` unless already attached (guards double-plug)."""
        if name not in {plugin.name for plugin in router.iter_plugins()}:
            router.plug(name, **options)

    def _invoke_tool(self, node: RouterNode, arguments: dict) -> Any:
        """Run a resolved tool node, adapting arguments and picking the vehicle.

        MCP ``arguments`` is the equivalent of a JSON body: fit it to the
        handler's declared parameters through the same ``spread_over_params``
        REST uses (extras dropped) — MCP wraps the API, it does not bypass it.
        An async handler stays on the loop; a sync handler goes through the
        server pool. Either way the returned awaitable is awaited by the engine.
        """
        app = self.application
        kwargs = app.spread_over_params(node, arguments)
        if asyncio.iscoroutinefunction(node):
            return node(**kwargs)
        server = app.server
        assert server is not None  # a request is in flight, so the app is owned
        return server.run_sync(lambda: node(**kwargs))

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer one MCP request over the Streamable HTTP transport.

        Gates the request (403/400), then routes by method: GET opens the SSE
        push stream, POST dispatches the JSON-RPC message through the engine
        and answers with the JSON-RPC envelope (a notification — no ``id`` —
        answers HTTP 202 with an empty body), anything else answers 405.
        """
        app = self.application
        request = Request(scope, receive, server=app.server, application=app)
        await request.init()
        origin = request.headers.get("origin")
        if self.allowed_origins is not None and origin is not None and origin not in self.allowed_origins:
            raise HTTPForbidden(f"Origin not allowed: {origin}")
        version = request.headers.get("mcp-protocol-version")
        if version is not None and version not in McpEngine.SUPPORTED_VERSIONS:
            raise HTTPBadRequest(f"Unsupported MCP-Protocol-Version: {version}")
        if request.method == "GET":
            await self.open_stream(request, scope, receive, send)
            return
        if request.method != "POST":
            raise HTTPException(405, "Method Not Allowed", headers=[(b"allow", b"GET, POST")])

        response = request.response
        envelope = request.data
        if isinstance(envelope, dict) and "id" not in envelope:  # notification
            response.status_code = 202
            await response(scope, receive, send)
            return
        jsonrpc_id = envelope.get("id") if isinstance(envelope, dict) else None
        assert self._engine is not None  # built at construction
        try:
            result = await self._engine.dispatch(envelope, request.auth_tags)
        except McpError as exc:
            payload = self._jsonrpc_error(exc.code, exc.message, jsonrpc_id)
        except Exception as exc:  # noqa: BLE001 — any handler failure becomes a JSON-RPC error
            payload = self._jsonrpc_error(JSONRPC_INTERNAL_ERROR, str(exc), jsonrpc_id)
        else:
            payload = {"jsonrpc": "2.0", "id": jsonrpc_id, "result": result}
        response.set_result(payload)
        await response(scope, receive, send)

    async def open_stream(self, request: Request, scope: Scope, receive: Receive, send: Send) -> None:
        """Open the SSE push stream for one MCP session (the GET branch).

        ``Mcp-Session-Id`` is echoed when the client supplies one, minted
        otherwise (``secrets.token_urlsafe``) and always returned in the
        response headers. A ``Last-Event-ID`` header asks for the snapshot
        baseline before the live feed. A server composed without tasks has no
        hub: GET answers 405.
        """
        server = self.application.server
        if not getattr(server, "tasks_enabled", False):
            raise HTTPException(405, "Method Not Allowed", headers=[(b"allow", b"POST")])
        session_id = request.headers.get("mcp-session-id") or secrets.token_urlsafe(32)
        last_event_id = request.headers.get("last-event-id")
        stream = SseStream(self._event_source(server, session_id, last_event_id))
        response = stream.response()
        response.set_header("mcp-session-id", session_id)
        await response(scope, receive, send)

    async def _event_source(
        self, server: Any, session_id: str, last_event_id: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        """The session's events: snapshot baseline on reconnect, then live.

        The baseline (only when the client presented ``Last-Event-ID``) replays
        the current ``progress.json`` of the session's pending/active tasks —
        spool reads off the loop via ``run_sync``. The live half follows a hub
        subscription, read against the server's ``leaving``: a server that
        starts leaving ends the feed here, so the stream closes by itself
        instead of being cancelled when the graceful wait runs out. The queue is
        unsubscribed on every road out (client gone, task cancelled, server
        leaving).
        """
        manager = server.tasks
        if last_event_id is not None:
            for event in await server.run_sync(lambda: self._baseline(manager, session_id)):
                yield self._sse_event(event)
        queue = manager.hub.subscribe(session_id)
        try:
            while True:
                event = await server.get_until_leaving(queue)
                if event is None:
                    return
                yield self._sse_event(event)
        finally:
            manager.hub.unsubscribe(session_id, queue)

    def _sse_event(self, event: Any) -> dict[str, Any]:
        """Adapt one hub event into the SSE event dict.

        The WHOLE hub event is the ``data:`` payload (the client gets
        ``type``/``task_id`` back intact); its ``type`` doubles as the SSE
        ``event:`` field so clients can listen per kind.
        """
        kind = event.get("type") if isinstance(event, dict) else None
        return {"event": kind, "data": event}

    def _baseline(self, manager: Any, session_id: str) -> list[dict[str, Any]]:
        """The current progress snapshots of the session's live tasks (sync, pool).

        Settled tasks are not replayed (their result is fetched, not streamed:
        there is no durable event log); tasks without a snapshot yield nothing.
        """
        events: list[dict[str, Any]] = []
        spool = manager.spool
        for descriptor in spool.list_pending() + spool.list_active(manager.worker_id):
            if descriptor.get("session_id") != session_id:
                continue
            snapshot = spool.read_progress(descriptor["task_id"])
            if snapshot is not None:
                events.append(
                    {"type": "progress", "task_id": descriptor["task_id"], "data": snapshot}
                )
        return events

    def _jsonrpc_error(self, code: int, message: str, jsonrpc_id: Any) -> dict:
        """Build a JSON-RPC 2.0 error envelope."""
        return {"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": code, "message": message}}


class McpApplication(RoutedApplication):
    """Standalone MCP transport: the whole app is one JSON-RPC endpoint.

    Supply the tool surface as an external ``RoutingClass`` (``routing_class=``
    instance, or ``module="pkg.mod:Class"`` imported and instantiated with no
    arguments); every route on it becomes a tool. The ``channel`` filter only
    bites when the external router plugs the ``channel`` plugin, so a plain
    router exposes all its routes. Without a router the app still answers
    ``initialize`` and lists no tools. Class attributes carry the MCP identity
    defaults so a subclass can set them declaratively.
    """

    mcp_name: ClassVar[str] = "genro-mcp"
    mcp_version: ClassVar[str] = "1.0.0"
    tool_separator: ClassVar[str] = "."
    mcp_channel: ClassVar[str] = "mcp"

    def __init__(self, **kwargs: Any) -> None:
        cls = type(self)
        name = kwargs.pop("mcp_name", cls.mcp_name)
        version = kwargs.pop("mcp_version", cls.mcp_version)
        separator = kwargs.pop("tool_separator", cls.tool_separator)
        allowed_origins = kwargs.pop("allowed_origins", None)
        routing_class: RoutingClass | None = kwargs.pop("routing_class", None)
        module: str | None = kwargs.pop("module", None)
        super().__init__(**kwargs)
        self._transport = McpTransport(
            self,
            name=name,
            version=version,
            tool_separator=separator,
            channel=cls.mcp_channel,
            allowed_origins=allowed_origins,
        )
        self._transport.build_engine(self._resolve_router(routing_class, module))

    @property
    def mcp_engine(self) -> McpEngine | None:
        """The MCP engine driving this app's tool surface (``None`` if unbuilt)."""
        return self._transport.engine

    def _resolve_router(
        self, routing_class: RoutingClass | None, module: str | None
    ) -> Router | None:
        """Resolve the external router from a ``routing_class`` or a ``module`` path."""
        if routing_class is None and module:
            routing_class = self._import_routing_class(module)
        return routing_class.route if routing_class is not None else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Every request is an MCP JSON-RPC message on the single endpoint."""
        await self._transport.handle(scope, receive, send)


class McpOpenApiApplication(OpenApiApplication):
    """OpenApiApplication that also exposes its API router as MCP tools.

    The REST/OpenAPI faces work exactly as in :class:`OpenApiApplication`; the
    MCP face answers under ``mcp_name_segment`` (default ``"mcp"``) via the same
    engine, pointed at the API router (the app's own in direct mode, the mounted
    class's in mounted mode). Visibility is the ``channel`` plugin's job: the
    MCP face lists only channel-``"mcp"`` entries; undeclared methods default to
    REST-only (``channels=rest_channel``).
    """

    mcp_name: ClassVar[str] = "genro-mcp"
    mcp_version: ClassVar[str] = "1.0.0"
    tool_separator: ClassVar[str] = "."
    mcp_channel: ClassVar[str] = "mcp"
    rest_channel: ClassVar[str] = "rest"

    def __init__(self, **kwargs: Any) -> None:
        cls = type(self)
        self._mcp_segment: str = kwargs.pop("mcp_name_segment", "mcp")
        name = kwargs.pop("mcp_name", cls.mcp_name)
        version = kwargs.pop("mcp_version", cls.mcp_version)
        separator = kwargs.pop("tool_separator", cls.tool_separator)
        allowed_origins = kwargs.pop("allowed_origins", None)
        self._mounted_router: Router | None = None
        # OpenApiApplication consumes routing_class/module to mount; the engine
        # is pointed at the app's own router (direct) or the mounted one.
        mounted = kwargs.get("routing_class") is not None or kwargs.get("module") is not None
        super().__init__(**kwargs)
        self._transport = McpTransport(
            self,
            name=name,
            version=version,
            tool_separator=separator,
            channel=cls.mcp_channel,
            allowed_origins=allowed_origins,
        )
        if mounted:
            self._transport.build_engine(self._mounted_router)
        else:
            self._transport.plug_if_absent(self.route, "channel")
            self.route.channel.configure(channels=cls.rest_channel)
            self._transport.build_engine(self.route)

    @property
    def mcp_engine(self) -> McpEngine | None:
        """The MCP engine driving this app's tool surface (``None`` if unbuilt)."""
        return self._transport.engine

    @property
    def mcp_name_segment(self) -> str:
        """Path segment under which the MCP JSON-RPC face is served."""
        return self._mcp_segment

    def auth_filters(self, scope: Scope) -> dict[str, str]:
        """Node-resolution filters: the base auth tags plus the REST channel.

        The API router carries the ``channel`` plugin (a method is an MCP tool
        only on channel ``"mcp"``), so the REST face must resolve on the REST
        channel; the MCP face passes ``"mcp"`` through the engine. The filter is
        harmless when no router on the path plugs ``channel`` (it is ignored).
        """
        filters = super().auth_filters(scope)
        filters["channel_channel"] = self.rest_channel
        return filters

    def schema_filters(self) -> dict[str, Any]:
        """Build the OpenAPI schema over the REST channel (the channel plugin is armed)."""
        return {"channel_channel": self.rest_channel}

    def _mount_routing_class(self, routing_class: RoutingClass) -> None:
        """Mount the API for OpenAPI, then remember its router for the MCP engine."""
        super()._mount_routing_class(routing_class)
        self._mounted_router = routing_class.route

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route the MCP segment to the JSON-RPC face; everything else to REST."""
        path = str(scope.get("path", "/")).lstrip("/")
        segment = path.partition("/")[0]
        if segment == self._mcp_segment:
            await self._transport.handle(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)
