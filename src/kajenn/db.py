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

"""Database handler — the core's contract for the databases a server mounts.

A ``database`` declared in the config names a ``handler_class`` (its own, or the
default of its ``databases`` section). The server builds ONE handler per class,
``handler_class(server)``, and hands it each of its databases with
``add_database(name, params)``: the handler builds the real database from the
params. Names follow the owner rule: ``foo`` is a server database, ``chat:foo``
the database ``foo`` of the application ``chat``.

The core calls the handler at fixed points and never imports a driver:

- ``open()`` / ``close()`` — async, once per lifespan: ``open`` before the
  applications' ``on_startup``, ``close`` after their ``on_shutdown``.
- ``take(name, call)`` — sync, no I/O: the object the application uses for
  ``name`` during ``call`` (the call's ``RegisteredRequest``). The core asks
  once per name per call and keeps the answer on ``call.databases``.
- ``release(call)`` — sync, does I/O: end of a call that took from this
  handler. The core always runs it on a pool thread.
- ``async_view(obj)`` — the object an ``async def`` handler uses: by default an
  ``AsyncDbView``, whose ``terminals`` are awaitable and run on the pool. A
  natively async handler returns its own object.
- ``capabilities`` — the optional operations the handler offers
  (``migration_plan(name)`` / ``migrate(name)``).

The core adds no connection pool and commits nothing: connections and
transactions are the handler's. Backend handlers (asqueel, SQLAlchemy, Django)
ship apart, in the kajenn-db package: https://kajenn-db.readthedocs.io.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .request_registry import RegisteredRequest
    from .server import BaseServer

__all__ = ["AsyncDbView", "DbHandlerBase"]


class DbHandlerBase:
    """One handler per class, serving every database declared with that class.

    Subclass it for a backend: ``add_database``, ``take`` and ``release`` are
    the backend's own and raise ``NotImplementedError`` here; ``open`` and
    ``close`` do nothing by default. ``terminals`` names the methods
    ``async_view`` makes awaitable.
    """

    terminals: tuple[str, ...] = ()

    def __init__(self, server: BaseServer) -> None:
        self.server = server

    @property
    def capabilities(self) -> frozenset[str]:
        """The optional operations offered (``migration_plan``, ``migrate``); none here."""
        return frozenset()

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        """Receive database ``name`` with its config ``params``. Sync, no I/O."""
        raise NotImplementedError(f"{type(self).__name__} does not implement add_database")

    async def open(self) -> None:
        """Lifespan startup, before the applications' ``on_startup``."""

    async def close(self) -> None:
        """Lifespan shutdown, after the applications' ``on_shutdown``."""

    def take(self, name: str, call: RegisteredRequest) -> Any:
        """The object database ``name`` gives ``call``. Sync, no I/O."""
        raise NotImplementedError(f"{type(self).__name__} does not implement take")

    def release(self, call: RegisteredRequest) -> None:
        """Release what ``call`` took from this handler. Runs on a pool thread."""
        raise NotImplementedError(f"{type(self).__name__} does not implement release")

    def async_view(self, obj: Any) -> Any:
        """The awaitable face of ``obj``, the object ``take`` returned."""
        return AsyncDbView(obj, self)


class AsyncDbView:
    """``obj`` with its ``terminals`` awaitable, each run with ``server.run_sync``.

    Every other attribute — ``table()``, ``query()``, plain values — passes
    through as ``obj`` has it. ``run_sync`` copies the context into the pool
    thread, so a terminal sees the call's ContextVars.
    """

    __slots__ = ("_obj", "_handler")

    def __init__(self, obj: Any, handler: DbHandlerBase) -> None:
        self._obj = obj
        self._handler = handler

    def __getattr__(self, name: str) -> Any:
        # Underscore names are never forwarded: the slots are not set yet while
        # the object is being built, and the wrapped object's internals stay out.
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self._obj, name)
        if name not in self._handler.terminals:
            return value
        server = self._handler.server

        async def terminal(*args: Any, **kwargs: Any) -> Any:
            return await server.run_sync(lambda: value(*args, **kwargs))

        return terminal

    def __repr__(self) -> str:
        return f"{type(self).__name__}({type(self._obj).__name__})"
