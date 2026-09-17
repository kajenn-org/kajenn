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

"""Server-managed session: id, meta, Bag data, and a keyed collection of Avatars.

A ``Session`` groups request-scoped state under a unique id with expiry
tracking. ``SessionMiddleware`` creates or reconnects sessions via the request
cookie and attaches them to ``scope["session"]``.

**The dressing model.** A session is not one identity but a wardrobe of them,
each stored under a key. The ``ROOT_AVATAR_KEY`` slot holds the identity of the
primary login — the one the auth chain resolves and the one ``avatar()`` returns
with no argument. Further keys are *sub-logins*: an identity a page acquired
inside the same session (a second-system credential, an impersonation, a
delegated account) that must coexist with the root one instead of replacing it.
Page trees will reference the slot they are dressed in by ``avatar_key``, so the
identity of a page is a lookup in this collection, never a copy of it.

``avatar(key)`` returns ``Avatar | None`` — ``None`` is an unclaimed slot, and an
absent root slot is an anonymous session; capturing an identity is an explicit
``avatar=`` at creation (the root slot) or an ``attach_avatar`` call. ``avatars``
is a read-only view for enumeration; ``attach_avatar`` is its only writer. There
is no detach: a slot claimed in a session stays claimed for its lifetime.
``data`` is a ``Bag`` for arbitrary application data. ``touch()`` refreshes
``last_access``; ``is_expired()`` measures the TTL from it.

Write-back is explicit (D24): a session persists at request end ONLY when
``dirty`` is set. ``attach_avatar`` marks it dirty (a login must survive), and a
handler mutating ``data`` marks it dirty with ``mark_dirty()`` — there is no
write-through. ``touch()`` is NOT a mutation for this purpose: the ``last_access``
refresh happens on every ``get`` (including read-only requests), so making it
dirty would save on every request and defeat the zero-I/O read path. The
middleware clears the flag with ``clear_dirty()`` after a successful save.
"""

from __future__ import annotations

import time
import types
from typing import Any, Mapping

from genro_bag import Bag

from .avatar import Avatar

__all__ = ["Session"]


class Session:
    """Server-managed session with meta, Bag data, and keyed identity avatars."""

    __slots__ = ("_id", "_meta", "_data", "_avatars", "_dirty")

    ROOT_AVATAR_KEY = "root"

    def __init__(self, session_id: str, avatar: Avatar | None, ttl: int) -> None:
        """Initialize the session with its token, its root avatar, and a TTL."""
        now = time.time()
        self._id = session_id
        self._meta: dict[str, Any] = {"created_at": now, "last_access": now, "ttl": ttl}
        self._data = Bag()
        self._avatars: dict[str, Avatar] = {}
        if avatar is not None:
            self._avatars[self.ROOT_AVATAR_KEY] = avatar
        self._dirty = False

    @property
    def id(self) -> str:
        """Unique session token."""
        return self._id

    @property
    def meta(self) -> dict[str, Any]:
        """Server-managed metadata: created_at, last_access, ttl."""
        return self._meta

    @property
    def data(self) -> Bag:
        """Application data as a Bag."""
        return self._data

    def avatar(self, key: str = ROOT_AVATAR_KEY) -> Avatar | None:
        """The avatar dressed under ``key``; ``None`` = unclaimed slot.

        With no argument it returns the root avatar — the primary login — so an
        anonymous session answers ``None``.
        """
        return self._avatars.get(key)

    @property
    def avatars(self) -> Mapping[str, Avatar]:
        """Read-only view of the keyed avatars (``attach_avatar`` is the only writer)."""
        return types.MappingProxyType(self._avatars)

    @property
    def dirty(self) -> bool:
        """Whether the session has unsaved changes to persist at request end."""
        return self._dirty

    def attach_avatar(self, avatar: Avatar, key: str = ROOT_AVATAR_KEY) -> None:
        """Dress ``key`` with an avatar — the login event (marks the session dirty).

        The session stays the same object: id, ``data`` and ``meta`` are
        untouched, so whatever an anonymous visitor accumulated survives
        the login. The default key is the root slot (the primary login); any
        other key is a sub-login coexisting with it. The change is marked dirty
        so the login persists.
        """
        self._avatars[key] = avatar
        self.mark_dirty()

    def mark_dirty(self) -> None:
        """Flag the session as changed — a handler mutating ``data`` calls this."""
        self._dirty = True

    def clear_dirty(self) -> None:
        """Reset the dirty flag (the middleware calls this after a successful save)."""
        self._dirty = False

    def touch(self) -> None:
        """Refresh ``last_access`` to now (NOT a dirty-making change; see module doc)."""
        self.meta["last_access"] = time.time()

    def is_expired(self) -> bool:
        """Whether the session has exceeded its TTL (non-positive TTL = expired)."""
        ttl: int = self.meta["ttl"]
        if ttl <= 0:
            return True
        last_access: float = self.meta["last_access"]
        return bool((time.time() - last_access) > ttl)
