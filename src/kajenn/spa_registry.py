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

"""Process-local ownership and live stores for a SPA application's visits."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from typing import TYPE_CHECKING, Any

from genro_bag import Bag

from .spa_page import Page

if TYPE_CHECKING:
    from .spa_application import SpaApplicationBase


@dataclass(slots=True)
class _User:
    store: Any
    connections: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _Connection:
    user: str
    store: Any
    pages: set[str] = field(default_factory=set)
    expires_at: float | None = None


@dataclass(slots=True)
class _Page:
    store: Any
    page_class: type[Page]
    instance: Page | None = None


@dataclass(frozen=True, slots=True)
class PageState:
    """Ownership at lookup time and references to the three live stores."""

    page_id: str
    connection_id: str
    user: str
    page_store: Any
    connection_store: Any
    user_store: Any


class UnknownPage(KeyError):
    """The connection does not own a page with this ID."""


class SpaRegistry:
    """One registry per SPA process. Its lock protects ownership, not store contents."""

    def __init__(self) -> None:
        self._users: dict[str, _User] = {}
        self._connections: dict[str, _Connection] = {}
        self._pages: dict[str, _Page] = {}
        self._lock = RLock()
        self._next_sweep = 0.0

    def new_store(self) -> Any:
        """Create a live store. Applications may supply a different store type."""
        return Bag()

    def new_connection(self, connection_id: str) -> None:
        """Open a guest visit with a server-assigned connection ID."""
        with self._lock:
            if connection_id in self._connections:
                raise ValueError(f"Connection already registered: {connection_id!r}")
            guest = f"guest_{connection_id}"
            owner = _User(self.new_store(), {connection_id})
            connection = _Connection(guest, self.new_store())
            self._users[guest] = owner
            self._connections[connection_id] = connection

    def ensure_connection(
        self, connection_id: str, identity: str | None, *, login: bool, ttl: int
    ) -> None:
        """Resolve a browser visit and reclaim visits idle for the session TTL."""
        with self._lock:
            now = monotonic()
            if now >= self._next_sweep:
                for cid, connection in list(self._connections.items()):
                    if connection.expires_at is not None and connection.expires_at <= now:
                        self.drop_connection(cid)
                self._next_sweep = now + 60
            existing = self._connections.get(connection_id)
            if existing and existing.expires_at is not None and existing.expires_at <= now:
                self.drop_connection(connection_id)
            if identity is not None and identity.startswith("guest_"):
                raise PermissionError("Reserved SPA guest identity")
            if connection_id not in self._connections:
                if identity is not None and not login:
                    raise PermissionError("A session login is required")
                self.new_connection(connection_id)
            connection = self._connections[connection_id]
            if identity is not None:
                if identity != connection.user:
                    if not login:
                        raise PermissionError("A session login is required")
                    self.change_connection_user(connection_id, identity)
            elif connection.user != f"guest_{connection_id}":
                raise PermissionError("Visit owner does not match the session")
            connection.expires_at = now + ttl

    def new_page(self, page_id: str, *, connection_id: str, page_class: type[Page] = Page) -> None:
        with self._lock:
            connection = self._connections[connection_id]
            if page_id in self._pages:
                raise ValueError(f"Page already registered: {page_id!r}")
            self._pages[page_id] = _Page(self.new_store(), page_class)
            connection.pages.add(page_id)
            try:
                self.page_attached(self.page_state(page_id, connection_id=connection_id))
            except Exception:
                self.drop_page(page_id, connection_id=connection_id)
                raise

    def page_state(self, page_id: str, *, connection_id: str) -> PageState:
        """Resolve the page only within the supplied connection."""
        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or page_id not in connection.pages:
                raise UnknownPage(page_id)
            return PageState(
                page_id,
                connection_id,
                connection.user,
                self._pages[page_id].store,
                connection.store,
                self._users[connection.user].store,
            )

    def resolve_page(
        self,
        application: SpaApplicationBase,
        page_id: str,
        *,
        connection_id: str,
        request_pages: dict[str, Page] | None = None,
    ) -> Page:
        """Construct or reuse an instance under the same lock as store ownership."""
        with self._lock:
            self.page_state(page_id, connection_id=connection_id)
            record = self._pages[page_id]
            persistent = record.page_class.persistent
            if persistent is None:
                persistent = application.persistent
            if not persistent:
                if request_pages is None:
                    return record.page_class(application, page_id, connection_id)
                if page_id not in request_pages:
                    request_pages[page_id] = record.page_class(application, page_id, connection_id)
                return request_pages[page_id]
            if record.instance is None:
                record.instance = record.page_class(application, page_id, connection_id)
            return record.instance

    def release_page_instance(self, page_id: str, *, connection_id: str) -> None:
        """Evict a live instance while retaining its class, identity and stores."""
        with self._lock:
            self.page_state(page_id, connection_id=connection_id)
            self._pages[page_id].instance = None

    def page_attached(self, state: PageState) -> None:
        """Adapter hook to bind stores once for a logical page."""

    def page_user_changed(self, previous: PageState, current: PageState) -> None:
        """Adapter hook to rebind a registered page to its new user's store."""

    def page_detached(self, state: PageState) -> None:
        """Adapter hook to remove bindings when the page leaves the register."""

    def change_connection_user(self, connection_id: str, user: str) -> None:
        """Apply an already verified login to one connection and all its pages."""
        if not user or user.startswith("guest_"):
            raise ValueError("Login requires an authenticated user name")
        with self._lock:
            connection = self._connections[connection_id]
            previous = connection.user
            if previous == user:
                return
            before = [self.page_state(pid, connection_id=connection_id) for pid in connection.pages]
            source = self._users[previous]
            if user not in self._users and previous == f"guest_{connection_id}":
                self._users[user] = source
                del self._users[previous]
            else:
                if user not in self._users:
                    self._users[user] = _User(self.new_store())
                self._users[user].connections.add(connection_id)
                source.connections.remove(connection_id)
                if not source.connections:
                    del self._users[previous]
            connection.user = user
            failures = []
            for state in before:
                try:
                    self.page_user_changed(
                        state, self.page_state(state.page_id, connection_id=connection_id)
                    )
                except Exception as error:
                    # A failed binding must never expose the previous user's data.
                    failures.append(error)
                    try:
                        self.drop_page(state.page_id, connection_id=connection_id)
                    except Exception as detach_error:
                        failures.append(detach_error)
            if failures:
                raise ExceptionGroup("Failed to rebind SPA pages after login", failures)

    def drop_page(self, page_id: str, *, connection_id: str) -> None:
        """Close one tab. Its connection remains available for a later page."""
        with self._lock:
            state = self.page_state(page_id, connection_id=connection_id)
            try:
                self.page_detached(state)
            finally:
                self._connections[connection_id].pages.remove(page_id)
                del self._pages[page_id]

    def drop_connection(self, connection_id: str) -> None:
        """Release this visit, its pages and the owner's store on last disconnect."""
        with self._lock:
            connection = self._connections[connection_id]
            failures = []
            for page_id in tuple(connection.pages):
                try:
                    self.drop_page(page_id, connection_id=connection_id)
                except Exception as error:
                    failures.append(error)
            del self._connections[connection_id]
            owner = self._users[connection.user]
            owner.connections.remove(connection_id)
            if not owner.connections:
                del self._users[connection.user]
            if failures:
                raise ExceptionGroup("Failed to detach SPA pages", failures)
