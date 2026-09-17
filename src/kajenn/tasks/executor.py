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

"""LocalTaskExecutor — run one spool task in-process on the live server (◆D22).

A batch task names its code by ``mount`` + ``node_path`` (the app it lives in,
and the route path inside that app's router). Running it needs the app instance
— which needs the whole server (storage, dbs, sibling apps). In this mono-process
core the server is ALREADY live: the executor binds to it directly and reaches
the target app through ``server.application_at``. It never serves
HTTP; it only resolves and runs handlers. (Distributed execution — a worker
process that rebuilds the server from a config path — belongs to the
orchestration package, D22, and is out of scope here.)

Running a handler needs NO request context: a ``@route`` handler is a bound
method, so ``self.server`` / ``self.db`` are already reachable from the app
instance. The executor resolves the callable with ``app.route.node(node_path)``
(a ``RouterNode``, callable, no HTTP) and invokes it with the task's params —
the same async/sync split the dispatcher uses: an async handler stays on the
loop, a sync handler goes through ``server.run_sync`` (the Macro 1 pool
protocol; ``routed_application.py``/``applications/mcp.py`` are the mirrors).

The manager has already MOVED the task into ``active/<worker>/``; the executor
reads it there, runs it, and settles it once: any outcome (ok / error) moves the
folder to ``terminated`` / ``aborted`` and a ``batch_id`` never moves again
(§5.7 terminal-by-position). There is a single logical worker in the core,
``WORKER_ID`` == ``"local"``; the per-worker ``active/<worker>/`` structure stays
for D22 forward-compat.

The A<->C bridge (core 1e Phase 6): a descriptor carrying the launching MCP
``session_id`` gets its lifecycle published on ``server.tasks.hub`` — ``started``
before the run, ``settled`` (with outcome/error) after — so a subscribed SSE
stream follows the task live. ``session_id`` ``None`` = no push channel, no-op.
The spool stays the source of truth; the hub is only the live courier.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .spool import TaskSpool

if TYPE_CHECKING:
    from ..server import BaseServer

__all__ = ["LocalTaskExecutor", "WORKER_ID"]

WORKER_ID = "local"     # the single logical worker in the mono-process core


class LocalTaskExecutor:
    """Resolve a spool task by ``mount``/``node_path`` and run it on the live server.

    Note:
        Bound to the live server (dual relationship: ``self.server``); owns the
        ``TaskSpool`` over the server's ``site`` storage. Both attributes are the
        seam the ``TaskManager`` reuses (the spool is stateless — the same storage
        yields an equivalent spool).
    """

    __slots__ = ("server", "spool")

    def __init__(self, server: BaseServer) -> None:
        """Bind to the live server and open the spool over its storage."""
        self.server = server
        self.spool = TaskSpool(server.storage)

    def resolve(self, descriptor: dict[str, Any]) -> Any:
        """Return the callable ``RouterNode`` for a task descriptor.

        Looks the app up by its ``mount`` (``""`` is the root app, exactly as in
        the request demux) and resolves ``node_path`` in that app's router. The
        node is callable — invoking it runs the handler, no HTTP.

        Raises:
            LookupError: if no application answers ``mount``.
        """
        mount = descriptor["mount"]
        app = self.server.application_at(mount)
        if app is None:
            raise LookupError(f"no app mounted at {mount!r}")
        return app.route.node(descriptor["node_path"])

    async def execute(self, task_id: str, worker_id: str) -> str:
        """Run the task active on ``worker_id`` and settle it; return the outcome.

        Reads the descriptor and params from the spool, resolves the handler, runs
        it (async on the loop, sync on the pool via ``server.run_sync``), writes the
        result, and settles the folder to ``terminated`` (ok) or ``aborted`` (error).
        The outcome is ``"ok"`` or ``"error"``; on error the exception text is
        stamped on the descriptor via ``settle``.

        Raises:
            LookupError: if no task ``task_id`` exists in the spool.
        """
        descriptor = self.spool.get(task_id)
        if descriptor is None:
            raise LookupError(f"task not found: {task_id}")
        params = self.spool.read_params(task_id)
        outcome, error = "ok", None
        self._publish(descriptor, {"type": "started"})
        try:
            node = self.resolve(descriptor)
            if asyncio.iscoroutinefunction(node):
                result = await node(**params)
            else:
                result = await self.server.run_sync(lambda: node(**params))
            self.spool.write_result(task_id, worker_id, result)
        except Exception as exc:
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            logging.getLogger(__name__).exception("batch task %r failed", task_id)
        self.spool.settle(task_id, worker_id, outcome, error=error)
        self._publish(descriptor, {"type": "settled", "outcome": outcome, "error": error})
        return outcome

    def _publish(self, descriptor: dict[str, Any], event: dict[str, Any]) -> None:
        """Publish a lifecycle event on the hub, keyed by the launching session.

        The A<->C bridge: ``session_id`` is the launching MCP session stamped on
        the descriptor at ``create`` time; ``None`` means the sender has no push
        channel and publishing is a no-op (the hub itself no-ops without
        subscribers). The event always carries the ``task_id``.
        """
        session_id = descriptor.get("session_id")
        if session_id is None:
            return
        self.server.tasks.hub.publish(
            session_id, {"task_id": descriptor["task_id"], **event}
        )
