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

    ``POST /_server/applications/ready``  the application ``code`` is in working state
    ``GET  /_server/applications/list``   every application, its mount and its state

An application other than ``_server`` is registered disabled and answers 503
with ``Retry-After`` until ``ready`` names it. The server sends ``ready``
itself, over the KajennBus, when the application's ``on_startup`` returns.

Every route is gated ``auth_rule="SERVER_ADMIN"``: an application calling on
the bus carries that tag, an administrator calls with it.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from kajenn.exceptions import HTTPNotFound

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
        server = self.application.server
        try:
            server.enable_application(code)
        except LookupError as missing:
            raise HTTPNotFound(str(missing)) from missing
        return {"code": code, "state": "enabled"}

    @route(auth_rule=SERVER_ADMIN_TAG)
    def list(self) -> list[dict[str, Any]]:
        """Every application in registration order: ``code``, ``mount`` and ``state``."""
        server = self.application.server
        return [
            {
                "code": code,
                "mount": app.code if app.mount is None else app.mount,
                "state": "enabled" if server.is_enabled(code) else "disabled",
            }
            for code, app in server.applications.items()
        ]
