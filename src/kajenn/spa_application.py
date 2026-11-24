# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""A routed SPA with one visit registry shared by its request handlers."""

from __future__ import annotations

from secrets import token_urlsafe
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from .exceptions import HTTPBadRequest, HTTPForbidden
from .request import Request
from .routed_application import RoutedApplication
from .spa_page import Page
from .spa_registry import SpaRegistry, UnknownPage
from .spa_transport import SpaVisit, prepare_visit, send_page_message
from .wsx import WSX_ROOT


if TYPE_CHECKING:
    from .server import BaseServer
    from .types import Scope


class _PageChannel(RoutingClass):
    def __init__(self, application: SpaApplicationBase) -> None:
        self.application = application
        self.route.plug("pydantic")

    @route(channel_channels="wsx")
    def openchannel(self, _request=None) -> dict[str, str]:
        page_id = _request.scope.get("genro.page_id")
        if not page_id:
            raise HTTPBadRequest("Missing page ID in WSX envelope")
        self.application.get_page(_request, page_id)
        return {"channel": page_id}


class SpaApplicationBase(RoutedApplication):
    """Subclass this application and use open_page/get_page from routed handlers."""

    persistent = True
    page_class = Page
    registry_class = SpaRegistry
    uses_spa_visits = True

    def __init__(self, **kwargs: Any) -> None:
        self.persistent = kwargs.pop("persistent", type(self).persistent)
        self.registry = self.registry_class()
        super().__init__(**kwargs)
        self.route.add_branches({"name": WSX_ROOT, "instance": _PageChannel(self)})

    async def authenticate_scope(self, scope: Scope) -> BaseServer:
        server: BaseServer = await super().authenticate_scope(scope)
        if not getattr(server, "kbus_role", None):
            scope["kajenn.spa_visit"] = await prepare_visit(server, self.code, scope)
        return server

    def connection_id(self, request: Request) -> str:
        """Get the request's connection and reconcile a verified session login."""
        visit: SpaVisit | None = request.scope.get("kajenn.spa_visit")
        if not visit:
            raise HTTPForbidden("A live browser session is required")
        if visit["identity"] is not None:
            avatar = request.avatar()
            if avatar is None or avatar.identity != visit["identity"]:
                raise HTTPForbidden("The request identity does not own this visit")
        try:
            self.registry.ensure_connection(
                visit["cid"], visit["identity"], login=visit["login"], ttl=visit["ttl"]
            )
        except PermissionError as error:
            raise HTTPForbidden(str(error)) from error
        return visit["cid"]

    def open_page(self, request: Request, page_class: type[Page] | None = None) -> Page:
        cid = self.connection_id(request)
        page_id = token_urlsafe(24)
        self.registry.new_page(page_id, connection_id=cid, page_class=page_class or self.page_class)
        try:
            return self.registry.resolve_page(
                self,
                page_id,
                connection_id=cid,
                request_pages=request.scope.setdefault("kajenn.spa_pages", {}),
            )
        except Exception:
            self.registry.drop_page(page_id, connection_id=cid)
            raise

    def get_page(self, request: Request, page_id: str) -> Page:
        cid = self.connection_id(request)
        try:
            return self.registry.resolve_page(
                self,
                page_id,
                connection_id=cid,
                request_pages=request.scope.setdefault("kajenn.spa_pages", {}),
            )
        except UnknownPage as error:
            raise HTTPForbidden("Unknown page for this connection") from error

    def close_page(self, request: Request, page_id: str) -> None:
        cid = self.connection_id(request)
        try:
            self.registry.drop_page(page_id, connection_id=cid)
        except UnknownPage as error:
            raise HTTPForbidden("Unknown page for this connection") from error

    def close_connection(self, request: Request) -> None:
        """Release this visit and every tab it owns."""
        self.registry.drop_connection(self.connection_id(request))

    async def send_page_message(
        self, page_id: str, path: str, data: Any = None, *, connection_id: str
    ) -> bool:
        try:
            state = self.registry.page_state(page_id, connection_id=connection_id)
        except UnknownPage:
            return False
        server = self.server
        if server is None:
            return False
        return await send_page_message(
            server, self.code, state.connection_id, state.user, state.page_id, path, data
        )
