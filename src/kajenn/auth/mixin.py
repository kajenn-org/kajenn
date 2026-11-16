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

"""Auth capability: the server asks the authentication application, as a mixin (D16).

``AuthMixin`` is composed BEFORE ``SessionMixin``/``MiddlewareMixin``/
``BaseServer`` (``class S(AuthMixin, SessionMixin, MiddlewareMixin,
BaseServer)``). It peels no kwarg: the server holds no store, no credential
and no user. Authentication belongs to the application mounted with code
``_auth`` (``AUTHENTICATION_CODE``), and the server asks it through two
fixed entry points over the KajennBus:

- ``/_auth/authenticate`` with ``{credential, channel}``, for a presented
  ``Authorization`` header;
- ``/_auth/avatar`` with ``{identity, group}``, for the root avatar of a
  session, which remembers only identity and group.

Both answer ``{identity, group, tags, data, valid_until}``, which becomes an
``Avatar``: a new object at every request, whose ``data`` a handler may write
for that request only. The answers share one cache, kept ``credential_cache_ttl``
seconds: the ``cache_ttl`` of the mounted ``_auth`` application. An
application's dedicated process does not mount it and reads the ``cache_ttl``
of the ``_auth`` element of its own recipe; ``DEFAULT_CREDENTIAL_CACHE_TTL``
applies when neither gives a value.
``forget_credential`` and ``forget_all_credentials`` empty it; a status or a
tag changed in the store reaches a live session within that time, at once
after the cache is emptied.

It overrides the coroutine ``authenticate(scope)``, which the execution point
(``RoutedApplication.execute``) and the WSX handshake await: the
``Authorization`` header, when presented, verified by ``/_auth/authenticate``
— an ``Avatar`` or a raised ``HTTPUnauthorized`` — else the avatar of the
session's root identity, asked to ``/_auth/avatar``, else ``None``.
``SessionMiddleware`` (order 400) has already put the session on an ``http``
scope when the execution point runs.

A server that does not mount ``_auth`` does not authenticate: a presented
credential raises ``HTTPUnauthorized`` with no bus call, and a session gives
no avatar. In an application's dedicated process (``kbus_role``) the ``_auth``
application lives in the server process: every question goes there over the
bus, and a server without ``_auth`` answers 404, which is ``HTTPUnauthorized``.
"""

from __future__ import annotations

import copy
import hashlib
from time import monotonic, time
from typing import Any

import kbus

from ..exceptions import HTTPException, HTTPUnauthorized, KBusCallError
from ..middleware.base import headers_dict
from ..session.avatar import Avatar

__all__ = ["AuthMixin"]

#: The code of the authentication application the server asks.
AUTHENTICATION_CODE = "_auth"

#: Seconds an answer stays cached in a process that does not mount ``_auth``.
DEFAULT_CREDENTIAL_CACHE_TTL = 300.0

CHALLENGE = [(b"www-authenticate", b"Bearer")]


