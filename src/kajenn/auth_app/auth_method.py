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

"""Login methods of the ``_auth`` application and their registry.

A login method is one way a user proves who they are. Each method is a
``RoutingClass`` that describes itself: ``descriptor()`` is the small dict a
login page renders it from (an id, a ``kind``, a label, where to post or
navigate).

The contract::

    AuthMethod (RoutingClass):
        method_id: str          # "password", ...
        kind: str               # "form" | "redirect" | "ceremony"
        env_requires: str       # capability its routes require; "" for none
        descriptor() -> dict    # what the login page renders it from

    AuthMethodRegistry:
        methods -> dict[str, AuthMethod]      # property, registration order
        descriptor_list -> list[dict]         # property, registration order
        register(method) -> None              # ValueError on a repeated method_id

``kind`` tells the page how to draw the method: ``form`` renders the
identity/password form, ``redirect`` renders one button navigating to the
method's entry URL, ``ceremony`` names a client-driven exchange.

``env_requires`` names the server capability the method's routes require
(``ServerCapabilities``). ``descriptor_list`` leaves out a method whose
capability is off, so the login page never shows a method whose routes
answer 404.

``PasswordMethod`` is the ``form`` method: it owns no routes and posts to
``/_auth/login``. The registry attaches a method under
``/_auth/<method_id>/`` only when the method owns routes; a method without
routes is recorded and never enters the routing tree. Every method converges
on the same outcome: the avatar attached to the request's session through
``request.session.attach_avatar``.
"""

from __future__ import annotations

from typing import Any

from genro_routes import RoutingClass

__all__ = ["AuthMethod", "AuthMethodRegistry", "PasswordMethod"]


class AuthMethod(RoutingClass):
    """Base contract for a login method of the ``_auth`` application.

    A concrete method sets ``kind``, extends ``descriptor()`` with what its
    kind needs, and adds its own routes with ``@route`` when it has any. The
    application is ``self.application`` (dual relationship).
    """

    #: The method kind: "form" | "redirect" | "ceremony". A concrete method sets it.
    kind: str = ""

    #: The capability the method's routes require; "" when they require none.
    env_requires: str = ""

    def __init__(self, application: Any, method_id: str) -> None:
        """Bind the method to its application and fix its id."""
        self.application = application
        self._method_id = method_id

    @property
    def method_id(self) -> str:
        """The method's identity, also its mount name under ``/_auth``."""
        return self._method_id

    def descriptor(self) -> dict[str, Any]:
        """The dict the login page renders this method from: ``id`` and ``kind``."""
        return {"id": self.method_id, "kind": self.kind}


class PasswordMethod(AuthMethod):
    """The password method: a ``form`` that posts identity and password to ``/_auth/login``.

    It owns no routes; the credential check lives on the application's
    ``login`` route.
    """

    kind = "form"

    def descriptor(self) -> dict[str, Any]:
        """Descriptor for the password form: adds ``label`` and the ``action`` it posts to."""
        return {
            **super().descriptor(),
            "label": "Sign in",
            "action": f"/{self.application.mount}/login",
        }


class AuthMethodRegistry:
    """The ordered registry of the login methods of one ``_auth`` application.

    Note:
        Parent (dual relationship): the application, stored as
        ``self.application``; a method that owns routes is attached to its
        router.
    """

    def __init__(self, application: Any) -> None:
        """Bind the registry to its application and start it empty."""
        self.application = application
        self._methods: dict[str, AuthMethod] = {}

    @property
    def methods(self) -> dict[str, AuthMethod]:
        """The registered methods, keyed by ``method_id``, in registration order."""
        return self._methods

    @property
    def descriptor_list(self) -> list[dict[str, Any]]:
        """The descriptor of every available method, in registration order.

        A method is available when it requires no capability or when the
        application's capabilities hold the one it requires.
        """
        capabilities = self.application.capabilities
        return [
            method.descriptor()
            for method in self.methods.values()
            if not method.env_requires or method.env_requires in capabilities
        ]

    def register(self, method: AuthMethod) -> None:
        """Record ``method``; attach it under ``method_id`` only when it owns routes.

        Raises:
            ValueError: A method with the same ``method_id`` is already registered.
        """
        method_id = method.method_id
        if method_id in self.methods:
            raise ValueError(f"auth method already registered: {method_id}")
        tree = method.route.nodes(lazy=True, forbidden=True)
        if tree.get("entries") or tree.get("routers"):
            self.application.route.add_branches({"name": method_id, "instance": method})
        self._methods[method_id] = method
