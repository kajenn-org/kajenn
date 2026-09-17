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

"""Auth methods — self-describing login mechanisms mounted under ``_server/auth``.

An auth method is one way a user proves who they are: a password form today,
an OIDC redirect to an external provider in the next wave. Each method is a
``RoutingClass``; the ``AuthSection`` attaches it under
``_server/auth/<method_id>`` ONLY when it owns routes (OIDC will own
``start`` + ``callback``). The password method owns none — it adapts the
existing ``/_server/login`` — so it is never attached: it lives only in the
section's registry (Invariant 10: zero-route nodes never enter the routing
tree). The method is also self-describing: ``descriptor()`` is the small dict
the login page renders it from (an id, a ``kind``, a label, the entry URL for
redirect methods).

The contract::

    AuthMethod (RoutingClass):
        method_id: str          # "password", "oidc:google", ...
        kind: str               # "form" | "redirect" | "ceremony"
        descriptor() -> dict    # what the login page renders it from

``kind`` tells the page how to draw the method: ``form`` renders the
identity/password form (the password method), ``redirect`` renders one button
navigating to the method's entry URL (OIDC), ``ceremony`` is a client-driven
exchange (passkey, a later wave). Every method converges on the SAME success
outcome: the avatar attached to the request's session through
``request.session.attach_avatar`` — the session id never changes at login, so
no cookie is involved and no method or handler ever sets one.

``safe_next_path`` is the shared open-redirect guard for the login ``next``
parameter: the login page mirrors it client-side today; the challenge
redirect (ErrorMiddleware, next phase) will apply it server-side, so one
rule governs every path.
"""

from __future__ import annotations

from typing import Any

from genro_routes import RoutingClass

__all__ = ["AuthMethod", "PasswordMethod", "safe_next_path"]


def safe_next_path(value: str | None, default: str = "/") -> str:
    """Return a login ``next`` target that is safe to redirect to, or ``default``.

    The login flow carries a ``next`` parameter through the challenge and back;
    an attacker who can shape it into an off-site URL turns the login page into
    an open redirect. The single guard keeps only a same-origin relative path:
    it must start with a single ``/`` and must not be a scheme-relative URL
    (``//host``) nor carry a backslash a browser would normalize to ``/``.
    Anything else (an absolute URL, ``javascript:``, an empty value, a bare
    path without the leading slash) collapses to ``default``.
    """
    if not value:
        return default
    if not value.startswith("/"):
        return default
    if value.startswith("//"):
        return default
    if "\\" in value:
        return default
    return value


class AuthMethod(RoutingClass):
    """Base contract for a login method attached under ``_server/auth/<id>``.

    A concrete method sets ``kind`` and (usually) ``method_id``, overrides
    ``descriptor()`` to add what its ``kind`` needs (a label, an entry URL),
    and adds its own routes with ``@route`` when it has any (OIDC does;
    password does not — a route-less method is recorded in the AuthSection
    registry but never attached to the routing tree, Invariant 10). The
    ServerApplication that owns the login surface is reached through
    ``self.application`` (dual relationship), the AsgiServer through
    ``self.application.server``.
    """

    #: The method kind: "form" | "redirect" | "ceremony". A concrete method sets it.
    kind: str = ""

    def __init__(self, application: Any, method_id: str) -> None:
        """Bind the method to its login surface and fix its id.

        Args:
            application: The ServerApplication the login surface belongs to
                (dual relationship). The AsgiServer is ``application.server``.
            method_id: The method's identity and its mount name under
                ``_server/auth`` (e.g. ``"password"``, ``"oidc:google"``).
        """
        self.application = application
        self._method_id = method_id

    @property
    def method_id(self) -> str:
        """The method's identity, also its mount name under ``_server/auth``."""
        return self._method_id

    @property
    def server(self) -> Any:
        """The AsgiServer, reached through the parent ServerApplication."""
        return self.application.server

    def descriptor(self) -> dict[str, Any]:
        """The dict the login page renders this method from.

        The base carries the two facts every method has — its ``id`` and its
        ``kind``; a concrete method adds what its kind needs (a ``label``, a
        ``url`` for redirect methods) by extending this dict.
        """
        return {"id": self.method_id, "kind": self.kind}


class PasswordMethod(AuthMethod):
    """The password method: a thin adapter over ``/_server/login``.

    Owns no routes and is therefore never attached to the routing tree
    (Invariant 10) — it lives only in the AuthSection registry. The credential
    check and session promotion live on the ServerApplication's
    ``/_server/login`` route (store-backed via the UserStore). This method only
    declares itself to the login page as a ``form`` so the page renders the
    identity/password form and posts it there.
    """

    kind = "form"

    def descriptor(self) -> dict[str, Any]:
        """Descriptor for the password form: adds where the page posts.

        The page posts credentials to the existing ``/_server/login`` JSON
        route (``action``); ``label`` names the form for the page.
        """
        return {**super().descriptor(), "label": "Sign in", "action": "/_server/login"}
