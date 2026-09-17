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

"""Session capability: HTTP sessions as a mixin over the base server (D16).

``SessionMixin`` is composed BEFORE ``MiddlewareMixin`` and ``BaseServer``
(``class S(SessionMixin, MiddlewareMixin, BaseServer)``). Its cooperative
``__init__`` peels ``session_store=`` (``None`` → a fresh ``MemorySessionStore``)
and ``session_ttl=`` (the default store's TTL), then ARMS ``SessionMiddleware``
by injecting ``{"session": True}`` into the ``middleware`` config it forwards
to ``MiddlewareMixin`` along the cooperative chain — composing the two mixins
arms sessions with no user action, while an explicit
``middleware={"session": False}`` still wins (``setdefault`` never overrides an
explicit switch). It overrides the §4 contract method ``session(request)`` to
return the session attached to the request scope; a composition WITHOUT the
mixin keeps the base answer (``None``). The login seam is not a server method:
a handler attaches the identity through the request facade
(``request.session.attach_avatar(avatar)``) — the session id never changes at
login, so the cookie already held by the client stays valid.

``save_session=`` arms the pickle snapshot, the development survival line the
CLI wires from ``serve --name`` (``~/.kajenn/sessions/<name>.pickle``):
``__call__`` intercepts the ``lifespan`` scope exactly like ``TaskMixin`` —
``lifespan.py`` is NEVER touched (ratified) — loading the snapshot before the
protocol runs (an absent file starts empty) and saving EVERY live session,
data Bag included, when the protocol completes at shutdown. Unarmed, every
scope passes straight through.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .store import MemorySessionStore, SessionStore

if TYPE_CHECKING:
    from ..types import Receive, Scope, Send

__all__ = ["SessionMixin"]


class SessionMixin:
    """Session capability mixin, composed BEFORE the middleware/server classes.

    Constructor kwargs peeled here: ``session_store`` — an explicit store
    (``None`` builds a ``MemorySessionStore``); ``session_ttl`` — the default
    store's TTL when no explicit store is given; ``save_session`` — the
    snapshot pickle path (``None``, the default, disarms the snapshot).
    """

    def __init__(self, **kwargs: Any) -> None:
        store: SessionStore | None = kwargs.pop("session_store", None)
        ttl: int | None = kwargs.pop("session_ttl", None)
        save_session: str | Path | None = kwargs.pop("save_session", None)
        middleware: dict[str, Any] = dict(kwargs.get("middleware") or {})
        middleware.setdefault("session", True)
        kwargs["middleware"] = middleware
        super().__init__(**kwargs)
        if store is None:
            store = MemorySessionStore() if ttl is None else MemorySessionStore(default_ttl=ttl)
        self._session_store = store
        self._save_session = Path(save_session) if save_session is not None else None

    @property
    def session_store(self) -> SessionStore:
        """The store backing this server's sessions."""
        return self._session_store

    @property
    def save_session(self) -> Path | None:
        """The snapshot pickle path, or ``None`` when the snapshot is disarmed."""
        return self._save_session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Hook the lifespan to load/save the session snapshot (D16 pattern).

        Armed (``save_session=`` given), the snapshot is loaded before the
        lifespan protocol runs — an absent file starts empty — and saved when
        the protocol completes at shutdown. Disarmed, or any non-lifespan
        scope, passes straight through.
        """
        if scope["type"] != "lifespan" or self.save_session is None:
            await super().__call__(scope, receive, send)
            return
        logger = logging.getLogger(__name__)
        if self.save_session.is_file():
            restored = self.session_store.load_snapshot(self.save_session)
            logger.info("restored %d session(s) from %s", restored, self.save_session)
        try:
            await super().__call__(scope, receive, send)
        finally:
            saved = self.session_store.save_snapshot(self.save_session)
            logger.info("saved %d session(s) to %s", saved, self.save_session)

    def session(self, request: Any) -> Any:
        """The session attached to the request scope, or ``None`` if none."""
        return request.get("session") if request is not None else None
