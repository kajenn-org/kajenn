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

"""TaskManager — the server-owned task backbone (spool + executor + hub, ◆D22).

The spool and the executor are leaf pieces: the spool is a folder model on storage,
the executor runs one resolved task. The manager is what makes them LIVE — a single
object owned by the server (dual relationship: ``self.server``) that drives the
fire-and-forget worker loop for the whole process. It owns:

- ``spool`` — the ``LocalTaskExecutor``'s own spool over ``server.storage`` (the
  stateless seam: the same storage yields an equivalent spool, so the manager reuses
  the executor's rather than opening a second);
- ``executor`` — the ``LocalTaskExecutor`` bound to the live server;
- ``hub`` — the in-memory ``EventHub`` (the live progress courier for Phase 6);
- ``scheduler`` — the ``TaskScheduler`` (the recurring loop, over ``task_store``);
- ``task_store`` — the ``FileTaskStore`` (persistent schedules, over ``site:tasks``);
- ``worker_id`` — the single logical worker of the mono-process core (``"local"``).

``start()``/``stop()`` are the lifecycle the server's lifespan hook calls
(``TaskMixin.__call__``): ``start`` launches two tasks on the running loop — the
fire-and-forget ``_worker_loop`` and the ``scheduler`` tick loop — and ``stop``
cancels/awaits them both (in-flight executions are their own tasks and are left
to finish). Each loop mirrors the same shape: a failing pass is logged and never
kills the loop. Session GC is NOT a manager job: the store reaps expired
sessions itself, delta-checked at ``create`` time (a ratified revision of core
1e/◆D22 — the former ``_purge_loop`` is gone).

The worker loop is FIRE-AND-FORGET on the event loop: it polls ``list_pending``,
``assign``s each task to ``worker_id``, and launches ``executor.execute`` as its own
task. The D2 thread pool stays reserved for the blocking handler BODY inside
``execute`` (via ``server.run_sync``) — the loop itself never blocks the pool.
Distributed dispatch (worker processes, a batch commander) is out of scope (D22).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from .executor import WORKER_ID, LocalTaskExecutor
from .hub import EventHub
from .scheduler import TaskScheduler
from .spool import ACTIVE
from .store import FileTaskStore

if TYPE_CHECKING:
    from ..server import BaseServer

__all__ = ["TaskManager", "POLL_SECONDS"]

POLL_SECONDS = 0.5      # how often the worker loop polls the pending queue


class TaskManager:
    """Owns the spool/executor/hub and drives the fire-and-forget worker loop.

    Note:
        Bound to the live server (dual relationship: ``self.server``). The loop
        task and its event loop are held on the instance; ``running`` reports
        whether the loop task is live.
    """

    __slots__ = (
        "server", "executor", "hub", "task_store", "scheduler", "worker_id",
        "_loop", "_loop_task",
    )

    def __init__(self, server: BaseServer) -> None:
        """Bind to the live server and build the executor, hub, store and scheduler.

        ``server.tasks_config`` (the tuning dict the mixin peeled from a
        ``tasks=`` dict, empty otherwise) is applied here: ``mount`` overrides
        the store's by-keys choice, ``tick_seconds`` retunes the scheduler.
        """
        config = getattr(server, "tasks_config", None) or {}
        self.server = server
        self.executor = LocalTaskExecutor(server)
        self.hub = EventHub()
        self.task_store = FileTaskStore(server.storage, mount=config.get("mount"))
        self.scheduler = TaskScheduler(self)
        if config.get("tick_seconds") is not None:
            self.scheduler.tick_seconds = float(config["tick_seconds"])
        self.worker_id = WORKER_ID
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def spool(self) -> Any:
        """The task spool (the executor's own, over ``server.storage``)."""
        return self.executor.spool

    @property
    def running(self) -> bool:
        """Whether the worker loop task is currently live."""
        return self._loop_task is not None and not self._loop_task.done()

    # -- lifecycle (called by the server's lifespan hook) --

    def start(self) -> None:
        """Launch the worker and scheduler loops (lifespan startup)."""
        self._loop = asyncio.get_running_loop()
        self._loop_task = self._loop.create_task(self._worker_loop())
        self.scheduler.start()
        logging.getLogger(__name__).info("task manager started (worker %r)", self.worker_id)

    async def stop(self) -> None:
        """Cancel the worker and scheduler loops (lifespan shutdown).

        In-flight executions are their own tasks and are left to finish.
        """
        await self.scheduler.stop()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        self._loop_task = None
        logging.getLogger(__name__).info("task manager stopped")

    # -- the fire-and-forget worker loop --

    async def _worker_loop(self) -> None:
        """Poll pending forever; a failing poll is logged and never kills the loop."""
        while True:
            try:
                self._drain_pending()
            except Exception:
                logging.getLogger(__name__).exception("task worker poll failed")
            await asyncio.sleep(POLL_SECONDS)

    def _drain_pending(self) -> None:
        """Assign every pending task to the worker and launch its execution.

        Each task is claimed with an atomic ``assign`` (pending -> active) and its
        ``execute`` runs as its own loop task (fire-and-forget); the sync handler
        body inside ``execute`` goes to the pool, not this loop.
        """
        loop = self._loop
        assert loop is not None      # set by start() before the loop runs
        for descriptor in self.spool.list_pending():
            task_id = descriptor["task_id"]
            self.spool.assign(task_id, self.worker_id)
            loop.create_task(self.executor.execute(task_id, self.worker_id))

    # -- the A<->C progress seam (spool = source of truth, hub = live courier) --

    def publish_progress(self, task_id: str, data: dict[str, Any]) -> None:
        """Write a progress snapshot AND publish it live, in one paired call.

        The pairing rule of the push channel: every ``progress.json`` write also
        fans out on the hub, keyed by the descriptor's launching ``session_id``
        (``None`` = no push channel, the spool write still happens). The task
        must be ACTIVE on this manager's worker — writing progress anywhere else
        would create a stray spool folder.

        Raises:
            LookupError: if the task is unknown or not in the active state.
        """
        descriptor = self.spool.get(task_id)
        if descriptor is None or descriptor["status"] != ACTIVE:
            raise LookupError(f"task not active: {task_id}")
        self.spool.write_progress(task_id, self.worker_id, data)
        session_id = descriptor.get("session_id")
        if session_id is not None:
            self.hub.publish(session_id, {"type": "progress", "task_id": task_id, "data": data})
