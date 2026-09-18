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

"""ASGI lifespan protocol: ordered startup, reverse shutdown, error isolation.

``Lifespan`` is constructed with the server it manages (dual parent-child:
``self.server``, SPECIFICATION.md §4). On ``lifespan.startup`` it runs
``on_startup`` on the server's applications in registration order; on
``lifespan.shutdown`` it runs ``on_shutdown`` in REVERSE order. Hooks may
be sync or async, detected with ``inspect.iscoroutinefunction`` at call time.

A hook that raises is logged and the sequence CONTINUES: one app's error
never blocks the others, and uvicorn always receives the matching
``.complete`` message — app errors are isolated, never abort the protocol.
``FatalBootError`` is the ONE exception to that isolation: an ``on_startup``
hook raises it to declare its failure fatal, the startup stops there and
uvicorn receives ``lifespan.startup.failed``, so the server exits instead
of running without what the hook was there to build.

**The server's lifecycle states live here** — the lifespan is the lifecycle.
``RUNNING`` takes new requests in charge; anything else refuses them with 503.
Under a signal the state has already turned when the shutdown arrives here
(``UvicornServer.handle_exit``, ``server.py``); the shutdown is where it turns
for everybody else, and BEFORE any application's hook runs the server stops
accepting — ``QUITTING`` when whoever triggered the shutdown chose to save
(``shutdown_mode``, which the ``--reload`` launcher sets), ``STOPPING``
otherwise — and the in-flight requests are drained, bounded by
``SHUTDOWN_DRAIN_TIMEOUT_SECONDS``. Only then do the hooks run, in reverse
order: each application saves AFTER nothing new can arrive and nothing old is
still being served. A state somebody already set is respected: whoever writes
``state`` before the shutdown reaches here decides.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .application import BaseApplication
    from .server import BaseServer
    from .types import Receive, Scope, Send

RUNNING = "running"
"""The server takes new requests in charge. Any other state refuses them."""

QUITTING = "quitting"
"""The server is leaving and saving what it holds."""

STOPPING = "stopping"
"""The server is leaving without saving."""

#: How long the shutdown waits for the in-flight requests before proceeding
#: without them, in seconds. What is still in flight past it is counted in the
#: log and waited for no longer: the shutdown sequence goes on without it.
SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 10.0

__all__ = [
    "QUITTING",
    "RUNNING",
    "SHUTDOWN_DRAIN_TIMEOUT_SECONDS",
    "STOPPING",
    "FatalBootError",
    "Lifespan",
]


class FatalBootError(Exception):
    """Raised by an ``on_startup`` hook to declare its failure fatal to the server.

    The one exception ``_run_hook`` does not swallow on startup: the startup
    stops at the app that raised it and ``Lifespan.__call__`` answers
    ``lifespan.startup.failed`` (message = the exception text) instead of
    ``.complete``, so uvicorn exits. On shutdown it gets the ordinary
    logged-and-continue isolation: nothing may abort the shutdown sequence.
    """


class Lifespan:
    """ASGI lifespan handler, held by the server as a dual parent-child."""

    def __init__(self, server: BaseServer) -> None:
        self.server = server
        self._logger = logging.getLogger(__name__)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: ARG002
        """Drive the ASGI lifespan protocol: startup then shutdown, both acked.

        A ``FatalBootError`` out of the startup is the one unacked road: the
        answer is ``lifespan.startup.failed`` and the protocol ends there.
        """
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.startup()
                except FatalBootError as fatal:
                    await send({"type": "lifespan.startup.failed", "message": str(fatal)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def startup(self) -> None:
        """Run ``on_startup`` in registration order; ``FatalBootError`` stops it."""
        for app in self._apps():
            await self._run_hook(app, "on_startup")

    async def shutdown(self) -> None:
        """Stop accepting, drain what is in flight, THEN run the hooks in reverse.

        The state turns first — ``start_leaving``: to ``shutdown_mode`` when it
        is still ``RUNNING``, untouched when somebody already chose, which under
        a signal is the normal case since ``UvicornServer`` turned it there — so
        no application saves while new work can still arrive. The drain is bounded:
        past ``SHUTDOWN_DRAIN_TIMEOUT_SECONDS`` the count still in flight goes in
        the log and the sequence proceeds — the hooks are never held behind a
        request that does not end.
        """
        self.server.start_leaving()
        still_in_flight = await self.server.requests.await_drain(SHUTDOWN_DRAIN_TIMEOUT_SECONDS)
        if still_in_flight:
            self._logger.warning(
                "Shutdown: %s request(s) still in flight after %.1fs — proceeding",
                still_in_flight,
                SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
            )
        for app in reversed(self._apps()):
            await self._run_hook(app, "on_shutdown")

    def _apps(self) -> list[BaseApplication]:
        """The server's applications, in registration order."""
        return list(self.server.applications.values())

    async def _run_hook(self, app: BaseApplication, name: str) -> None:
        """Call ``app``'s hook; a raise is logged, the sequence continues.

        ``FatalBootError`` from ``on_startup`` propagates instead — the hook
        declared the server must not start. From ``on_shutdown`` it is an
        ordinary error: nothing may abort the shutdown sequence.
        """
        handler = getattr(app, name)
        try:
            if inspect.iscoroutinefunction(handler):
                await handler()
            else:
                handler()
        except FatalBootError:
            if name == "on_startup":
                raise
            self._logger.exception("%s.%s raised", type(app).__name__, name)
        except Exception:
            self._logger.exception("%s.%s raised", type(app).__name__, name)
