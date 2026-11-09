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

"""The ``_server/applications`` section: the life of the hosted applications.

``ApplicationsSection`` is a ``RoutingClass`` the ``ServerApplication``
attaches under ``applications``:

    ``POST /_server/applications/add``      write an application into the live configuration
    ``POST /_server/applications/ready``    the application ``code`` is in working state
    ``POST /_server/applications/disable``  switch the application ``code`` off
    ``POST /_server/applications/enable``   switch it on again, the same instance
    ``POST /_server/applications/remove``   take the application ``code`` away
    ``GET  /_server/applications/list``     every application, its mount, its state

An application other than ``_server`` is registered disabled and answers 503
with ``Retry-After`` until ``ready`` names it. The server sends ``ready``
itself, over the KajennBus, when the application's ``on_startup`` returns.
Switched off or being removed, it answers 503 without ``Retry-After``;
removed, its mount answers 404. ``_server`` is never disabled nor removed.
An application calling ``remove`` on its own code gives up: a ``mandatory``
one then leaves only ``_server`` answering.

Every route is gated ``auth_rule="SERVER_ADMIN"``: an application calling on
the bus carries that tag, an administrator calls with it.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from kajenn.application import BaseApplication
from kajenn.exceptions import HTTPBadRequest, HTTPException, HTTPNotFound

if TYPE_CHECKING:
    from ..server_app import ServerApplication

__all__ = ["SERVER_ADMIN_TAG", "ApplicationsSection"]

#: The tag every route of the section asks for.
SERVER_ADMIN_TAG = "SERVER_ADMIN"


class ApplicationsSection(RoutingClass):
    """The ``_server/applications`` mount: ready and list."""

    def __init__(self, application: ServerApplication) -> None:
        self.application = application

    @route(auth_rule=SERVER_ADMIN_TAG, openapi_method="post")
    def ready(self, code: str) -> dict[str, str]:
        """Enable the application ``code``; 404 when no application has that code."""
        self.answer_refusal(self.application.server.enable_application, code)
        return {"code": code, "state": "enabled"}

    @route(auth_rule=SERVER_ADMIN_TAG, openapi_method="post")
    async def add(
        self, app_class: str, code: str, params: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Write the application into the live configuration; it mounts disabled.

        ``app_class`` is an import path, ``"pkg.module:Class"``, of an
        application class; ``params`` are the extra keyword arguments of its
        constructor. 400 when the class is not an application or its
        constructor refuses the parameters, 409 when ``code`` or the mount is
        already claimed.
        """
        server = self.application.server
        if code in server.applications:
            raise HTTPException(409, f"application code already claimed: {code}")
        cls = self.import_application(app_class)
        options = dict(params or {})
        if "spawner" in options and server.kbus_dispatcher is None:
            raise HTTPBadRequest("this server started no KajennBus dispatcher: an external "
                                 "application must be declared at boot")
        nodes = server.application_nodes
        try:
            nodes.application(app_class=cls, code=code, **options)
        except ValueError as refused:
            nodes.pop(code)
            raise HTTPException(409, str(refused)) from refused
        except TypeError as refused:
            nodes.pop(code)
            raise HTTPBadRequest(str(refused)) from refused
        return {"code": code, "state": "disabled"}

    def import_application(self, path: str) -> type[BaseApplication]:
        """The application class at ``path`` (``"pkg.module:Class"``); 400 otherwise."""
        module_name, _, class_name = path.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError, ValueError) as missing:
            raise HTTPBadRequest(f"no class at {path!r}: {missing}") from missing
        if not (isinstance(cls, type) and issubclass(cls, BaseApplication)):
            raise HTTPBadRequest(f"{path!r} is not an application class")
        return cls

    @route(auth_rule=SERVER_ADMIN_TAG, openapi_method="post")
    def disable(self, code: str) -> dict[str, str]:
        """Switch the application off: 503 without ``Retry-After``; 400 on ``_server``."""
        server = self.application.server
        self.answer_refusal(server.disable_application, code)
        return {"code": code, "state": "disabled"}

    @route(auth_rule=SERVER_ADMIN_TAG, openapi_method="post")
    def enable(self, code: str) -> dict[str, str]:
        """Switch the application on again; 409 while it is being removed."""
        server = self.application.server
        self.answer_refusal(server.enable_application, code)
        return {"code": code, "state": "enabled"}

    @route(auth_rule=SERVER_ADMIN_TAG, openapi_method="post")
    async def remove(self, code: str, _request=None) -> dict[str, str]:
        """Take the application away; answers when it is gone. 400 on ``_server``.

        Called by the application itself on its own code, it is the
        application giving up.
        """
        server = self.application.server
        app = server.applications.get(code)
        if app is None:
            raise HTTPNotFound(f"no application with code {code!r}")
        if server.is_management(app):
            raise HTTPBadRequest(f"application {code!r} is the management application")
        avatar = _request.avatar()
        failed = avatar is not None and avatar.identity == f"application:{code}"
        await server.remove_live(code, failed=failed)
        return {"code": code, "state": "removed"}

    def answer_refusal(self, change: Any, code: str) -> None:
        """Run ``change(code)``, answering 404, 400 or 409 for its refusals."""
        server = self.application.server
        app = server.applications.get(code)
        if app is None:
            raise HTTPNotFound(f"no application with code {code!r}")
        if server.is_management(app):
            raise HTTPBadRequest(f"application {code!r} is the management application")
        try:
            change(code)
        except ValueError as refused:
            raise HTTPException(409, str(refused)) from refused

    @route(auth_rule=SERVER_ADMIN_TAG)
    def list(self) -> list[dict[str, Any]]:
        """Every application in registration order: ``code``, ``mount``, ``state``, ``reason``."""
        server = self.application.server
        rows = []
        for code, app in server.applications.items():
            state, reason = server.application_state(code)
            rows.append({
                "code": code,
                "mount": app.code if app.mount is None else app.mount,
                "state": state,
                "reason": reason,
            })
        return rows
