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

"""Auth capability: header/session identity resolution as a mixin (D16).

``AuthMixin`` is composed BEFORE ``SessionMixin``/``MiddlewareMixin``/
``BaseServer`` (``class S(AuthMixin, SessionMixin, MiddlewareMixin,
BaseServer)``). Its cooperative ``__init__`` peels ``auth=`` (the config dict;
``None`` builds an ``AuthCore`` with no header backends armed), ``channels=``
(the route that authenticates each channel) and ``credential_cache_ttl=``.

It also wires the server's identity stores. ``users=`` and ``tokens=`` each take
the store DESCRIPTOR the configuration declares — ``store_class`` (``FileUserStore``
/ ``FileApiKeyStore`` when omitted) plus that class's kwargs, ``{mount, prefix}``
defaulting to ``site:users`` / ``site:api_keys``. The stores are built AFTER
``super().__init__()`` returns — by then the cooperative chain has run and
``self.storage`` exists, since AuthMixin precedes StorageMixin in the MRO. The
``user_store`` / ``api_key_store`` properties return ``None`` when
unconfigured. A descriptor without storage on the server is a boot error
(no silent fallback).

The server creates NO user at boot: there is no ``admin_password=`` kwarg,
and no bootstrap identity is written. Authentication belongs to the
applications, and a deployment that needs a first identity declares the
store class that carries it.

It overrides the coroutine ``authenticate(scope)``, which the execution point
(``RoutedApplication.execute``) and the WSX handshake await: the
``Authorization`` header, when presented, verified through the channel's
``authentication_route`` — an ``Avatar`` or a raised ``HTTPUnauthorized`` —
else the session's root avatar when the scope carries one, else ``None``. ``SessionMiddleware`` (order 400)
has already put the session on an ``http`` scope when the execution point runs.
"""

from __future__ import annotations

import hashlib
from time import monotonic
from typing import Any

import kbus

from ..exceptions import HTTPException, HTTPUnauthorized
from ..kbus import KBusCallError
from ..middleware.base import headers_dict
from ..session.avatar import Avatar
from .api_key_store import ApiKeyStore, FileApiKeyStore
from .core import AuthCore
from .user_store import FileUserStore, UserStore

__all__ = ["AuthMixin"]


