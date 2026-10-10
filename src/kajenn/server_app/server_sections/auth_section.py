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

"""The ``_server/auth`` container: the mount that holds the auth-method sections.

When the login surface is active the ``ServerApplication`` attaches ONE
``AuthSection`` under the ``auth`` name, so it lives at ``/_server/auth/``.
A registered auth method (``AuthMethod``) is attached to this section under
its ``method_id`` ONLY when it owns routes, so those routes live at
``/_server/auth/<method_id>/`` (the OIDC method's ``start`` and ``callback``,
e.g. ``/_server/auth/oidc:google/start``). A route-less method — the password
one — is recorded in the registry but never attached: zero-route nodes never
enter the routing tree (Invariant 10).

The section carries the routed method children, keeps the ordered registry
the login surface reads to build ``login_methods``, and owns two public
routes: ``authenticate`` (the default ``authentication_route`` of every
channel, backed by ``AuthCore.verify``) and ``forget_credential`` (evicts a
cached credential). Routing is dispatch; the registry is this dict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from kajenn.exceptions import HTTPForbidden

if TYPE_CHECKING:
    from ..auth_method import AuthMethod
    from ..server_app import ServerApplication

__all__ = ["AuthSection"]


class AuthSection(RoutingClass):
    """The ``_server/auth`` mount that carries the registered auth methods.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``. The AsgiServer is reached via
        ``self.application.server``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication and start an empty registry.

        Args:
            application: The ServerApplication this section belongs to
                (dual relationship). The AsgiServer is ``application.server``.
        """
        self.application = application
        self._methods: dict[str, AuthMethod] = {}

    @property
    def server(self) -> Any:
        """The AsgiServer, reached through the parent ServerApplication."""
        return self.application.server

    @property
    def methods(self) -> dict[str, AuthMethod]:
        """The registered methods, keyed by ``method_id`` (insertion order)."""
        return self._methods

    def register(self, method: AuthMethod) -> None:
        """Record a method; mount its routes only when it owns some.

        Every method enters the ordered registry the login surface reads to
        build ``login_methods``. Only a method that OWNS routes is also linked
        into this section's router under ``method_id`` (so its routes live at
        ``/_server/auth/<method_id>/``); a route-less method (the password one)
        stays registry-only — zero-route nodes are never attached to the
        routing tree (Invariant 10: routing is dispatch, never a registry).

        Args:
            method: The AuthMethod to register. Its ``method_id`` must be unique.

        Raises:
            ValueError: If a method with the same ``method_id`` is already
                registered (method ids are unique by contract, so a clash is a
                configuration bug).
        """
        method_id = method.method_id
        if method_id in self.methods:
            raise ValueError(f"auth method already registered: {method_id}")
        tree = method.route.nodes(lazy=True, forbidden=True)
        if tree.get("entries") or tree.get("routers"):
            self.route.add_branches({"name": method_id, "instance": method})
        self._methods[method_id] = method

    def descriptors(self) -> list[dict[str, Any]]:
        """The descriptor of every registered method, in registration order."""
        return [method.descriptor() for method in self.methods.values()]

    @staticmethod
    def _bus_only(request: Any) -> None:
        """Refuse a caller that did not come through the KajennBus (403).

        The two routes below serve ``server.authenticate_credential`` and the
        applications that call them with ``kbus_call``; over HTTP they would
        let anyone verify a credential or evict a cache entry.
        """
        if request is None or not request.scope.get("kajenn.kbus"):
            raise HTTPForbidden("this route serves the KajennBus only")

    @route(media_type="application/json", openapi_method="post")
    def authenticate(
        self, credential: str = "", channel: str = "", _request=None
    ) -> dict[str, Any]:
        """Verify ``credential`` with the server's ``AuthCore``.

        Returns ``{identity, tags, data}``; an invalid credential raises
        ``HTTPUnauthorized`` (status 401 for a bus caller). Reachable through
        the bus only: an HTTP caller gets 403.

        Note:
            Route: POST /_server/auth/authenticate
        """
        self._bus_only(_request)
        avatar = self.server.auth_core.verify(credential)
        return {"identity": avatar.identity, "tags": avatar.tags, "data": {}}

    @route(media_type="application/json", openapi_method="post")
    def forget_credential(self, credential: str = "", _request=None) -> dict[str, Any]:
        """Drop the cached avatars of ``credential``. Bus only, like ``authenticate``.

        Note:
            Route: POST /_server/auth/forget_credential
        """
        self._bus_only(_request)
        self.server.forget_credential(credential)
        return {"status": "ok"}
