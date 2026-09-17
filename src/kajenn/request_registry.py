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

"""Request registry: the current request and the in-flight picture.

``RequestRegistry`` is held by the server as a dual parent-child
(``self.server``, SPECIFICATION.md §4) and is the SINGLE writer of the
in-flight set (D12 spirit): the server registers a request on entry and
unregisters it on exit, around the http dispatch. Each registration is a
lightweight ``RegisteredRequest`` record (D18: slotted, high cardinality)
carrying a monotonic id, the scope type, the path, the start time, and the
request's cleanup callbacks. The server drains those cleanups in the http
``finally`` (``run_cleanups``) so any app — routed or bare — gets end-of-request
teardown (e.g. ``request.db`` closing its connection) for free.

The "current request" is exposed through a ContextVar that lives on the
registry INSTANCE (never at module level): ``register`` sets it and keeps the
reset token, ``unregister`` resets it. Because the ContextVar is an instance
attribute, deleting the server garbage-collects everything (instance-isolation
rule) and concurrent requests — each on its own task context — see their own
``current``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .server import BaseServer
    from .types import Scope

__all__ = ["RegisteredRequest", "RequestRegistry"]


class RegisteredRequest:
    """One in-flight request tracked by the registry (D18: slotted record).

    A snapshot taken at registration: the monotonic ``request_id``, the ASGI
    ``scope_type``, the ``path``, and ``started_at`` (``time.monotonic()``).
    Slotted because requests are high cardinality.

    It also owns the request's end-of-life cleanups: ``add_cleanup(fn)`` queues
    a zero-arg callback and ``run_cleanups()`` drains them LIFO at the end of the
    dispatch (the server calls it in the http ``finally``). The ``_cleanups``
    list is lazy — allocated only when the first callback is queued — so a
    request that registers none pays nothing.
    """

    __slots__ = ("_request_id", "_scope_type", "_path", "_started_at", "_cleanups")

    def __init__(self, request_id: int, scope_type: str, path: str) -> None:
        self._request_id = request_id
        self._scope_type = scope_type
        self._path = path
        self._started_at = time.monotonic()
        self._cleanups: list[Callable[[], Any]] | None = None

    @property
    def request_id(self) -> int:
        """Monotonic id assigned by the registry at registration."""
        return self._request_id

    @property
    def scope_type(self) -> str:
        """ASGI scope type of the request (``http``)."""
        return self._scope_type

    @property
    def path(self) -> str:
        """Request path at registration time."""
        return self._path

    @property
    def started_at(self) -> float:
        """``time.monotonic()`` captured at registration."""
        return self._started_at

    def add_cleanup(self, callback: Callable[[], Any]) -> None:
        """Queue a zero-arg ``callback`` to run at end of request (LIFO)."""
        if self._cleanups is None:
            self._cleanups = []
        self._cleanups.append(callback)

    def run_cleanups(self, error: BaseException | None = None) -> None:
        """Run queued cleanups LIFO, isolating and logging each one's exception.

        Called by the server in the http ``finally`` — so cleanups run whether
        the request succeeded or failed. ``error`` carries the terminating
        exception (``None`` on success) for error-aware cleanups; the base drain
        runs every callback regardless.
        """
        if self._cleanups is None:
            return
        for callback in reversed(self._cleanups):
            try:
                callback()
            except Exception:
                logging.getLogger(__name__).exception("Request cleanup %r failed", callback)

    def __repr__(self) -> str:
        return (
            f"<RegisteredRequest id={self.request_id} "
            f"type={self.scope_type} path={self.path!r}>"
        )


class RequestRegistry:
    """Tracks in-flight requests and the current one, owned by the server.

    The server is the single writer: it calls ``register(scope)`` on request
    entry and ``unregister(item)`` on exit. ``current`` reads the instance-owned
    ContextVar; ``in_flight`` counts the live requests; ``snapshot()`` lists them.
    """

    def __init__(self, server: BaseServer) -> None:
        self.server = server
        self._counter = 0
        self._in_flight: dict[int, RegisteredRequest] = {}
        self._tokens: dict[int, Token[RegisteredRequest | None]] = {}
        self._current: ContextVar[RegisteredRequest | None] = ContextVar(
            "current_request", default=None
        )
        self._empty = asyncio.Event()
        self._empty.set()

    @property
    def current(self) -> RegisteredRequest | None:
        """The request being handled in this task's context, or ``None``."""
        return self._current.get()

    @property
    def in_flight(self) -> int:
        """How many requests are registered right now."""
        return len(self._in_flight)

    def register(self, scope: Scope) -> RegisteredRequest:
        """Register a request from ``scope``, set ``current``, return the item."""
        self._counter += 1
        item = RegisteredRequest(self._counter, scope["type"], scope["path"])
        self._in_flight[item.request_id] = item
        self._tokens[item.request_id] = self._current.set(item)
        self._empty.clear()
        return item

    def unregister(self, item: RegisteredRequest) -> None:
        """Drop ``item`` from the in-flight set and reset ``current``."""
        self._in_flight.pop(item.request_id)
        self._current.reset(self._tokens.pop(item.request_id))
        if not self._in_flight:
            self._empty.set()

    async def await_drain(self, timeout: float | None = None) -> int:
        """Wait for the in-flight set to empty.

        Args:
            timeout: seconds to wait, or None to wait without a bound.

        Returns:
            How many requests are still in flight — zero when the drain
            completed, the count to report when the timeout ran out first.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._empty.wait(), timeout)
        return self.in_flight

    def snapshot(self) -> list[RegisteredRequest]:
        """A list of the currently in-flight requests (registration order)."""
        return list(self._in_flight.values())