class AuthMixin:
    """Auth capability mixin, composed BEFORE the session/middleware/server classes.

    It peels no constructor kwarg. The server asks the application mounted
    with code ``_auth`` (see module docstring).
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._credential_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._credential_cache_generation = 0

    @property
    def _authenticates(self) -> bool:
        """Whether this process can ask an authentication application.

        True when ``_auth`` is mounted here, and in an application's dedicated
        process, whose questions go to the server process.
        """
        return AUTHENTICATION_CODE in self.applications or self.kbus_role is not None

    @property
    def credential_cache_ttl(self) -> float:
        """Seconds an answer stays cached; ``0`` disables the cache.

        The ``cache_ttl`` of the mounted ``_auth`` application. A process that
        does not mount it — an application's dedicated process — reads the
        ``cache_ttl`` attribute of the ``_auth`` element of its own recipe;
        ``DEFAULT_CREDENTIAL_CACHE_TTL`` when neither gives a value.
        """
        application = self.applications.get(AUTHENTICATION_CODE)
        if application is not None:
            ttl: float = application.cache_ttl
            return ttl
        handler = getattr(self, "config", None)
        node = None if handler is None else handler.node(f"applications.{AUTHENTICATION_CODE}")
        if node is None:
            return DEFAULT_CREDENTIAL_CACHE_TTL
        declared: float = handler.open_attrs(node).get("cache_ttl", DEFAULT_CREDENTIAL_CACHE_TTL)
        return declared

    async def authenticate_credential(self, credential: str, channel: str) -> Avatar:
        """Verify ``credential`` with ``/_auth/authenticate``; cache the answer by TTL.

        The answer is ``{identity, group, tags, data, valid_until}``;
        ``valid_until`` (a POSIX timestamp or ``None``) ends the cache entry
        earlier than the TTL, so a cached avatar never outlives the
        credential. Without an authentication application the credential is
        refused with no bus call. A 401, or a 404 (nobody serves the entry
        point), is ``HTTPUnauthorized`` with the ``WWW-Authenticate: Bearer``
        challenge; an entry point that cannot be reached (a lost link, a
        timeout, an error REPLY without status) or that fails is a 503; any
        other error status propagates as ``HTTPException``. Failures are not
        cached, and an answer that arrives after
        ``forget_credential``/``forget_all_credentials`` ran is not cached
        either: a key revoked during the verification is refused next time.
        """
        if not self._authenticates:
            raise HTTPUnauthorized("no authentication application is mounted", headers=CHALLENGE)
        key = ("credential", hashlib.sha256(credential.encode()).hexdigest(), channel)
        return await self._ask_avatar(
            key,
            f"/{AUTHENTICATION_CODE}/authenticate",
            {"credential": credential, "channel": channel},
        )

    async def _ask_avatar(
        self, key: tuple[str, str, str], path: str, data: dict[str, Any]
    ) -> Avatar:
        """The ``Avatar`` the entry point ``path`` answers for ``data``, through the cache.

        Errors as ``authenticate_credential``.
        """
        cached = self._credential_cache.get(key)
        if cached is not None and cached[0] > monotonic():
            return self._build_avatar(cached[1])
        generation = self._credential_cache_generation
        try:
            answer = await self.kbus_call(path, data)
        except KBusCallError as error:
            if error.status == 401:
                raise HTTPUnauthorized(str(error.error), headers=CHALLENGE) from error
            if error.status == 404:
                raise HTTPUnauthorized(
                    "no authentication application answers", headers=CHALLENGE
                ) from error
            if error.status is None or error.status >= 500:
                raise HTTPException(503, f"authentication failed: {error.error}") from error
            raise HTTPException(error.status, str(error.error)) from error
        except (kbus.Error, TimeoutError) as error:
            raise HTTPException(503, f"authentication unreachable: {error}") from error
        if self.credential_cache_ttl > 0 and generation == self._credential_cache_generation:
            lifetime = self.credential_cache_ttl
            valid_until = answer.get("valid_until")
            if valid_until is not None:
                lifetime = min(lifetime, valid_until - time())
            if lifetime > 0:
                self._credential_cache[key] = (monotonic() + lifetime, answer)
        return self._build_avatar(answer)

    def _build_avatar(self, answer: dict[str, Any]) -> Avatar:
        """A new ``Avatar`` from an answer ``{identity, group, tags, data}``.

        Every call gives its own object with its own copy of ``data``: what a
        handler writes there lives for that request only.
        """
        avatar = Avatar(answer["identity"], answer["tags"], group=answer["group"])
        for name, value in (answer.get("data") or {}).items():
            avatar.data[name] = copy.deepcopy(value)
        return avatar

    def forget_credential(self, credential: str) -> None:
        """Drop the cached avatars of ``credential`` for every channel, and those of the sessions."""
        digest = hashlib.sha256(credential.encode()).hexdigest()
        self._credential_cache_generation += 1
        for key in list(self._credential_cache):  # a snapshot: the loop may be writing
            if key[0] == "session" or key[1] == digest:
                self._credential_cache.pop(key, None)

    def forget_all_credentials(self) -> None:
        """Drop every cached avatar: the next request asks ``_auth`` again."""
        self._credential_cache_generation += 1
        self._credential_cache.clear()

    async def authenticate(self, scope: Any) -> Any:
        """Resolve the identity of ``scope``: a presented credential first, then the session.

        An ``Authorization`` header is always verified
        (``authenticate_credential``): an invalid one raises
        ``HTTPUnauthorized`` on any route, a session or not. A scope without
        ``kajenn.channel`` is read as the ``api`` face.

        Without a header, a session with a root avatar is asked to
        ``/_auth/avatar`` with its identity and group, through the cache. The
        avatar built from the answer replaces the root avatar of the session
        and is returned. When ``_auth`` refuses (unknown identity, a status
        that does not authenticate, a group the user no longer has) the
        session is deleted and the answer is ``None``. No session, no root
        avatar, or no authentication application: ``None``.
        """
        credential = headers_dict(scope).get("authorization")
        if credential:
            return await self.authenticate_credential(
                credential, scope.get("kajenn.channel", "api")
            )
        session = self.session(scope)
        root = session.avatar() if session is not None else None
        if root is None or not self._authenticates:
            return None
        key = ("session", root.identity, root.group or "")
        try:
            avatar = await self._ask_avatar(
                key,
                f"/{AUTHENTICATION_CODE}/avatar",
                {"identity": root.identity, "group": root.group or ""},
            )
        except HTTPUnauthorized:
            self.session_store.delete(session.id)
            return None
        was_dirty = session.dirty
        session.attach_avatar(avatar)
        if not was_dirty:
            session.clear_dirty()  # identity and group, the stored part, did not change
        return avatar
