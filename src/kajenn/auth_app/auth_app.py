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
``auth_sections``). Assigning a tag that has no record is refused.

Declared records: at startup the application reads ``declared_tags`` and
``system_users`` of every application of the server, itself included
(``AUTH_ADMIN``), and creates the records that do not exist
(``create_declared_records``). It does the same for an application written in
the live configuration later, and the server asks it for an application with
a ``spawner`` when its process joins. A tag record has the code as declared
and ``owner`` the application code; a system user has ``system`` true and no
token. An existing record is left as it is; a record
whose declaration disappears stays. A declaration comes from an application,
for one with a ``spawner`` across the ``joined`` call: an entry with a wrong
type, or a tag code an auth rule cannot hold, is not written and a warning is
logged.

Temporary administrator: the identity ``_temporary_admin``
(``TEMPORARY_ADMIN``), the way into a site that has no administrator. At
startup, after the declared records, the application looks for an
administrator: a user other than ``_temporary_admin``, with status
``confirmed`` and ``system`` false, that holds ``AUTH_ADMIN`` among its own
tags or among the tags of any of its groups. Without one it writes the record
of ``_temporary_admin`` (tags ``["AUTH_ADMIN"]``, status ``confirmed``,
``system`` false, no group, no email, no external id) with a password
generated at every startup, and logs one warning line that carries the
identity and the password. The password is stored only as its hash; no other
line, answer or record carries it. A restart in which the record still exists
writes it again with a new password: the password of the previous log stops
working. With an administrator the record is deleted and no password is
generated. After every write of the ``users`` and ``groups`` management
sections the application looks again (``remove_temporary_admin``): when an
administrator exists it deletes the record and empties the credential cache
of the server and of every joined process, so the password, the sessions and
the tokens of ``_temporary_admin`` stop working at once. Otherwise the
management treats ``_temporary_admin`` as any user. A store that cannot be
built or written at startup writes nothing and a warning is logged, as for the
declared records.

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

import asyncio
import base64
import binascii
import logging
import re
import secrets
import time
from typing import Any, ClassVar

from genro_bag import BagResolver
from genro_builders.builder import element
from genro_routes import RoutingClass, route
from genro_storage.exceptions import StorageError

from kajenn.application import ApplicationGrammar
from kajenn.auth.mixin import AUTHENTICATION_CODE, DEFAULT_CREDENTIAL_CACHE_TTL
from kajenn.exceptions import HTTPForbidden, HTTPUnauthorized
from kajenn.routed_application import RoutedApplication
from kajenn.session import Avatar

from .auth_method import AuthMethod, AuthMethodRegistry, PasswordMethod
from .auth_sections import GroupsSection, TagsSection, TokensSection, UsersSection
from .auth_sections.tags_section import ADMIN_TAG, NEW_TAG_DEFAULTS
from .auth_store import AUTHENTICATING_STATUS, TOKEN_PREFIX, USER_STATUSES, AuthStore, FileAuthStore
from .oidc_method import OidcMethod

__all__ = ["AuthApplicationBase", "AuthApplicationGrammar"]

#: The ``scheme`` labels a credential check takes from its header word.
HEADER_SCHEMES = frozenset({"basic", "bearer"})

OTHER_SCHEME = "other"
API_KEY_SCHEME = "api_key"
JWT_SCHEME = "jwt"
JWT_PARTS = 3


def credential_scheme(credential: str) -> str:
    """The ``scheme`` label of a credential checked by ``_auth.verify_credential``.

    Both outcomes use the form of the ``Authorization`` value.

    A bearer token starting with ``gak_`` is ``api_key``; a bearer token of
    three dot-separated parts is ``jwt``; otherwise the header word
    lowercased when it is ``basic`` or ``bearer``; anything else — a value
    with no space included — is ``other``.
    """
    word, separator, token = credential.partition(" ")
    if not separator:
        return OTHER_SCHEME
    scheme = word.lower()
    if scheme == "bearer":
        if token.startswith(TOKEN_PREFIX):
            return API_KEY_SCHEME
        if len(token.split(".")) == JWT_PARTS:
            return JWT_SCHEME
    return scheme if scheme in HEADER_SCHEMES else OTHER_SCHEME


#: The identity of the temporary administrator of a site without an administrator.
TEMPORARY_ADMIN = "_temporary_admin"

#: Bytes of randomness in the generated password of the temporary administrator.
TEMPORARY_PASSWORD_BYTES = 24

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_BACKOFF_SECONDS = 30.0
CHALLENGE = [(b"www-authenticate", b"Bearer")]

#: A name inside an auth rule: the tag names of ``tags_match``.
RULE_NAME = re.compile(r"[A-Za-z_]\w*")

