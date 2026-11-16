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

"""AuthApplicationBase: the authentication application, mounted at ``/_auth``.

``AuthApplicationBase`` is a ``RoutedApplication`` with the fixed code and
mount ``_auth``. It mounts as is: the server never adds it on its own, a site
declares it. It owns the ``AuthStore`` of users, groups, tags and tokens and
answers every entry point of authentication. A subclass replaces the methods
it wants; ``verify_credential`` is the one that verifies a presented
credential.

Configuration, under the application element (``AuthApplicationGrammar``)::

    auth_app = applications.application(
        app_class=AuthApplicationBase, code="_auth", cache_ttl=300
    )
    auth_app.store(store_class=FileAuthStore, prefix="auth")
    auth_app.login(max_attempts=5, backoff=30)
    auth_app.oidc().provider(
        code="google",
        issuer="https://accounts.example.com",
        client_id="client-123",
        client_secret=EnvResolver("GOOGLE_CLIENT_SECRET"),
    )
    auth_app.self_registration(status="waiting", tags=["GUEST"])

- ``cache_ttl`` — an attribute of the application element: the seconds the
  server keeps an answer of ``authenticate`` and ``avatar`` cached, default
  300; ``0`` disables the cache. The server reads it from the mounted
  application. A hand-built server passes the same value as the ``cache_ttl``
  kwarg.
- ``store(store_class=None, **params)`` — the application builds
  ``store_class(server.storage, **params)``; ``FileAuthStore`` when
  ``store_class`` is omitted. Without the word: ``FileAuthStore(server.storage)``.
  A hand-built server passes the same dict as the ``store`` kwarg. The store
  is built on first use: a server without storage raises ``RuntimeError``.
- ``login(max_attempts, backoff)`` — the lockout policy, defaults 5 attempts
  and 30 seconds. A hand-built server passes the same dict as the ``login``
  kwarg.
- ``oidc()`` with one ``provider(code, issuer, client_id, client_secret,
  scopes)`` per external provider; ``client_secret`` is optional and is
  written as a resolver; ``scopes`` defaults to ``openid email profile``. Each
  provider registers an ``OidcMethod`` with id ``oidc:<code>`` and routes
  ``/_auth/oidc:<code>/start`` and ``/_auth/oidc:<code>/callback`` (see
  ``oidc_method``). Without a provider no OIDC route exists. A hand-built
  server passes ``oidc={code: {issuer, client_id, ...}}``. A provider declared
  on a server without ``external_url`` raises ``ValueError`` when the server
  takes the application: the callback url handed to the provider is absolute.
- ``self_registration(status, tags)`` — what happens to a user an external
  provider recognises and the store does not have: a record is written with
  that ``status`` and those ``tags``. One setting for every provider. Without
  the word such a login is refused. A setting whose ``status`` is not a user
  status is not applied: self registration stays off and a warning is logged.
  A hand-built server passes the same dict as the ``self_registration`` kwarg.

Credentials accepted: a password, through ``/_auth/login`` or an
``Authorization: Basic`` header, and a ``gak_`` token as
``Authorization: Bearer``. Nothing else.

Routes called by the server, reachable only through the KajennBus (an HTTP
caller gets 403):

- ``authenticate(credential, channel)`` — answers
  ``{identity, group, tags, data, valid_until}`` from ``verify_credential``;
  an invalid credential raises ``HTTPUnauthorized`` (401);
- ``forget_credential(credential)`` — drops the cached avatars of
  ``credential`` in the server and in every joined process;
  answers ``{"status": "ok"}``;
- ``avatar(identity, group)`` — answers ``{identity, group, tags, data,
  valid_until}`` for an identity and a group; an empty ``group`` means the
  user's primary group. An unknown identity, a user whose status does not
  authenticate and a group the user does not belong to raise
  ``HTTPUnauthorized``.

Routes called by a client:

- ``login(identity, password, group)`` — verifies the password and attaches
  the ``Avatar`` to the request's session; the session id does not change.
  ``group`` is optional: empty means the user's primary group. Success answers
  ``{session_id, identity, group, tags, groups}``, ``groups`` being every group
  of the user. A refusal answers ``{"error": ...}`` and attaches nothing:
  missing identity or password, wrong credentials, an active lockout, a group
  the user does not belong to;
- ``logout(session_id)`` — deletes the session; answers ``{"status": "ok"}``;
- ``login_methods`` — public: ``{"methods": [descriptor, ...]}`` from the
  registry of login methods: ``PasswordMethod`` first, then one ``OidcMethod``
  per declared provider.

Management, every route asking ``AUTH_ADMIN``: ``/_auth/users/...``,
``/_auth/groups/...``, ``/_auth/tags/...``, ``/_auth/tokens/...`` (see
``auth_sections``).

``group`` in every answer is the active group: the one asked for, else the
user's primary group, ``None`` when the user has none. The tags are the
user's own tags plus those of the active group (``AuthStore.get_user_tags``);
for a token they come from ``AuthStore.verify_token``.

Lockout: a password check through ``login`` or a ``Basic`` header shares one
counter on the user record (``failed_attempts``, ``last_failed_at``). After
``max_attempts`` consecutive failures the identity is refused for
``backoff * 2**(failed_attempts - max_attempts)`` seconds from the last
failure; a refused attempt does not touch the counter; a success resets it.
An identity without a record has no counter.

Handlers that need the live request declare an unannotated ``_request``
parameter, injected per dispatch.
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Any, ClassVar

from genro_bag import BagResolver
from genro_builders.builder import element
from genro_routes import RoutingClass, route

from kajenn.application import ApplicationGrammar
from kajenn.auth.mixin import AUTHENTICATION_CODE, DEFAULT_CREDENTIAL_CACHE_TTL
from kajenn.exceptions import HTTPForbidden, HTTPUnauthorized
from kajenn.routed_application import RoutedApplication
from kajenn.session import Avatar

from .auth_method import AuthMethod, AuthMethodRegistry, PasswordMethod
from .auth_sections import GroupsSection, TagsSection, TokensSection, UsersSection
from .auth_store import AUTHENTICATING_STATUS, USER_STATUSES, AuthStore, FileAuthStore
from .oidc_method import OidcMethod

__all__ = ["AuthApplicationBase", "AuthApplicationGrammar"]

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_BACKOFF_SECONDS = 30.0
CHALLENGE = [(b"www-authenticate", b"Bearer")]


class AuthApplicationGrammar(ApplicationGrammar):
    """The words the ``_auth`` application adds to a recipe, under its own element."""

    @element(sub_tags="", node_label="store")
    def store(self, store_class: type = None, **params: Any) -> None:
        """The AuthStore: ``store_class`` (``FileAuthStore`` when omitted) and its own kwargs."""

    @element(sub_tags="", node_label="login")
    def login(self, max_attempts: int = None, backoff: float = None) -> None:
        """Lockout policy of the password check: ``max_attempts`` and ``backoff`` seconds."""

    @element(sub_tags="provider", collection_key="code", node_label="oidc")
    def oidc(self) -> None:
        """Collection of the external OIDC providers, each labelled by its ``code``."""

    @element(parent_tags="oidc", sub_tags="")
    def provider(
        self,
        code: str,
        issuer: str = None,
        client_id: str = None,
        client_secret: str | BagResolver = None,
        scopes: str = "openid email profile",
    ) -> None:
        """One OIDC provider: ``code`` (required), ``issuer``, ``client_id``,
        optional ``client_secret`` and ``scopes``."""

    @element(sub_tags="", node_label="self_registration")
    def self_registration(self, status: str = None, tags: list = None) -> None:
        """Self registration of a user unknown to the store: birth ``status`` and ``tags``."""


class AuthApplicationBase(RoutedApplication):
    """The authentication application, mounted at ``/_auth`` (see module docstring)."""

    openapi_info: ClassVar[dict[str, Any]] = {
        "title": "kajenn authentication endpoints",
        "version": "1.0.0",
    }
    code = AUTHENTICATION_CODE
    mount = AUTHENTICATION_CODE
    grammar: type = AuthApplicationGrammar

    def __init__(self, **kwargs: Any) -> None:
        self._cache_ttl: float = kwargs.pop("cache_ttl", DEFAULT_CREDENTIAL_CACHE_TTL)
        self._store_options: dict[str, Any] = kwargs.pop("store", {})
        self._login_policy: dict[str, Any] = kwargs.pop("login", {})
        self._oidc_providers: dict[str, dict[str, Any]] = kwargs.pop("oidc", {})
        self._self_registration: dict[str, Any] = {}
        self.self_registration = kwargs.pop("self_registration", {})
        self._store: AuthStore | None = None
        self._sections: dict[str, RoutingClass] = {}
        super().__init__(**kwargs)
        self._auth_method_registry = AuthMethodRegistry(self)
        self.register_auth_method(PasswordMethod(self, "password"))
        for code, provider in self.oidc_providers.items():
            self.register_auth_method(OidcMethod(self, f"oidc:{code}", code, provider))
        self.attach_section(UsersSection(self), name="users")
        self.attach_section(GroupsSection(self), name="groups")
        self.attach_section(TagsSection(self), name="tags")
        self.attach_section(TokensSection(self), name="tokens")

    @RoutedApplication.server.setter
    def server(self, value: Any) -> None:
        """Read the words of the recipe, take ownership, then check ``external_url``.

        The recipe is read before ownership: an OIDC method it declares joins
        the router while the router has no server to arm its plugins from.

        Raises:
            ValueError: An OIDC provider is declared and the server has no
                ``external_url``.
        """
        handler = getattr(value, "config", None)
        if handler is not None:
            self.read_declared_configuration(handler)
        RoutedApplication.server.fset(self, value)
        if self.oidc_providers and self.server.external_url is None:
            codes = ", ".join(sorted(self.oidc_providers))
            raise ValueError(
                f"application {self.code!r}: oidc provider(s) {codes} declared but the "
                "server has no external_url, needed for the absolute redirect_uri"
            )

    def read_declared_configuration(self, handler: Any) -> None:
        """Fold the recipe's ``store``, ``login``, ``oidc`` and ``self_registration`` nodes.

        ``handler`` is the configuration handler of the server taking the
        application. Each provider found is added to ``oidc_providers`` and
        registered as an ``OidcMethod``.
        """
        section = f"applications.{self.code}"
        store_node = handler.node(f"{section}.store")
        if store_node is not None:
            self._store_options = handler.open_attrs(store_node)
        if handler.node(f"{section}.login") is not None:
            self._login_policy = handler.closed_attrs(f"{section}.login", "max_attempts", "backoff")
        if handler.node(f"{section}.self_registration") is not None:
            self.self_registration = handler.closed_attrs(
                f"{section}.self_registration", "status", "tags"
            )
        oidc_node = handler.node(f"{section}.oidc")
        if oidc_node is None:
            return
        for child in oidc_node.value:
            provider = handler.closed_attrs(
                f"{section}.oidc.{child.label}",
                "issuer",
                "client_id",
                "client_secret",
                "scopes",
            )
            self.oidc_providers[child.label] = provider
            self.register_auth_method(
                OidcMethod(self, f"oidc:{child.label}", child.label, provider)
            )

    # ── store, policy, registry, sections ──────────────────────────────

    @property
    def store(self) -> AuthStore:
        """The AuthStore, built from the ``store`` options on first use."""
        if self._store is None:
            self._store = self.build_store()
        return self._store

    def build_store(self) -> AuthStore:
        """Build ``store_class(server.storage, **params)``; ``FileAuthStore`` by default.

        Raises:
            RuntimeError: The owning server has no storage.
        """
        storage = getattr(self.server, "storage", None)
        if storage is None:
            raise RuntimeError(f"application {self.code!r}: the store needs the server storage")
        options = dict(self.store_options)
        store_class = options.pop("store_class", None) or FileAuthStore
        built: AuthStore = store_class(storage, **options)
        return built

    @property
    def cache_ttl(self) -> float:
        """Seconds the server keeps an answer cached; ``0`` disables the cache."""
        return self._cache_ttl

    @property
    def store_options(self) -> dict[str, Any]:
        """The ``store`` options: ``store_class`` and its kwargs (may be empty)."""
        return self._store_options

    @property
    def login_policy(self) -> dict[str, Any]:
        """The lockout policy from the ``login`` word (may be empty)."""
        return self._login_policy

    @property
    def oidc_providers(self) -> dict[str, dict[str, Any]]:
        """The declared OIDC providers, keyed by code (may be empty)."""
        return self._oidc_providers

    @property
    def self_registration(self) -> dict[str, Any]:
        """The ``self_registration`` setting: ``status`` and ``tags``; empty when absent."""
        return self._self_registration

    @self_registration.setter
    def self_registration(self, setting: dict[str, Any]) -> None:
        """Take the setting. One whose ``status`` is not a user status is not applied.

        The refused setting leaves self registration off and a warning in the log.
        """
        if setting and setting.get("status") not in USER_STATUSES:
            logging.getLogger(__name__).warning(
                "application %s: self_registration not applied, status %r is not one of %s",
                self.code,
                setting.get("status"),
                ", ".join(USER_STATUSES),
            )
            self._self_registration = {}
            return
        self._self_registration = setting

    @property
    def auth_method_registry(self) -> AuthMethodRegistry:
        """The registry of the login methods."""
        return self._auth_method_registry

    def register_auth_method(self, method: AuthMethod) -> None:
        """Register a login method; its routes, if any, live at ``/_auth/<method_id>/``."""
        self.auth_method_registry.register(method)

    @property
    def sections(self) -> dict[str, RoutingClass]:
        """Attached management sections keyed by their mount segment."""
        return self._sections

    def attach_section(self, section: RoutingClass, name: str) -> None:
        """Attach ``section`` under ``name`` (endpoints at ``/_auth/<name>/...``)."""
        self.route.add_branches({"name": name, "instance": section})
        self.sections[name] = section

    # ── verification ───────────────────────────────────────────────────

    def verify_credential(self, credential: str) -> dict[str, Any]:
        """Verify an ``Authorization`` value (``<scheme> <value>``).

        ``Basic`` is checked on the store's users with the lockout rule;
        ``Bearer gak_...`` with ``AuthStore.verify_token``.

        Returns:
            ``{identity, group, tags, data, valid_until}``.

        Raises:
            HTTPUnauthorized: A malformed, unknown or invalid credential.
        """
        scheme, separator, value = credential.partition(" ")
        if not separator:
            raise HTTPUnauthorized("Malformed Authorization header", headers=CHALLENGE)
        if scheme.lower() == "basic":
            return self._verify_basic(value)
        if scheme.lower() == "bearer":
            return self._verify_bearer(value)
        raise HTTPUnauthorized("Invalid or expired credentials", headers=CHALLENGE)

    def _verify_basic(self, value: str) -> dict[str, Any]:
        """The answer for a ``Basic`` value: password check with the lockout rule."""
        try:
            decoded = base64.b64decode(value, validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            raise HTTPUnauthorized("Malformed Basic credentials", headers=CHALLENGE) from None
        identity, separator, password = decoded.partition(":")
        if not separator or not identity:
            raise HTTPUnauthorized("Malformed Basic credentials", headers=CHALLENGE)
        user, refusal = self._verify_login(identity, password)
        if user is None:
            raise HTTPUnauthorized(refusal["error"], headers=CHALLENGE)
        return self._avatar_answer(user["identity"], None)

    def _verify_bearer(self, value: str) -> dict[str, Any]:
        """The answer for a ``Bearer`` value: only a valid ``gak_`` token."""
        verified = self.store.verify_token(value)
        if verified is None:
            raise HTTPUnauthorized("Invalid or expired credentials", headers=CHALLENGE)
        return {
            "identity": verified["identity"],
            "group": verified["group"],
            "tags": verified["tags"],
            "data": {},
            "valid_until": verified["expires_at"],
        }

    def _avatar_answer(self, identity: str, group: str | None) -> dict[str, Any]:
        """``{identity, group, tags, data, valid_until}`` of a confirmed user in ``group``.

        ``group=None`` means the user's primary group. Raises
        ``HTTPUnauthorized`` for an unknown identity, a status that does not
        authenticate, or a group the user does not belong to.
        """
        user = self.store.get_user(identity)
        if user is None or user.get("status") != AUTHENTICATING_STATUS:
            raise HTTPUnauthorized("Unknown or disabled identity", headers=CHALLENGE)
        try:
            tags = self.store.get_user_tags(identity, group)
        except ValueError as refused:
            raise HTTPUnauthorized(str(refused), headers=CHALLENGE) from None
        return {
            "identity": identity,
            "group": group if group is not None else user.get("primary_group"),
            "tags": tags,
            "data": {},
            "valid_until": None,
        }

    def _verify_login(
        self, identity: str, password: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Check a password under the lockout rule: ``(user, {})`` or ``(None, refusal)``.

        The refusal is the ``{"error": ...}`` shape, with ``remaining_attempts``
        when the identity has a record. A refused attempt during a lockout
        leaves the counter as it is; a failure counts; a success resets.
        """
        record = self.store.get_user(identity)
        if record is not None and self._lock_seconds_remaining(record) > 0:
            return None, {"error": "Too many failed attempts"}
        verified = self.store.verify_password(identity, password)
        if verified is None:
            if record is None:
                return None, {"error": "Invalid credentials"}
            record["failed_attempts"] = record.get("failed_attempts", 0) + 1
            record["last_failed_at"] = time.time()
            self.store.save_user(record)
            max_attempts = self.login_policy.get("max_attempts", LOCKOUT_MAX_ATTEMPTS)
            remaining = max(0, max_attempts - record["failed_attempts"])
            return None, {"error": "Invalid credentials", "remaining_attempts": remaining}
        if verified.get("failed_attempts"):
            verified["failed_attempts"] = 0
            verified["last_failed_at"] = 0.0
            self.store.save_user(verified)
        return verified, {}

    def _lock_seconds_remaining(self, record: dict[str, Any]) -> float:
        """Seconds left in ``record``'s lockout window, ``0.0`` when not locked."""
        policy = self.login_policy
        failed = record.get("failed_attempts", 0)
        max_attempts = policy.get("max_attempts", LOCKOUT_MAX_ATTEMPTS)
        if failed < max_attempts:
            return 0.0
        backoff = policy.get("backoff", LOCKOUT_BACKOFF_SECONDS)
        window = backoff * 2 ** (failed - max_attempts)
        remaining: float = max(0.0, record.get("last_failed_at", 0.0) + window - time.time())
        return remaining

    def _bus_only(self, request: Any) -> None:
        """Refuse a caller that did not come through the KajennBus (403)."""
        if request is None or not request.scope.get("kajenn.kbus"):
            raise HTTPForbidden("this route serves the KajennBus only")

    # ── routes called by the server, bus only ──────────────────────────

    @route(media_type="application/json", openapi_method="post")
    def authenticate(
        self, credential: str = "", channel: str = "", _request=None
    ) -> dict[str, Any]:
        """Verify ``credential`` with ``verify_credential``. Bus only.

        Note:
            Route: POST /_auth/authenticate
        """
        self._bus_only(_request)
        return self.verify_credential(credential)

    @route(media_type="application/json", openapi_method="post")
    async def forget_credential(self, credential: str = "", _request=None) -> dict[str, Any]:
        """Drop the cached avatars of ``credential`` everywhere. Bus only.

        Note:
            Route: POST /_auth/forget_credential
        """
        self._bus_only(_request)
        await self.server.forget_credentials_everywhere(credential)
        return {"status": "ok"}

    @route(media_type="application/json", openapi_method="post")
    def avatar(self, identity: str = "", group: str = "", _request=None) -> dict[str, Any]:
        """The avatar answer of ``identity`` in ``group`` (empty: primary group). Bus only.

        Note:
            Route: POST /_auth/avatar
        """
        self._bus_only(_request)
        return self._avatar_answer(identity, group or None)

    # ── routes called by a client ──────────────────────────────────────

    @route(media_type="application/json", openapi_method="post")
    def login(
        self, identity: str = "", password: str = "", group: str = "", _request=None
    ) -> dict[str, Any]:
        """Verify the password and attach the avatar to the request's session.

        Note:
            Route: POST /_auth/login
        """
        if not identity or not password:
            return {"error": "Identity and password are required"}
        user, refusal = self._verify_login(identity, password)
        if user is None:
            return refusal
        active_group = group or None
        try:
            tags = self.store.get_user_tags(identity, active_group)
        except ValueError as refused:
            return {"error": str(refused)}
        if active_group is None:
            active_group = user.get("primary_group")
        avatar = Avatar(identity, tags, group=active_group)
        session = _request.session
        session.attach_avatar(avatar)
        return {
            "session_id": session.id,
            "identity": avatar.identity,
            "group": avatar.group,
            "tags": avatar.tags,
            "groups": list(user.get("groups", [])),
        }

    @route(media_type="application/json")
    def logout(self, session_id: str = "") -> dict[str, Any]:
        """Delete a session; no error when it is unknown.

        Note:
            Route: POST /_auth/logout
        """
        if session_id:
            self.server.session_store.delete(session_id)
        return {"status": "ok"}

    @route(media_type="application/json")
    def login_methods(self) -> dict[str, Any]:
        """Public descriptors of the registered login methods.

        Note:
            Route: GET /_auth/login_methods
        """
        return {"methods": self.auth_method_registry.descriptor_list}
