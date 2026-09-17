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

"""Session store — the storage Protocol and the in-memory default.

``SessionStore`` is a runtime-checkable ``Protocol`` (get/create/delete/
purge_expired/dump/restore). Its test suite is a shared CONTRACT suite driven
by a store factory (§5.9), so a custom backend plugs into the SAME tests.
``MemorySessionStore`` is the dict-backed only shipped store: ``secrets``
tokens, a ``default_ttl`` for new sessions, lazy expiry on ``get``, and a
delta-checked ``purge_expired`` at ``create`` time — the mass reap runs only
when ``PURGE_INTERVAL`` has elapsed since the last one (no background task:
this REPLACES the former TaskManager purge loop, a ratified revision of core
1e/◆D22). ``dump``/``restore`` persist meta and the keyed avatars'
identity/tags only — never the data Bag. The serialized shape is
``avatars: {key: {identity, tags}}``, the whole wardrobe of the session.
``save_snapshot``/``load_snapshot`` are the OTHER persistence pair — one
pickle file carrying every live session whole, data Bag included, the
development survival line ``SessionMixin`` drives around the lifespan.
``create()`` is anonymous by default (``avatar is None``); capturing an identity
into a session is an explicit ``create(avatar=...)``, which dresses the root
slot.
"""

from __future__ import annotations

import pickle
import secrets
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .avatar import Avatar
from .session import Session

__all__ = ["SessionStore", "MemorySessionStore", "PURGE_INTERVAL"]

PURGE_INTERVAL = 300.0   # seconds between two mass reaps of expired sessions


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session storage backends."""

    def get(self, session_id: str) -> Session | None:
        """Retrieve a session by id, or ``None`` if unknown or expired."""
        ...

    def create(self, avatar: Avatar | None = None) -> Session:
        """Create a new session with a unique token (anonymous by default).

        ``avatar`` dresses the session's root slot.
        """
        ...

    def save(self, session: Session) -> None:
        """Persist a dirty session's state (the middleware calls this at request end)."""
        ...

    def delete(self, session_id: str) -> None:
        """Remove a session from the store."""
        ...

    def purge_expired(self) -> int:
        """Remove every expired session; return how many were purged."""
        ...

    def dump(self) -> dict[str, Any]:
        """Serialize the sessions for persistence."""
        ...

    def restore(self, data: dict[str, Any]) -> None:
        """Restore sessions from serialized data."""
        ...


class MemorySessionStore:
    """In-memory session store — the default implementation."""

    __slots__ = ("_sessions", "_default_ttl", "_last_purge")

    def __init__(self, default_ttl: int = 3600) -> None:
        """Initialize an empty store with a default TTL for new sessions."""
        self._sessions: dict[str, Session] = {}
        self._default_ttl = default_ttl
        self._last_purge = time.time()

    def get(self, session_id: str) -> Session | None:
        """Retrieve a session by id; drop and return ``None`` if it has expired."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired():
            del self._sessions[session_id]
            return None
        session.touch()
        return session

    def create(self, avatar: Avatar | None = None) -> Session:
        """Create a session (default TTL); a delta-checked mass reap runs first.

        The reap is opportunistic AND throttled: it runs only when
        ``PURGE_INTERVAL`` has elapsed since the last one, so a burst of
        creates never pays a full-store scan each time.
        """
        if time.time() - self._last_purge > PURGE_INTERVAL:
            self.purge_expired()
        session_id = secrets.token_urlsafe(32)
        session = Session(session_id=session_id, avatar=avatar, ttl=self._default_ttl)
        self._sessions[session_id] = session
        return session

    def save(self, session: Session) -> None:
        """No-op: the in-memory store holds the live object, so it is already saved."""

    def delete(self, session_id: str) -> None:
        """Remove a session from the store (a no-op if absent)."""
        self._sessions.pop(session_id, None)

    def purge_expired(self) -> int:
        """Drop every expired session from the store; return the count purged."""
        self._last_purge = time.time()
        expired = [sid for sid, session in self._sessions.items() if session.is_expired()]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def dump(self) -> dict[str, Any]:
        """Serialize meta and every keyed avatar's identity/tags (never the data Bag)."""
        return {
            session_id: {
                "meta": dict(session.meta),
                "avatars": {
                    key: {"identity": avatar.identity, "tags": list(avatar.tags)}
                    for key, avatar in session.avatars.items()
                },
            }
            for session_id, session in self._sessions.items()
        }

    def save_snapshot(self, path: str | Path) -> int:
        """Pickle EVERY live session — data Bag INCLUDED — to *path*.

        The development survival line (``kajenn serve --name``): the whole
        store crosses a restart through one pickle file. This deliberately
        supersedes the ``dump``/``restore`` contract ("the data Bag is never
        persisted") for the snapshot path. Expired sessions are reaped first;
        parent directories are created. Returns how many sessions were saved.
        """
        self.purge_expired()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pickle.dumps(self._sessions))
        return len(self._sessions)

    def load_snapshot(self, path: str | Path) -> int:
        """Repopulate the store from a ``save_snapshot`` file, dropping expired ones.

        The TTL is the only filter: a session whose ``last_access`` is still
        within its ``ttl`` comes back whole (data Bag included). Returns how
        many sessions were restored.
        """
        sessions: dict[str, Session] = pickle.loads(Path(path).read_bytes())
        kept = {sid: session for sid, session in sessions.items() if not session.is_expired()}
        self._sessions.update(kept)
        return len(kept)

    def restore(self, data: dict[str, Any]) -> None:
        """Restore non-expired sessions from ``dump()`` output (meta + rebuilt avatars)."""
        for session_id, session_data in data.items():
            meta = session_data["meta"]
            avatars = session_data["avatars"]
            root = avatars.get(Session.ROOT_AVATAR_KEY)
            session = Session(
                session_id=session_id,
                avatar=Avatar(root["identity"], root["tags"]) if root else None,
                ttl=meta["ttl"],
            )
            for key, avatar_data in avatars.items():
                if key != Session.ROOT_AVATAR_KEY:
                    session.attach_avatar(Avatar(avatar_data["identity"], avatar_data["tags"]), key)
            session.clear_dirty()
            session.meta["created_at"] = meta["created_at"]
            session.meta["last_access"] = meta["last_access"]
            if not session.is_expired():
                self._sessions[session_id] = session
