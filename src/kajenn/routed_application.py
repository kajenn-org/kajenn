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

"""RoutedApplication: the application base wiring genro-routes into the core.

``RoutedApplication`` composes the app-side contract (``BaseApplication``)
with the genro-routes ``RoutingClass``: handlers are ``@route``-decorated
methods on subclasses, and external ``RoutingClass`` instances mount as
sub-trees via ``add_branches({"name": ..., "instance": child})``. The constructor
plugs the ``auth`` plugin on the app router, so
entries declaring ``auth_rule`` are filtered by the request's authorization
tags — attached children inherit the plug.

The config-driven plugins (the ``openapi`` dialect) are armed LAZILY:
on the first ``route`` access made after the app is attached to a server, the
app calls ``server.arm_router(self.route)`` (once, guarded), so a server built
with a ``plugins`` section plugs them onto every routed app it hosts. A
composition whose server lacks the ``PluginMixin`` exposes no ``arm_router``
and arms nothing — the app degrades to the ``auth`` plug alone.

The ASGI dispatch (``__call__``) builds a ``Request`` bound to this app,
eager-parses it (``init``) and hands it to ``execute``, the single execution
point every face reaches (REST, MCP, WSX, the KajennBus): resolve the node
from the request path — already mount-relative, the server demux strips the
prefix (D3) — with the identity tags of ``scope["auth"]`` as auth filters,
bind kwargs, execute (async handlers stay on the loop, sync handlers go
through ``server.run_sync`` — the pool protocol), then answer via
``request.response.set_result(value, metadata)``. Resolution failures raise
core exceptions (``ROUTER_ERRORS``: unknown or unavailable path →
``HTTPNotFound``; a ruled entry denied with no identity → ``HTTPUnauthorized``,
with an identity whose tags do not match → ``HTTPForbidden``) that propagate to
the server's ``ErrorMiddleware``. An HTTP request on a method other than the
one the entry declares (``openapi_method``) answers 405 with an ``Allow``
header naming the declared method, and the handler never runs; an entry
declaring no method answers every method.
A call the handler cannot take becomes an HTTP answer — never a 500 — on the
two exception codes genro-routes distinguishes: ``signature_error`` (the bind
against the handler signature fails: unknown keyword, missing required
argument, too many positionals) → ``HTTPBadRequest`` (400);
``validation_error`` (the signature is satisfied and pydantic rejects the
values) → the status the application declared in its own grammar
(``application.validation_error_status``: 400 under the strict reading, the
default, and 422 for an application asking for the FastAPI convention). The
handler BODY is mapped to neither: whatever it raises — a
``TypeError`` included, sync body or async — propagates and reaches
``ErrorMiddleware`` as a 500. There is no local cleanup drain: the server
``finally`` owns end-of-request cleanups.

Kwargs binding: ``bind_kwargs`` starts from ``request.handler_kwargs()``
(query + body by content-type) and reconciles a hydrated JSON body with a
scalar-parameter handler — when the node's neutral ``params`` block declares
fields (pydantic plugin) and the handler does not itself absorb ``body_data``
or ``**kwargs``, the body dict is spread over the declared names through
``spread_over_params`` (extras dropped). On the ``mcp`` channel the body is
the tool's ``arguments`` and is always spread, whatever the handler
declares. Auth without the middleware: when
``scope`` carries no ``auth`` key the resolution runs unfiltered — the public
router exposes exactly what the auth plugin leaves untagged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from genro_routes import Router, RoutingClass, is_result_wrapper

from .application import BaseApplication
from .applications.mcp import McpTransport
from .applications.openapi import ApplicationMeta
from .exceptions import ROUTER_ERRORS, HTTPBadRequest, HTTPException
from .plugins.openapi.plugin import METHOD_NOT_ALLOWED, OpenAPIPlugin
from .request import Request
from .streaming import StreamingResponse

if TYPE_CHECKING:
    from genro_routes import RouterNode

    from .types import Receive, Scope, Send

__all__ = ["RoutedApplication"]

logger = logging.getLogger(__name__)

#: The one segment the kernel reserves inside a routed application.
META_SEGMENT = "_meta"

#: Where the MCP JSON-RPC face answers, relative to the application.
META_MCP = META_SEGMENT + "/mcp"

#: The URLs of 0.8.0 and where they moved. They give the new answer, with a
#: ``Deprecation`` header and a log line, until their removal is decided.
MOVED_URLS = {
    "_meta/schema_json": "_meta/api/schema_json",
    "_meta/docs": "_meta/api/docs",
    "mcp": META_MCP,
}


class _HandlerSignatureInvalid(Exception):
    """Marker raised by the node's ``signature_error`` exception mapping.

    genro-routes binds the call against the handler signature before running
    it: an unknown keyword, a missing required argument or too many positionals
    re-raise as this class, the binding ``TypeError`` kept as ``__cause__``.
    """


class _HandlerArgumentsInvalid(Exception):
    """Marker raised by the node's ``validation_error`` exception mapping.

    The signature is satisfied and pydantic rejects the values: genro-routes
    re-raises the ``pydantic.ValidationError`` as this class, the original error
    kept as ``__cause__``; the dispatcher catches rejected values without
    importing pydantic (the sibling of the MCP engine's marker).
    """


class RoutedApplication(BaseApplication, RoutingClass):
    """Application base serving ``@route`` handlers through the app router.

    Every routed application has two faces on the same router. The ``api``
    face answers a plain HTTP request on the route's path and is described by
    OpenAPI under ``_meta/api``; the MCP face answers JSON-RPC on
    ``_meta/mcp``. The ``channel`` plugin decides what each face shows: a route
    that declares no ``channel_channels`` answers on ``api`` only, a tool is a
    route declaring ``mcp`` (``@route(channel_channels="mcp,api")`` for both).
    ``_meta`` is the one segment the application cannot use for a route of its
    own.

    Constructor kwargs peeled here: ``docs`` (``"swagger"``, the default, or
    ``"off"``, which turns off only the Swagger UI page), ``mcp_name``,
    ``mcp_version``, ``tool_separator`` (class attributes give the defaults)
    and ``allowed_origins`` (the Origin gate of the MCP face, ``None`` for no
    restriction). The rest flows down the D16 chain (``code``/``mount``/
    ``db_name`` to ``BaseApplication``).
    """

    ROUTER_ERRORS: dict[str, type[Exception]] = ROUTER_ERRORS

    # The channel of a request that reaches ``__call__`` without one: a plain
    # HTTP request, a kbus frame that names none. It is also the default of
    # every route that declares no channel.
    http_channel: ClassVar[str] = "api"
    mcp_channel: ClassVar[str] = "mcp"
    mcp_name: ClassVar[str] = "kajenn-mcp"
    mcp_version: ClassVar[str] = "1.0.0"
    tool_separator: ClassVar[str] = "."
    openapi_info: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        cls = type(self)
        self._armed: bool = False
        self._docs_style: str = kwargs.pop("docs", "swagger")
        name = kwargs.pop("mcp_name", cls.mcp_name)
        version = kwargs.pop("mcp_version", cls.mcp_version)
        separator = kwargs.pop("tool_separator", cls.tool_separator)
        allowed_origins = kwargs.pop("allowed_origins", None)
        super().__init__(**kwargs)
        router = self.route
        # Every plugin the faces read is registered and attached here, before
        # any route is resolved: genro-routes drops the per-route options of a
        # plugin (``openapi_method``) whose class was not registered yet when
        # the routes were first resolved (genro-org/genro-routes#74).
        if OpenAPIPlugin.plugin_code not in Router.available_plugins():
            Router.register_plugin(OpenAPIPlugin)
        router.plug("auth")
        router.plug("channel")
        router.channel.configure(channels=cls.http_channel)
        router.plug("pydantic")
        router.plug(OpenAPIPlugin.plugin_code)
        # A branch of the same name is refused by ``add_branches`` itself. Only a
        # route declared under that name clashes: a catch-all route resolves
        # every segment without naming it (kajenn-org/kajenn#123).
        if router.node(META_SEGMENT).path == META_SEGMENT:
            raise ValueError(
                f"{cls.__name__}: {META_SEGMENT!r} is reserved and cannot name a route"
            )
        router.add_branches({"name": META_SEGMENT, "instance": ApplicationMeta(self)})
        self._mcp_transport = McpTransport(
            self,
            name=name,
            version=version,
            tool_separator=separator,
            channel=cls.mcp_channel,
            allowed_origins=allowed_origins,
        )
        self._mcp_transport.build_engine(router)

    @property
    def docs_style(self) -> str:
        """Documentation style — ``"swagger"`` or ``"off"``."""
        return self._docs_style

    @property
    def api_info(self) -> dict[str, Any]:
        """The OpenAPI info dict of the application: ``openapi_info``."""
        return self.openapi_info

    @property
    def mcp_transport(self) -> McpTransport:
        """The MCP face of this application."""
        return self._mcp_transport

    @property
    def mcp_engine(self) -> Any:
        """The MCP engine driving this application's tools."""
        return self.mcp_transport.engine

    def meta_url(self, path: str) -> str:
        """The public URL of ``_meta/<path>`` of this application."""
        return f"/{self.mount}/{META_SEGMENT}/{path}" if self.mount else f"/{META_SEGMENT}/{path}"

    @property
    def route(self) -> Router:
        """The app router; arms the server's configured plugins on first access.

        The first access made once a server owns this app triggers
        ``server.arm_router`` (once, guarded by ``_armed``); accesses before
        attachment — the ``auth`` plug in ``__init__`` — arm nothing.
        """
        router: Router = super().route
        arm_router = getattr(self.server, "arm_router", None)
        if not self._armed and arm_router is not None:
            self._armed = True
            arm_router(router)
        return router

    def active_well_known(self) -> dict[str, str]:
        """The declared well-known paths, each route checked against the router.

        A route must name an entry exactly, written without a leading slash — a
        best-match fallback to a shorter path does not count; one that does not
        raises ``ValueError`` naming it.
        A ruled entry counts like any other: its own auth rules answer the
        request. The router is read through ``super().route``, NOT through the
        ``route`` property: the server asks this at registration, and the
        configured plugins are armed later, on the first access a request makes.
        """
        return self.check_well_known_routes(super().active_well_known())

    def active_default_well_known(self) -> dict[str, str]:
        """The default well-known paths, each route checked like ``active_well_known``."""
        return self.check_well_known_routes(super().active_default_well_known())

    def check_well_known_routes(self, active: dict[str, str]) -> dict[str, str]:
        """``active`` unchanged, after checking that every route names an entry."""
        router = super().route
        for target in active.values():
            parent, _, name = target.rpartition("/")
            branch = router.router_at_path(parent)
            entries = branch.nodes(lazy=True, forbidden=True).get("entries", ()) if branch else ()
            # The demux forwards "/" + route as written: a leading slash
            # would reach the router as "//...", so it names no entry either.
            if target.startswith("/") or name not in entries:
                raise ValueError(
                    f"application {self.code!r}: well-known route {target!r} names no entry"
                )
        return active

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Build the request, run it through ``execute``, respond.

        A handler that answers with a ``StreamingResponse`` — an SSE stream, a
        long download — speaks the wire itself: it is called with the ASGI
        triple and nothing is buffered. Any other result is answered through
        ``request.response`` with the resolved node's metadata (a result
        wrapper's own metadata on top).

        ``_meta/mcp`` goes to the MCP face. A URL of 0.8.0 (``MOVED_URLS``)
        gives the answer of its new address, with a ``Deprecation`` header and
        a log line; ``mcp`` moves only while the application has no route of
        that name.
        """
        path = str(scope.get("path", "/")).strip("/")
        moved = self.moved_url(path)
        if moved is not None:
            logger.warning("deprecated URL %r of application %r: use %r",
                           path, self.code, moved)
            send = self.deprecated_send(send, self.meta_url(moved.removeprefix(META_SEGMENT + "/")))
            path = moved
            scope = {**scope, "path": "/" + moved, "raw_path": ("/" + moved).encode()}
        if path == META_MCP:
            await self.mcp_transport.handle(scope, receive, send)
            return
        scope.setdefault("kajenn.channel", self.http_channel)
        request = Request(scope, receive, server=self.server, application=self)
        await request.init()
        result = await self.execute(request)
        if isinstance(result, StreamingResponse):
            await result(scope, receive, send)
            return
        node = request.node
        if is_result_wrapper(result):
            request.response.set_result(result.value, {**node.metadata, **result.metadata})
        else:
            request.response.set_result(result, node.metadata)
        await request.response(scope, receive, send)

    def moved_url(self, path: str) -> str | None:
        """The new address of ``path`` when it is a URL of 0.8.0, else ``None``."""
        moved = MOVED_URLS.get(path)
        if moved is None or path != "mcp":
            return moved
        return moved if super().route.node(path).error == "not_found" else None

    def deprecated_send(self, send: Send, successor: str) -> Send:
        """``send`` with ``Deprecation`` and a ``successor-version`` link on the response start."""

        async def deprecated(message: Any) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [
                    *message.get("headers", []),
                    (b"deprecation", b"true"),
                    (b"link", f'<{successor}>; rel="successor-version"'.encode()),
                ]}
            await send(message)

        return deprecated

    async def execute(self, request: Request) -> Any:
        """Resolve ``request.path`` in the app router, run the handler, return its result.

        The single execution point every face reaches (#37): REST through
        ``__call__``, an MCP ``tools/call`` through the transport, WSX and the
        KajennBus through ``__call__``. The resolved node is left on
        ``request.node``. An async handler runs on the loop, a sync one through
        ``server.run_sync`` with ``route_cleanup`` after it on the same thread.
        A scope without ``auth`` is authenticated before resolution
        (``authenticate_scope``, the step ``discover`` shares): an invalid
        credential raises ``HTTPUnauthorized`` (401).

        Raises the mapped ``ROUTER_ERRORS`` exception when resolution fails,
        and a 405 ``HTTPException`` with ``Allow`` when the ``openapi`` plugin
        denies the request method (``method_filters``). A
        call that does not fit the handler signature surfaces as
        ``HTTPBadRequest`` (400); values the handler's pydantic validation
        rejects surface on this application's ``validation_error_status`` (400
        strict, 422 FastAPI). Both keep the original error as ``__cause__``.
        What the handler body raises is mapped to neither: it propagates.
        """
        server = await self.authenticate_scope(request.scope)
        errors = {
            **self.ROUTER_ERRORS,
            "signature_error": _HandlerSignatureInvalid,
            "validation_error": _HandlerArgumentsInvalid,
        }
        node = self.route.node(
            request.path,
            errors=errors,
            **self.auth_filters(request.scope),
            **self.method_filters(request.scope),
        )
        if node.error and node.error.startswith(METHOD_NOT_ALLOWED):
            allow = node.error.removeprefix(METHOD_NOT_ALLOWED)
            raise HTTPException(405, "Method Not Allowed", headers=[(b"allow", allow.encode())])
        request.node = node
        call = self.make_callable(node, request)
        try:
            if asyncio.iscoroutinefunction(node):
                return await call()
            return await server.run_sync(call)
        except _HandlerSignatureInvalid as exc:
            detail = exc.__cause__ or exc
            raise HTTPBadRequest(f"Arguments do not fit the handler: {detail}") from exc
        except _HandlerArgumentsInvalid as exc:
            detail = exc.__cause__ or exc
            raise HTTPException(
                self.validation_error_status, f"Invalid argument values: {detail}"
            ) from exc

    async def authenticate_scope(self, scope: Scope) -> Any:
        """Put the request identity on ``scope["auth"]`` when it is missing; return the server.

        The step ``execute`` and ``discover`` share: a scope without ``auth``
        is authenticated through ``server.authenticate`` — an invalid
        credential raises ``HTTPUnauthorized`` (401), no credential leaves
        ``None`` (anonymous). A scope that already carries an identity is left
        as it is.
        """
        server = self.server
        if server is None:
            raise RuntimeError(f"{type(self).__name__} dispatch requires an owning server")
        if scope.get("auth") is None:
            scope["auth"] = await server.authenticate(scope)
        return server

    async def discover(self, scope: Scope, **kwargs: Any) -> dict[str, Any]:
        """The neutral ``nodes()`` tree this request may execute.

        The discovery every format reads (the OpenAPI schema, the MCP
        ``tools/list``): the scope is authenticated as ``execute`` does it,
        then the router tree is filtered by the same ``auth_filters`` the
        execution resolves with — the request's channel and the caller's
        tags — with ``forbidden=False``, so an entry the caller may not
        execute is not listed. ``kwargs`` reach ``nodes()`` unchanged
        (``basepath``, ``lazy``, ``pattern``).
        """
        await self.authenticate_scope(scope)
        filters: dict[str, Any] = self.auth_filters(scope)
        return self.route.nodes(forbidden=False, **kwargs, **filters)

    def auth_filters(self, scope: Scope) -> dict[str, str]:
        """Filters for node resolution, from the scope identity and channel.

        ``scope["kajenn.channel"]`` becomes ``channel_channel``, the filter of
        the ``channel`` plugin (ignored when no router on the path plugs it); a
        scope handed to ``execute`` without the key resolves on ``http_channel``.
        An ``Avatar`` on ``scope["auth"]`` becomes the comma-separated
        ``auth_tags`` the auth plugin evaluates entry rules against. No
        identity — key absent (middleware off) or ``None`` (anonymous) —
        passes no ``auth_tags``: the plugin still denies every ruled entry.
        """
        filters = {"channel_channel": scope.get("kajenn.channel", self.http_channel)}
        avatar = scope.get("auth")
        if avatar is not None:
            filters["auth_tags"] = ",".join(avatar.tags)
        return filters

    def method_filters(self, scope: Scope) -> dict[str, str]:
        """The method filter for node resolution, on HTTP requests only.

        A request on ``http_channel`` that did not come through the KajennBus
        passes its method as ``openapi_method``: the ``openapi`` plugin denies
        an entry declaring another one, and ``execute`` answers 405. MCP, WSX
        and the KajennBus carry a method of their own transport and pass
        nothing. ``discover`` does not use this filter: the listing shows the
        routes of every method.
        """
        if scope.get("kajenn.kbus") or scope.get("kajenn.channel", self.http_channel) != self.http_channel:
            return {}
        return {"openapi_method": scope["method"]}

    def make_callable(self, node: RouterNode, request: Request) -> Callable[[], Any]:
        """Package the node invocation as the zero-arg call the dispatcher runs.

        The node declares its handler's nature (genro-routes marks async
        entries so ``asyncio.iscoroutinefunction(node)`` is honest); the
        dispatcher reads that same nature to pick the vehicle — the returned
        async callable is awaited on the loop, the sync one goes through the
        server pool. ``bind_kwargs`` decides the arguments.
        """
        kwargs = self.bind_kwargs(node, request)

        # asyncio.iscoroutinefunction, not inspect's: on 3.11 genro-routes
        # falls back to the asyncio sentinel, which only the asyncio check reads.
        if asyncio.iscoroutinefunction(node):

            async def acall() -> Any:
                return await node(**kwargs)

            return acall

        def call() -> Any:
            try:
                return node(**kwargs)
            finally:
                self.route_cleanup()

        return call

    def route_cleanup(self) -> None:
        """Per-dispatch cleanup on the executor thread — a consumer seam.

        The sync dispatch runs this on the SAME pool thread the handler just
        ran on, after it returned or raised: the place to release whatever
        thread-local resources the handler's code opened. Here it releases the
        databases the current call took (``server.release_databases``), so a
        sync handler's databases die on its own thread; an override calls
        ``super()`` to keep that. The async path never calls it: the release
        of what an ``async def`` handler took is the call's async cleanup.
        """
        call = self.server.requests.current
        if call is not None:
            self.server.release_databases(call)

    def bind_kwargs(self, node: RouterNode, request: Request) -> dict[str, Any]:
        """Reconcile the request kwargs with the handler's declared parameters.

        Base: ``request.handler_kwargs()``. A hydrated JSON body arrives as a
        single ``body_data`` dict; a REST handler declares scalar parameters,
        so the dict is spread over the fields the handler accepts (from the
        node's neutral ``params`` block — never ``inspect``). The whole
        ``body_data`` is kept when the handler itself declares it, accepts
        ``**kwargs``, or exposes no signature (no pydantic plugin). On the
        ``mcp`` channel the body is the tool's ``arguments``, i.e. the
        handler's kwargs: it is always spread, whatever the handler declares.

        A handler that declares ``_request`` is given the live ``Request``: a
        login surface and a websocket channel command both need what only the
        request knows, the cookie it came with. The seam belongs to every
        routed application, not to one of them.
        """
        kwargs = request.handler_kwargs()
        body = kwargs.get("body_data")
        fields = node.params.get("fields")
        if isinstance(body, dict) and fields is not None:
            param_names = {
                f["name"] for f in fields if f["kind"] not in ("var_positional", "var_keyword")
            }
            accepts_kwargs = any(f["kind"] == "var_keyword" for f in fields)
            keeps_body = "body_data" in param_names or accepts_kwargs
        else:
            keeps_body = True
        if isinstance(body, dict) and (
            request.scope.get("kajenn.channel") == self.mcp_channel
            or not keeps_body
        ):
            del kwargs["body_data"]
            kwargs.update(self.spread_over_params(node, body))
        if any(f["name"] == "_request" for f in fields or []):
            # The declarative seam for a handler that needs the request itself
            # — the cookie it carries, the session on it, the server behind it.
            # It is declared UNANNOTATED, so it stays out of the pydantic model
            # and out of the public schema, and it overrides any same-named
            # value that arrived on the wire: nobody sends themselves a
            # request. No ambient state: it travels as an ordinary argument.
            kwargs["_request"] = request
        return kwargs

    def spread_over_params(self, node: RouterNode, data: dict[str, Any]) -> dict[str, Any]:
        """Fit a dict of values to the handler's declared parameters.

        Shared by every wire dialect (a REST body, MCP arguments):
        keeps ``data`` whole when the handler declares no signature
        (``fields`` is ``None`` — no pydantic plugin) or accepts ``**kwargs``;
        otherwise keeps only the declared names, dropping extras. An empty
        ``fields`` list is a known no-parameter handler: everything drops.
        """
        fields = node.params.get("fields")
        if fields is None:
            return data
        if any(f["kind"] == "var_keyword" for f in fields):
            return data
        param_names = {
            f["name"] for f in fields if f["kind"] not in ("var_positional", "var_keyword")
        }
        return {k: v for k, v in data.items() if k in param_names}