#: The operators of ``tags_match`` (any case): a tag code that is one of them
#: cannot be named in a rule.
RULE_KEYWORDS = frozenset({"and", "or", "not"})


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

    # ── declared tags and system users ─────────────────────────────────

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        """``AUTH_ADMIN``, the tag the management sections ask for."""
        return [{"code": ADMIN_TAG, "description": "Management of users, groups, tags and tokens"}]

    def on_startup(self) -> None:
        """Create the declared records, then the temporary administrator; watch for added apps.

        Every application the server holds is read with
        ``create_declared_records``; then ``refresh_temporary_admin``. An
        application written later in the ``applications`` branch of the live
        configuration is read once the server has registered it.
        """
        for app in list(self.server.applications.values()):
            self.create_declared_records(app)
        self.refresh_temporary_admin()
        self.server.application_nodes.subscribe(self.code, insert=self._application_written)

    # ── temporary administrator ────────────────────────────────────────

    @property
    def has_admin(self) -> bool:
        """True when a user other than ``_temporary_admin`` is an administrator.

        An administrator has status ``confirmed``, ``system`` false, and
        ``AUTH_ADMIN`` among its own tags or the tags of any of its groups.
        """
        for user in self.store.user_list:
            if user["identity"] == TEMPORARY_ADMIN:
                continue
            if user.get("status") != AUTHENTICATING_STATUS or user.get("system", False):
                continue
            if ADMIN_TAG in user.get("tags", []):
                return True
            for code in user.get("groups", []):
                group = self.store.get_group(code)
                if group is not None and ADMIN_TAG in group.get("tags", []):
                    return True
        return False

    def refresh_temporary_admin(self) -> None:
        """Write ``_temporary_admin`` with a new password when the site has no administrator.

        The password is logged once, in one warning line with the identity.
        The tokens issued to ``_temporary_admin`` before are deleted: nothing
        obtained with an earlier password stays valid.
        With an administrator the record of ``_temporary_admin`` is deleted
        and nothing is logged. A store that cannot be built or written
        (``RuntimeError``, ``StorageError``) writes nothing and a warning is
        logged.
        """
        try:
            self._write_temporary_admin()
        except (RuntimeError, StorageError) as failure:
            logging.getLogger(__name__).warning(
                "application %s: temporary administrator not written: %s", self.code, failure
            )

    def _write_temporary_admin(self) -> None:
        """The writes of ``refresh_temporary_admin``; a store failure propagates."""
        if self.has_admin:
            self.store.delete_user(TEMPORARY_ADMIN)
            return
        for token in self.store.token_list:
            if token.get("user") == TEMPORARY_ADMIN:
                self.store.delete_token(token["key_id"])
        password = secrets.token_urlsafe(TEMPORARY_PASSWORD_BYTES)
        self.store.save_user(
            {
                "identity": TEMPORARY_ADMIN,
                "groups": [],
                "primary_group": None,
                "tags": [ADMIN_TAG],
                "status": AUTHENTICATING_STATUS,
                "system": False,
                "email": None,
                "external_ids": [],
                "password_hash": self.store.hash_password(password),
            }
        )
        logging.getLogger(__name__).warning(
            "application %s: temporary administrator %s, password %s",
            self.code,
            TEMPORARY_ADMIN,
            password,
        )

    async def remove_temporary_admin(self) -> None:
        """Delete ``_temporary_admin`` when another administrator exists (see ``has_admin``).

        After the deletion the credential cache of the server and of every
        joined process is emptied: the password, the sessions and the tokens
        of ``_temporary_admin`` stop working at once. Nothing happens when the
        record does not exist or no other administrator exists.
        """
        if self.store.get_user(TEMPORARY_ADMIN) is None or not self.has_admin:
            return
        self.store.delete_user(TEMPORARY_ADMIN)
        await self.server.forget_credentials_everywhere()

    def on_remove(self) -> None:
        """Stop watching the ``applications`` branch of the live configuration."""
        self.server.application_nodes.unsubscribe(self.code, any=True)

    def _application_written(self, node: Any, pathlist: Any = (), **_: Any) -> None:
        """Read the application of a node written at runtime, after the server registered it.

        A node written inside an application (``pathlist`` not empty) is none.
        """
        if pathlist:
            return
        code = node.attr.get("code") or node.label
        asyncio.get_running_loop().call_soon(self._read_added_application, code)

    def _read_added_application(self, code: str) -> None:
        """``create_declared_records`` on the application ``code``, when the server holds it."""
        app = self.server.applications.get(code)
        if app is not None:
            self.create_declared_records(app)

    def create_declared_records(self, app: Any) -> None:
        """Write the missing records of the tags and system users ``app`` declares.

        A tag is written as ``{code, description, owner, reserved, parent}``:
        ``code`` and ``parent`` as declared, ``owner`` the code of ``app``,
        missing fields from ``NEW_TAG_DEFAULTS``. A system user is written as
        declared, with ``system`` true and no token. A record that already
        exists is left as it is; a tag record that exists with another
        ``owner`` is also reported with a warning, since two applications then
        share one tag.

        Nothing stops the boot or the ``joined`` call: an entry whose
        ``code``, ``parent``, ``identity`` or ``tags`` has the wrong type, a
        tag whose ``code`` is not a name an auth rule can hold (an operator, or
        a character outside letters, digits and ``_``), and a user record the
        store refuses, are not written and a warning is logged; a store that
        cannot be built or written (``RuntimeError``, ``StorageError``) writes
        nothing for ``app`` and a warning is logged, and the first request
        that uses the store gets the error.
        """
        try:
            self._write_declared_records(app)
        except (RuntimeError, StorageError) as failure:
            self._warn_declaration(app, "declared records not written", failure)

    def _warn_declaration(self, app: Any, what: str, reason: Any) -> None:
        """Log a warning about a declaration of ``app`` that was not written."""
        logging.getLogger(__name__).warning(
            "application %s: %s of %r: %s", self.code, what, app.code, reason
        )

    def _write_declared_records(self, app: Any) -> None:
        """The writes of ``create_declared_records``; a store failure propagates."""
        store = self.store
        for tag in app.declared_tags:
            parent = tag.get("parent", NEW_TAG_DEFAULTS["parent"])
            if not isinstance(tag.get("code"), str) or not isinstance(parent, (str, type(None))):
                self._warn_declaration(app, "tag not written", f"bad code or parent in {tag!r}")
                continue
            if not RULE_NAME.fullmatch(tag["code"]) or tag["code"].lower() in RULE_KEYWORDS:
                self._warn_declaration(
                    app, "tag not written", f"a rule cannot hold the code {tag['code']!r}"
                )
                continue
            code = tag["code"]
            existing = store.get_tag(code)
            if existing is not None:
                if existing.get("owner") != app.code:
                    self._warn_declaration(
                        app,
                        "tag not written",
                        f"{code!r} already exists with owner {existing.get('owner')!r}",
                    )
                continue
            store.save_tag(
                {
                    **NEW_TAG_DEFAULTS,
                    "description": tag.get("description", NEW_TAG_DEFAULTS["description"]),
                    "reserved": tag.get("reserved", NEW_TAG_DEFAULTS["reserved"]),
                    "code": code,
                    "owner": app.code,
                    "parent": parent,
                }
            )
        for user in app.system_users:
            tags = user.get("tags", [])
            if not isinstance(user.get("identity"), str) or not (
                isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)
            ):
                self._warn_declaration(
                    app, "system user not written", f"bad identity or tags in {user!r}"
                )
                continue
            if store.get_user(user["identity"]) is not None:
                continue
            record = {**user, "tags": tags, "system": True}
            try:
                store.save_user(record)
            except ValueError as refused:
                self._warn_declaration(
                    app, f"system user {user['identity']!r} not written", refused
                )

    def get_tags_refusal(self, body: dict[str, Any]) -> dict[str, Any]:
        """The ``{"error": ...}`` answer for a ``tags`` field the management refuses.

        Refused: a ``tags`` value that is not a list of strings, and a name
        that is not the code of a tag record. Empty when ``body`` has no
        ``tags`` field or every name is the code of a tag record.
        """
        tags = body.get("tags", [])
        if not (isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)):
            return {"error": "tags must be a list of tag codes"}
        known = {record["code"] for record in self.store.tag_list}
        unknown = [tag for tag in tags if tag not in known]
        if not unknown:
            return {}
        return {"error": f"Unknown tags: {', '.join(unknown)}"}

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

        Each verification is counted in ``server.event_metrics`` by credential
        scheme, ``accepted`` or ``refused``; a caller refused by the bus gate is not.

        Note:
            Route: POST /_auth/authenticate
        """
        self._bus_only(_request)
        metrics = self.server.event_metrics
        try:
            answer = self.verify_credential(credential)
        except HTTPUnauthorized:
            metrics.record_credential_check(credential_scheme(credential), "refused")
            raise
        metrics.record_credential_check(credential_scheme(credential), "accepted")
        return answer

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

        Every answer is counted in ``server.event_metrics`` as scheme ``password``:
        ``refused`` when it has an ``error`` key, otherwise ``accepted``.

        Note:
            Route: POST /_auth/login
        """
        answer = self._login(identity, password, group, _request)
        outcome = "refused" if "error" in answer else "accepted"
        self.server.event_metrics.record_credential_check("password", outcome)
        return answer

    def _login(
        self, identity: str, password: str, group: str, request: Any
    ) -> dict[str, Any]:
        """Verify the password and attach the avatar, returning the login answer."""
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
        session = request.session
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
