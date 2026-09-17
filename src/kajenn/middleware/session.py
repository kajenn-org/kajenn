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

"""Session middleware — session lifecycle driven by the request cookie.

Reads the session token from the request ``Cookie`` header (via the shared
``headers_dict`` scope cache, not a request object), reconnects an existing
session or creates a new ANONYMOUS one through ``server.session_store``
(``store.create()`` with no avatar — capturing an identity into a session is
an explicit act of the login surface, core 1d), attaches it to
``scope["session"]``, and — ONLY when the session was created here — wraps
``send`` to add its ``Set-Cookie`` header (HttpOnly, ``Max-Age`` = the session
TTL times ``COOKIE_LIFETIME_FACTOR``). The cookie is deliberately much longer
than the session: the server-side TTL is SLIDING (every request refreshes
``last_access``) while ``Max-Age`` is fixed from issue time, so a same-length
cookie would log an active user out on schedule. The wide fixed cookie is the
legacy-proven answer (``GnrWebConnection.write_cookie``: timeout × 24, never
re-issued per request) — the server stays the only arbiter of expiry and no
response but the first carries a ``Set-Cookie``. Login never changes the
session id: a handler attaches the avatar to
the existing session in place (``request.session.attach_avatar``), so the
cookie the client already holds stays valid and no login-time cookie exists —
handlers stay pure and never set cookies themselves. Armed by ``SessionMixin``; order 400 (OUTSIDE
``AuthMiddleware`` at 450, so the session is on the scope before the §5.5
fallback runs), default OFF. The chain only carries ``http`` scopes, so no
scope filtering happens here.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

from .base import BaseMiddleware, cookie_value

if TYPE_CHECKING:
    from ..types import ASGIApp, Receive, Scope, Send

__all__ = ["SessionMiddleware", "COOKIE_LIFETIME_FACTOR"]

COOKIE_LIFETIME_FACTOR = 24   # cookie Max-Age = session TTL × this (see module doc)


class SessionMiddleware(BaseMiddleware):
    """Per-server session middleware: cookie in, session on the scope, cookie out."""

    middleware_order = 400
    middleware_default = False

    def __init__(
        self,
        app: ASGIApp,
        server: Any,
        cookie_name: str = "session_id",
        secure: bool = False,
        samesite: str = "lax",
        **options: Any,
    ) -> None:
        """Store the cookie configuration; ``server`` supplies ``session_store``."""
        super().__init__(app, server, **options)
        self._cookie_name = cookie_name
        self._secure = secure
        self._samesite = samesite

    def _cookie_value(self, scope: Scope) -> str | None:
        """The session cookie value carried by the request, or ``None``."""
        return cookie_value(scope, self._cookie_name)

    def get_session(self, scope: Scope) -> Any | None:
        """The session the store holds for this scope's cookie, or ``None``.

        Args:
            scope: any scope carrying cookies — an HTTP request, or the
                handshake of a websocket, which the chain never sees.

        Returns:
            The stored session, or ``None`` when no cookie arrived or the
            store has nothing for it.

        A pure reading: nothing is created here. The anonymous session of a
        first visit is born in ``__call__``, which is where a cookie can be
        issued for it — a websocket handshake has no such moment to offer.
        """
        incoming = self._cookie_value(scope)
        return self.server.session_store.get(incoming) if incoming else None

    def _set_cookie(self, session: Any) -> tuple[bytes, bytes]:
        """Build the ``Set-Cookie`` header tuple for a session to (re)issue to the client.

        ``Max-Age`` is the TTL times ``COOKIE_LIFETIME_FACTOR``: the cookie
        must outlive the sliding server-side expiry (module doc).
        """
        parts = [
            f"{self._cookie_name}={session.id}",
            f"Max-Age={session.meta['ttl'] * COOKIE_LIFETIME_FACTOR}",
            "Path=/",
            "HttpOnly",
            f"SameSite={self._samesite.capitalize()}",
        ]
        if self._secure:
            parts.append("Secure")
        return (b"set-cookie", "; ".join(parts).encode("latin-1"))

    def _write_back(self, session: Any) -> None:
        """Persist the session at request end, but ONLY when it is dirty.

        A read-only request never marks the session dirty (``get`` only refreshes
        ``last_access``), so it stays zero-I/O; a data mutation or a login raised
        the flag, and here it is saved and cleared.
        """
        if session.dirty:
            self.server.session_store.save(session)
            session.clear_dirty()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach the session to the scope; issue the cookie for a NEW session only.

        Login never changes the session id (a handler attaches the avatar to
        the existing session in place), so the only moment a cookie must be
        issued is when the store had no session for the incoming token (none
        arrived, expired, or unknown) and a fresh anonymous one was created
        here. In either path the session is written back at request end when
        dirty (``_write_back``).
        """
        store = self.server.session_store
        session = self.get_session(scope)
        if session is not None:
            scope["session"] = session
            await self.app(scope, receive, send)
            self._write_back(session)
            return
        session = store.create()
        scope["session"] = session

        async def send_with_cookie(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(self._set_cookie(session))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)
        self._write_back(session)
