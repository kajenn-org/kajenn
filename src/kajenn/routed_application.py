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
sub-trees via ``add_branches({"name": ..., "instance": child})``. The constructor peels
``db_name`` (the ``request.db`` seam resolves it against the server's
database registry) and plugs the ``auth`` plugin on the app router, so
entries declaring ``auth_rule`` are filtered by the request's authorization
tags — attached children inherit the plug.

The config-driven plugins (the ``openapi`` dialect) are armed LAZILY:
on the first ``route`` access made after the app is attached to a server, the
app calls ``server.arm_router(self.route)`` (once, guarded), so a server built
with a ``plugins`` section plugs them onto every routed app it hosts. A
composition whose server lacks the ``PluginMixin`` exposes no ``arm_router``
and arms nothing — the app degrades to the ``auth`` plug alone.

The ASGI dispatch (``__call__``) is the per-app routing engine: build a
``Request`` bound to this app, eager-parse it (``init``), resolve the node
from the request path — already mount-relative, the server demux strips the
prefix (D3) — with the identity tags of ``scope["auth"]`` as auth filters,
bind kwargs, execute (async handlers stay on the loop, sync handlers go
through ``server.run_sync`` — the pool protocol), then answer via
``request.response.set_result(value, metadata)``. Resolution failures raise
core exceptions (``ROUTER_ERRORS``: unknown or unavailable path →
``HTTPNotFound``; a ruled entry denied with no identity → ``HTTPUnauthorized``,
with an identity whose tags do not match → ``HTTPForbidden``) that propagate to
the server's ``ErrorMiddleware``.
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
``spread_over_params`` (extras dropped). Auth without the middleware: when
``scope`` carries no ``auth`` key the resolution runs unfiltered — the public
router exposes exactly what the auth plugin leaves untagged.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, is_result_wrapper

from .application import BaseApplication
from .exceptions import (
    HTTPBadRequest,
    HTTPException,
    HTTPForbidden,
    HTTPNotFound,
    HTTPUnauthorized,
)
from .request import Request
from .streaming import StreamingResponse
from .well_known import WELL_KNOWN_ROOT

if TYPE_CHECKING:
    from genro_routes import Router, RouterNode

    from .types import Receive, Scope, Send