class AuthMixin:
    """Auth capability mixin, composed BEFORE the session/middleware/server classes.

    Constructor kwargs peeled here: ``auth`` — the credential config dict
    (``{'basic': ..., 'bearer': ..., 'jwt': [...]}``); ``None`` arms no header
    backend but still resolves the session identity through §5.5 precedence.
    ``users`` / ``tokens`` — the store descriptor (``store_class`` plus its
    kwargs) for the identity/api-key stores.

    The server creates NO user at boot: authentication belongs to the
    applications. A deployment that needs a first identity declares the store
    class that carries it.
    """

    DEFAULT_AUTHENTICATION_ROUTE = "/_server/auth/authenticate"

    def __init__(self, **kwargs: Any) -> None:
        auth: dict[str, Any] | None = kwargs.pop("auth", None)
        users = kwargs.pop("users", None)
        tokens = kwargs.pop("tokens", None)
        channels: dict[str, dict[str, str]] = kwargs.pop("channels", None) or {}
        credential_cache_ttl: float = kwargs.pop("credential_cache_ttl", 300.0)
        super().__init__(**kwargs)
        self._user_store = self._build_user_store(users)
        self._api_key_store = self._build_api_key_store(tokens)
        self._auth_core = AuthCore(**(auth or {}), api_key_store=self._api_key_store)
        self._channels = channels
        self._credential_cache_ttl = credential_cache_ttl
        self._credential_cache: dict[tuple[str, str], tuple[float, Avatar]] = {}
        self._credential_cache_generation = 0

    @property
    def channels(self) -> dict[str, dict[str, str]]:
        """The configured channels: ``{name: {"authentication_route": path}}``."""
        return self._channels

    @property
    def credential_cache_ttl(self) -> float:
        """Seconds a verified credential stays cached; ``0`` disables the cache."""
        return self._credential_cache_ttl

    def authentication_route(self, channel: str) -> str:
        """The route verifying a credential presented on ``channel``."""
        configured = self.channels.get(channel)
        if configured is None:
            return self.DEFAULT_AUTHENTICATION_ROUTE
        return configured["authentication_route"]

    async def authenticate_credential(self, credential: str, channel: str) -> Avatar:
        """Verify ``credential`` through the channel's route; cache the ``Avatar`` by TTL.

        The route answers ``{identity, tags, data}``. A 401 from it, or no
        application answering it (404), is ``HTTPUnauthorized`` with the
        ``WWW-Authenticate: Bearer`` challenge; a route that cannot be reached
        (a lost link, a timeout, an error REPLY without status) or that fails
        is a 503; any other error status propagates as ``HTTPException``.
        Failures are not cached, and an answer that arrives after
        ``forget_credential``/``forget_all_credentials`` ran is not cached
        either: a key revoked during the verification is refused next time.
        """
        key = (hashlib.sha256(credential.encode()).hexdigest(), channel)
        cached = self._credential_cache.get(key)
        if cached is not None and cached[0] > monotonic():
            return cached[1]
        generation = self._credential_cache_generation
        challenge = [(b"www-authenticate", b"Bearer")]
        try:
            answer = await self.kbus_call(
                self.authentication_route(channel), {"credential": credential, "channel": channel}
            )
        except KBusCallError as error:
            if error.status == 401:
                raise HTTPUnauthorized(str(error.error), headers=challenge) from error
            if error.status == 404:
                raise HTTPUnauthorized(
                    f"no authentication route for channel {channel}", headers=challenge
                ) from error
            if error.status is None or error.status >= 500:
                raise HTTPException(503, f"authentication route failed: {error.error}") from error
            raise HTTPException(error.status, str(error.error)) from error
        except (kbus.Error, TimeoutError) as error:
            raise HTTPException(503, f"authentication route unreachable: {error}") from error
        avatar = Avatar(answer["identity"], answer["tags"])
        for name, value in (answer.get("data") or {}).items():
            avatar.data[name] = value
        if self.credential_cache_ttl > 0 and generation == self._credential_cache_generation:
            self._credential_cache[key] = (monotonic() + self.credential_cache_ttl, avatar)
        return avatar

    def forget_credential(self, credential: str) -> None:
        """Drop the cached avatars of ``credential`` for every channel."""
        digest = hashlib.sha256(credential.encode()).hexdigest()
        self._credential_cache_generation += 1
        for key in list(self._credential_cache):  # a snapshot: the loop may be writing
            if key[0] == digest:
                self._credential_cache.pop(key, None)

    def forget_all_credentials(self) -> None:
        """Drop every cached avatar: a revoked or deleted api key is refused at once."""
        self._credential_cache_generation += 1
        self._credential_cache.clear()

    @property
    def auth_core(self) -> AuthCore:
        """The credential store backing this server's header authentication."""
        return self._auth_core

    @property
    def user_store(self) -> UserStore | None:
        """The local identity store, or ``None`` when no ``users`` is configured."""
        return self._user_store

    @property
    def api_key_store(self) -> ApiKeyStore | None:
        """The api-key registry, or ``None`` when no ``tokens`` is configured."""
        return self._api_key_store

    def _build_user_store(self, users: Any) -> UserStore | None:
        """Build the user store the descriptor names, or ``None`` when unconfigured.

        The descriptor is what the ``authentication.users`` section carries:
        ``store_class`` (``FileUserStore`` when omitted) plus that class's own
        kwargs. The storage is handed to it — no built store ever arrives here,
        because a store is not a value a configuration can hold.
        """
        if users is None:
            return None
        options = dict(users)
        store_class = options.pop("store_class", None) or FileUserStore
        return store_class(self._require_storage("users"), **options)

    def _build_api_key_store(self, tokens: Any) -> ApiKeyStore | None:
        """Build the api-key store the descriptor names, or ``None`` — see
        ``_build_user_store``; the default class is ``FileApiKeyStore``."""
        if tokens is None:
            return None
        options = dict(tokens)
        store_class = options.pop("store_class", None) or FileApiKeyStore
        return store_class(self._require_storage("tokens"), **options)

    def _require_storage(self, section: str) -> Any:
        """Return the server storage, or raise when a config-dict store needs it.

        A ``{mount, prefix}`` store cannot be built without a StorageMixin on the
        server: an incoherent configuration is a boot error, never a silent None.
        """
        storage = getattr(self, "storage", None)
        if storage is None:
            raise RuntimeError(
                f"'{section}' store needs a storage mount, but the server has no storage"
            )
        return storage

    async def authenticate(self, scope: Any) -> Any:
        """Resolve the identity of ``scope``: a presented credential first, then the session.

        An ``Authorization`` header is always verified, through the route of the
        scope's channel (``authenticate_credential``): an invalid one raises
        ``HTTPUnauthorized`` on any route, a session or not. Without a header
        the session's root avatar answers when ``scope["session"]`` carries
        one; otherwise ``None``. A scope without ``kajenn.channel`` is read as
        the REST face.
        """
        credential = headers_dict(scope).get("authorization")
        if credential:
            return await self.authenticate_credential(
                credential, scope.get("kajenn.channel", "rest")
            )
        session = self.session(scope)
        return session.avatar() if session is not None else None
