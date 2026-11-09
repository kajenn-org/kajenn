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

"""The MCP face of every ``RoutedApplication``: Streamable HTTP over ``McpEngine``.

:class:`McpTransport` owns everything the transport-agnostic :class:`McpEngine`
does not: method/header/Origin gating, the JSON-RPC envelope, the
202-for-notifications rule, and the invoke callback — a ``tools/call`` becomes
a ``Request`` on the tool's path, run through the application's ``execute``,
the execution point the ``api`` face uses, so a tool sees its ``_request`` and
a sync tool runs ``route_cleanup``. The transport holds its owning application
as ``self.application`` (dual-parent) and reaches its ``execute`` and
``server`` through it. The application answers it on ``_meta/mcp``.

Visibility is the ``channel`` plugin's job: a method is a tool only when it
declares channel ``"mcp"`` (``@route(channel_channels="mcp,api")`` for a method
on both faces); an undeclared method answers on ``api`` only.

:class:`McpMeta` is the ``_meta/mcp`` branch: ``tools`` lists the tools the
caller sees, readable with a plain GET.

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

import contextvars
import json
import secrets
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..exceptions import HTTPBadRequest, HTTPException, HTTPForbidden
from genro_routes import RoutingClass, route

from ..mcp import JSONRPC_INTERNAL_ERROR, McpEngine, McpError
from ..request import Request
from ..sse import SseStream

if TYPE_CHECKING:
    from genro_routes import Router

    from ..routed_application import RoutedApplication
    from ..types import Receive, Scope, Send

__all__ = ["McpMeta", "McpTransport"]


class McpTransport:
    """Stateless MCP Streamable HTTP transport bound to a ``RoutedApplication``.

    Owns the HTTP shell (method/header/Origin gating, JSON-RPC envelope,
    202-for-notifications) and the sync/async invoke callback, driving an
    :class:`McpEngine` built over a router the host application supplies. The
    host is held as ``self.application`` (dual-parent): the transport reads its
    ``execute`` and ``server`` through it.
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
        # The MCP POST request a tools/call belongs to, per task: the engine's
        # invoke protocol carries no request, the tool's Request is built on it.
        self.mcp_request: contextvars.ContextVar[Request] = contextvars.ContextVar("mcp_request")
        self._name = name
        self._version = version
        self._tool_separator = tool_separator
        self._engine: McpEngine | None = None

    @property
    def engine(self) -> McpEngine | None:
        """The MCP engine driving the tool surface (``None`` until built)."""
        return self._engine

    def build_engine(self, router: Router) -> None:
        """Build the engine over ``router``, the application's own.

        The application attached ``pydantic`` (the neutral ``params``/``result``
        blocks the engine reads for ``inputSchema``/``outputSchema``) and
        ``auth`` (a ruled tool is neither listed nor callable for a caller
        whose tags do not match) before building the transport.
        """
        self._engine = McpEngine(
            router,
            name=self._name,
            version=self._version,
            tool_separator=self._tool_separator,
            channel=self.channel,
            invoke=self._invoke_tool,
            discover=self._discover_tools,
        )

    async def _discover_tools(self, auth_tags: Any) -> dict:
        """The tool tree of the MCP POST, through the application's ``discover``.

        The scope is the MCP POST's own: channel ``mcp`` and the identity the
        POST came with — the filters a ``tools/call`` on the same POST executes
        with. ``auth_tags`` is not read: the identity is on the scope.
        """
        scope = self.mcp_request.get().scope
        return await self.application.discover(scope)

    async def _invoke_tool(self, path: str, arguments: dict, auth_tags: Any) -> Any:
        """Run a tool through the application's ``execute``, as a request of its own.

        The tool's scope is the MCP POST scope on the tool's path: a POST whose
        JSON body is ``arguments`` — the equivalent of a REST body, spread over
        the handler's parameters by ``bind_kwargs`` — carrying the original
        headers and the identity the POST came with (``scope["auth"]``).
        ``auth_tags`` is not read: the identity is on the scope.
        """
        app = self.application
        mcp_scope = self.mcp_request.get().scope
        tool_path = "/" + path
        headers = [
            (name, value)
            for name, value in mcp_scope.get("headers", [])
            if name not in (b"content-type", b"content-length")
        ]
        scope = {
            **mcp_scope,
            "path": tool_path,
            "raw_path": tool_path.encode(),
            "method": "POST",
            "headers": [(b"content-type", b"application/json"), *headers],
            "query_string": b"",
            "kajenn.channel": self.channel,
        }
        body = json.dumps(arguments).encode()

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive, server=app.server, application=app)
        await request.init()
        return await app.execute(request)

    async def list_tools(self, scope: Scope) -> list[dict[str, Any]]:
        """The ``tools/list`` result for ``scope``, read on channel ``mcp``.

        ``scope`` is an authenticated request scope of any channel: a copy of
        it moves to the transport's channel and stands in for the MCP POST the
        discovery reads its filters from.
        """

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        app = self.application
        request = Request({**scope, "kajenn.channel": self.channel}, receive,
                          server=app.server, application=app)
        assert self._engine is not None  # built at construction
        token = self.mcp_request.set(request)
        try:
            result = await self._engine.dispatch(
                {"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, request.auth_tags)
        finally:
            self.mcp_request.reset(token)
        tools: list[dict[str, Any]] = result["tools"]
        return tools

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer one MCP request over the Streamable HTTP transport.

        Gates the request (403/400), then routes by method: GET opens the SSE
        push stream, POST dispatches the JSON-RPC message through the engine
        and answers with the JSON-RPC envelope (a notification — no ``id`` —
        answers HTTP 202 with an empty body), anything else answers 405.
        """
        app = self.application
        scope["kajenn.channel"] = self.channel
        request = Request(scope, receive, server=app.server, application=app)
        await request.init()
        origin = request.headers.get("origin")
        if self.allowed_origins is not None and origin is not None and origin not in self.allowed_origins:
            raise HTTPForbidden(f"Origin not allowed: {origin}")
        version = request.headers.get("mcp-protocol-version")
        if version is not None and version not in McpEngine.SUPPORTED_VERSIONS:
            raise HTTPBadRequest(f"Unsupported MCP-Protocol-Version: {version}")
        # Authenticated after the gates: a refused Origin or version is answered
        # without spending a credential verification.
        if scope.get("auth") is None:
            scope["auth"] = await app.server.authenticate(scope)
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
        self.mcp_request.set(request)
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


class McpMeta(RoutingClass):
    """The ``_meta/mcp`` branch: the tools the caller sees, over a plain GET."""

    def __init__(self, application: RoutedApplication) -> None:
        self.application = application

    @route()
    async def tools(self, _request=None) -> list[dict[str, Any]]:
        """The ``tools/list`` result for the caller, without speaking MCP.

        The list is the one a ``tools/list`` with the same identity gets.
        ``_request`` is injected by ``bind_kwargs`` and left unannotated, out
        of the schema.
        """
        return await self.application.mcp_transport.list_tools(_request.scope)