__all__ = ["RoutedApplication"]


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

    Constructor kwargs peeled here: ``db_name`` — the server database code
    the ``request.db`` seam resolves for this app (``None`` falls back to
    ``"default"``). The rest flows down the D16 chain (``code``/``mount`` to
    ``BaseApplication``).
    """

    # genro-routes resolution error codes mapped to core HTTP exceptions (the
    # node.error string-code contract): an unknown or unavailable path answers
    # 404; a ruled entry denied answers on the HTTP distinction the router
    # already draws — 401 "I do not know who you are" when no identity was
    # presented, 403 "I know, and it is not enough" when the presented tags do
    # not match. The 401 is what ``ErrorMiddleware`` negotiates into a login
    # challenge, so a browser reaching a protected route lands on the login
    # page instead of a dead end.
    ROUTER_ERRORS: dict[str, type[Exception]] = {
        "not_found": HTTPNotFound,
        "not_available": HTTPNotFound,
        "not_authorized": HTTPForbidden,
        "not_authenticated": HTTPUnauthorized,
    }

    def __init__(self, **kwargs: Any) -> None:
        self._armed: bool = False
        self._db_name: str | None = kwargs.pop("db_name", None)
        super().__init__(**kwargs)
        self.route.plug("auth")

    @property
    def db_name(self) -> str | None:
        """Server database code for the ``request.db`` seam (``None`` → default)."""
        return self._db_name

    def _import_routing_class(self, module_path: str) -> RoutingClass:
        """Import and instantiate a ``RoutingClass`` from a ``"pkg.mod:Class"`` path.

        The class is instantiated with no arguments; an API needing constructor
        arguments is supplied as a ready ``routing_class=`` instance instead.
        Shared by the ``OpenApiApplication`` and ``McpApplication`` subclasses.
        """
        module_name, class_name = module_path.split(":")
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        return cls()  # type: ignore[no-any-return]

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

    @property
    def well_known_names(self) -> tuple[str, ...]:
        """The children of this app's ``_well_known`` branch — its discovery documents.

        Every child is one name served under ``/.well-known/``: an entry, or a
        sub-branch with a path of its own under it. A ruled entry is a name
        like any other (``forbidden=True``), because the answer to an identity
        that does not match is the application's own 401/403, never a 404 from
        the demux.

        The router is read through ``super().route``, NOT through the ``route``
        property: the server asks this at mount time, and the configured
        plugins are armed later, on the first access a request makes.
        """
        branch = super().route.router_at_path(WELL_KNOWN_ROOT)
        if branch is None:
            return ()
        children = branch.nodes(lazy=True, forbidden=True)
        return (*children.get("entries", ()), *children.get("routers", ()))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Resolve the request in the app router, execute, respond.

        Raises the mapped ``ROUTER_ERRORS`` exception when resolution fails;
        the server's ``ErrorMiddleware`` answers it. A call that does not fit
        the handler signature surfaces as ``HTTPBadRequest`` (400); values the
        handler's pydantic validation rejects surface on this application's
        ``validation_error_status`` (400 strict, 422 FastAPI). Both keep the
        original error as ``__cause__``. What the handler body raises is mapped
        to neither: it propagates as a 500.

        A handler that answers with a ``StreamingResponse`` — an SSE stream, a
        long download — speaks the wire itself: it is called with the ASGI
        triple and nothing is buffered.
        """
        server = self.server
        if server is None:
            raise RuntimeError(f"{type(self).__name__} dispatch requires an owning server")
        request = Request(scope, receive, server=server, application=self)
        await request.init()
        errors = {
            **self.ROUTER_ERRORS,
            "signature_error": _HandlerSignatureInvalid,
            "validation_error": _HandlerArgumentsInvalid,
        }
        node = self.route.node(request.path, errors=errors, **self.auth_filters(scope))
        call = self.make_callable(node, request)
        try:
            if asyncio.iscoroutinefunction(node):
                result = await call()
            else:
                result = await server.run_sync(call)
        except _HandlerSignatureInvalid as exc:
            detail = exc.__cause__ or exc
            raise HTTPBadRequest(f"Arguments do not fit the handler: {detail}") from exc
        except _HandlerArgumentsInvalid as exc:
            detail = exc.__cause__ or exc
            raise HTTPException(
                self.validation_error_status, f"Invalid argument values: {detail}"
            ) from exc
        if isinstance(result, StreamingResponse):
            await result(scope, receive, send)
            return
        if is_result_wrapper(result):
            request.response.set_result(result.value, {**node.metadata, **result.metadata})
        else:
            request.response.set_result(result, node.metadata)
        await request.response(scope, receive, send)

    def auth_filters(self, scope: Scope) -> dict[str, str]:
        """Auth filters for node resolution, from the scope identity.

        An ``Avatar`` on ``scope["auth"]`` becomes the comma-separated
        ``auth_tags`` the auth plugin evaluates entry rules against. No
        identity — key absent (middleware off) or ``None`` (anonymous) —
        passes no filter: the plugin still denies every ruled entry.
        """
        avatar = scope.get("auth")
        if avatar is None:
            return {}
        return {"auth_tags": ",".join(avatar.tags)}

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
        thread-local resources the handler's code opened (a synchronous db
        connection lives and must die on its own thread). No-op by default,
        same consumer-seam discipline as ``wsgi_app`` and ``build_registry``.
        The async path never calls it — an async handler owns its awaits.
        """

    def bind_kwargs(self, node: RouterNode, request: Request) -> dict[str, Any]:
        """Reconcile the request kwargs with the handler's declared parameters.

        Base: ``request.handler_kwargs()``. A hydrated JSON body arrives as a
        single ``body_data`` dict; a REST handler declares scalar parameters,
        so the dict is spread over the fields the handler accepts (from the
        node's neutral ``params`` block — never ``inspect``). The whole
        ``body_data`` is kept when the handler itself declares it, accepts
        ``**kwargs``, or exposes no signature (no pydantic plugin).

        A handler that declares ``_request`` is given the live ``Request``: a
        login surface and a websocket channel command both need what only the
        request knows, the cookie it came with. The seam belongs to every
        routed application, not to one of them.
        """
        kwargs = request.handler_kwargs()
        fields = node.params.get("fields") or []
        if any(f["name"] == "_request" for f in fields):
            # The declarative seam for a handler that needs the request itself
            # — the cookie it carries, the session on it, the server behind it.
            # It is declared UNANNOTATED, so it stays out of the pydantic model
            # and out of the public schema, and it overrides any same-named
            # value that arrived on the wire: nobody sends themselves a
            # request. No ambient state: it travels as an ordinary argument.
            kwargs["_request"] = request
        body = kwargs.get("body_data")
        if not isinstance(body, dict):
            return kwargs
        fields = node.params.get("fields")
        if fields is None:
            return kwargs
        param_names = {
            f["name"] for f in fields if f["kind"] not in ("var_positional", "var_keyword")
        }
        accepts_kwargs = any(f["kind"] == "var_keyword" for f in fields)
        if "body_data" in param_names or accepts_kwargs:
            return kwargs
        del kwargs["body_data"]
        kwargs.update(self.spread_over_params(node, body))
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
