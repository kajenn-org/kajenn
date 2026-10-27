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

"""The ``_server/databases`` section: the declared databases and their migration.

``DatabasesSection`` is a ``RoutingClass`` the ``ServerApplication`` attaches
under ``databases``:

    ``/_server/databases/list``           every database, its owner, handler and capabilities
    ``/_server/databases/plan?name=..``   the handler's ``migration_plan(name)``
    ``/_server/databases/apply?name=..``  the handler's ``migrate(name)``

Every route is gated ``auth_rule="SERVER_ADMIN"``. The section reads the
server's database registry and calls only the ``DbHandlerBase`` contract: the
core imports no backend. Migration is never run by the server on its own: these
routes and ``kajenn db plan|apply`` are the two ways an operator launches it.

``plan`` and ``apply`` are sync routes, so the dispatch runs them on the pool
(``server.run_sync``): both handler operations are sync and open a connection
of their own. The answer is ``{"name": name, "result": <what the handler
returned>}``: handlers return different shapes (a dict, a list, ``None``), and
the envelope keeps every answer a JSON object. ``name`` is required. An unknown
name is a 404; a known database whose handler does not offer the operation is a
422 — the capability belongs to the database ``name`` selects, not to the route.
Both refusals come from ``server.database_handler``, which the CLI shares.

Parent (dual relationship): the ServerApplication, stored as
``self.application``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from kajenn.exceptions import HTTPNotFound, HTTPUnprocessableContent

if TYPE_CHECKING:
    from ..server_app import ServerApplication

__all__ = ["DatabasesSection"]

DATABASES_RULE = "SERVER_ADMIN"


class DatabasesSection(RoutingClass):
    """The ``_server/databases`` mount: the registry and the migration operations.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``. The server is ``self.application.server``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application

    def capable_handler(self, name: str, capability: str) -> Any:
        """``server.database_handler`` with its refusals as HTTP errors: 404, 422."""
        try:
            return self.application.server.database_handler(name, capability)
        except LookupError as error:
            raise HTTPNotFound(str(error)) from error
        except ValueError as error:
            raise HTTPUnprocessableContent(str(error)) from error

    @route(media_type="application/json", auth_rule=DATABASES_RULE)
    def list(self) -> list[dict[str, Any]]:
        """Every declared database, in registration order.

        ``owner`` follows the name rule: ``None`` for a server database
        (``foo``), the application code for ``chat:foo``. ``handler_class`` is
        the qualified class name.

        Note:
            Route: GET /_server/databases/list
        """
        databases = []
        for name, handler in self.application.server.databases.items():
            owner, _, _ = name.rpartition(":")
            handler_class = type(handler)
            databases.append(
                {
                    "name": name,
                    "owner": owner or None,
                    "handler_class": f"{handler_class.__module__}.{handler_class.__qualname__}",
                    "capabilities": sorted(handler.capabilities),
                }
            )
        return databases

    @route(media_type="application/json", auth_rule=DATABASES_RULE)
    def plan(self, name: str) -> dict[str, Any]:
        """What ``apply`` would do to database ``name``; changes nothing.

        Note:
            Route: GET /_server/databases/plan?name=<name>
        """
        handler = self.capable_handler(name, "migration_plan")
        return {"name": name, "result": handler.migration_plan(name)}

    @route(media_type="application/json", auth_rule=DATABASES_RULE, openapi_method="post")
    def apply(self, name: str) -> dict[str, Any]:
        """Bring database ``name`` to its model through its handler.

        Note:
            Route: POST /_server/databases/apply?name=<name>
        """
        handler = self.capable_handler(name, "migrate")
        return {"name": name, "result": handler.migrate(name)}
