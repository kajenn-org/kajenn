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

"""TaskScheduler — the loop that runs due schedules (◆D22).

One asyncio task owned by the ``TaskManager`` (started with the manager on
lifespan startup, dies in shutdown), tick ~30s (cron granularity is the minute).
Each tick:

- **scan**: walk every mounted app's routing tree (``nodes(lazy=True,
  forbidden=True)`` — a gated route is still a declared task) collecting entries
  whose metadata carries ``task``. The tree IS the live registry: no register()
  API. A task_name declared twice is an ERROR — both are excluded, no silent
  pick. A record whose task_name nobody declares is an ORPHAN: never executed,
  never deleted (a UI concern).
- **sync defaults**: a declared ``task_every``/``task_cron`` auto-creates the
  missing code-default record (code = task_name); an existing record always wins
  (store override) — deleting it resets to the code default at the next scan.
- **run**: for each due record resolve the live callable and spawn it — async
  handlers on the loop, sync ones through ``server.run_sync`` (the D2 pool). No
  overlap: a schedule whose previous run is still in flight is skipped. Runs are
  SYSTEM calls: no middleware chain, no auth filters.

On completion the record's ``last_*``/``next_run_ts`` update and a JSONL line is
appended to the task's capped log. ``run_now`` fires a schedule immediately (from
another thread when the loop is up, inline otherwise) with the same no-overlap
guard and the same outcome path.

The store lives on the ``TaskManager`` (``manager.task_store``), not the server;
the scheduler reaches the live server through ``manager.server``. Store I/O and
sync task bodies are synchronous by construction (core 1b) and dispatched via
``server.run_sync`` (a zero-arg closure — ``run_sync`` takes no kwargs).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from .schedule import TaskCadence

if TYPE_CHECKING:
    from .manager import TaskManager

__all__ = ["TaskScheduler", "TICK_SECONDS"]

TICK_SECONDS = 30.0


class TaskScheduler:
    """The scheduling loop bound to its manager (dual relationship)."""

    __slots__ = ("manager", "tick_seconds", "_running", "_loop", "_loop_task")

    def __init__(self, manager: TaskManager) -> None:
        """Bind the scheduler to the manager owning the store, server and loop.

        Args:
            manager: The TaskManager; the store is ``manager.task_store``, the
                live server is ``manager.server``, the live registry is the
                mounted apps' routing trees.
        """
        self.manager = manager
        self.tick_seconds = TICK_SECONDS
        self._running: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def server(self) -> Any:
        """The live server (via the manager)."""
        return self.manager.server

    @property
    def store(self) -> Any:
        """The manager's TaskStore."""
        return self.manager.task_store

    @property
    def running(self) -> set[str]:
        """The codes with a run currently in flight (a copy)."""
        return set(self._running)

    # -- lifecycle --

    def start(self) -> None:
        """Start the tick loop on the running event loop (lifespan startup)."""
        self._loop = asyncio.get_running_loop()
        self._loop_task = self._loop.create_task(self._run_loop())
        logging.getLogger(__name__).info("task scheduler started (tick %ss)", self.tick_seconds)

    async def stop(self) -> None:
        """Cancel the tick loop (lifespan shutdown); in-flight runs finish."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        logging.getLogger(__name__).info("task scheduler stopped")

    async def _run_loop(self) -> None:
        """Tick forever; a failing tick is logged and never kills the loop."""
        while True:
            try:
                await self.tick()
            except Exception:
                logging.getLogger(__name__).exception("task scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    # -- the live registry --

    def _apps(self) -> Any:
        """Every application the server serves, in registration order."""
        return self.server.applications.values()

    def scan(self) -> dict[str, dict[str, Any]]:
        """task_name -> {"callable", "metadata"} from every mounted app's tree.

        Duplicates (the same task_name declared by two routes) are an explicit
        error: logged and EXCLUDED — no silent pick.
        """
        found: dict[str, dict[str, Any]] = {}
        duplicates: set[str] = set()
        for app in self._apps():
            router = getattr(app, "route", None)
            if router is None:
                continue
            for entry in self._walk(router):
                metadata = entry.get("metadata") or {}
                task_name = metadata.get("task")
                if not task_name:
                    continue
                if task_name in found:
                    duplicates.add(task_name)
                    continue
                found[task_name] = {"callable": entry["callable"], "metadata": metadata}
        for task_name in duplicates:
            found.pop(task_name, None)
            logging.getLogger(__name__).error(
                "duplicate task name %r: declared by more than one route", task_name
            )
        return found

    def _walk(self, router: Any) -> Any:
        """Yield every handler entry in a router tree (structural view)."""
        tree = router.nodes(lazy=True, forbidden=True)
        yield from (tree.get("entries") or {}).values()
        for sub_router in (tree.get("routers") or {}).values():
            yield from self._walk(sub_router)

    def sync_defaults(self, registry: dict[str, dict[str, Any]], now: float) -> None:
        """Auto-create the code-default record for declared default schedules."""
        for task_name, info in registry.items():
            metadata = info["metadata"]
            every, cron = metadata.get("task_every"), metadata.get("task_cron")
            if every and cron:
                logging.getLogger(__name__).error(
                    "task %r declares both task_every and task_cron", task_name
                )
                continue
            if not every and not cron:
                continue  # schedulable, but only through store records
            kind, spec = ("every", every) if every else ("cron", cron)
            try:
                first_run = TaskCadence(kind, spec).get_next_run(now)
            except ValueError:
                logging.getLogger(__name__).exception(
                    "task %r: invalid default schedule %r", task_name, spec
                )
                continue
            self.store.upsert_default(
                {
                    "code": task_name,
                    "task_name": task_name,
                    "target_kind": "task",
                    "kwargs": {},
                    "kind": kind,
                    "spec": spec,
                    "enabled": True,
                    "next_run_ts": first_run,
                    "last_run_ts": None,
                    "last_outcome": None,
                    "last_error": None,
                    "last_duration": None,
                }
            )

    # -- ticking and running --

    async def tick(self) -> None:
        """One pass: scan, sync defaults, spawn every due schedule.

        Store I/O (``sync_defaults`` writes, ``due_rows`` reads every record)
        runs on the server pool via ``run_sync``, never on the loop.
        """
        now = time.time()
        registry = self.scan()
        await self.server.run_sync(lambda: self.sync_defaults(registry, now))
        loop = asyncio.get_running_loop()
        due = await self.server.run_sync(lambda: self.store.due_rows(now))
        for row in due:
            if row.get("target_kind", "task") != "task":
                continue  # reserved for the future privileged mode
            code = row["code"]
            if code in self._running:
                continue  # no overlap
            info = registry.get(row["task_name"])
            if info is None:
                continue  # orphan: never runs it (a UI concern)
            self._running.add(code)
            loop.create_task(self._execute(row, info["callable"]))

    async def _execute(self, row: dict[str, Any], task_callable: Any) -> None:
        """Run one schedule and settle its outcome (record + JSONL log).

        A sync task body runs through ``server.run_sync`` (the D2 pool — same
        rule as the executor); the store writes go there too.
        """
        code, task_name = row["code"], row["task_name"]
        kwargs = row.get("kwargs") or {}
        started = time.time()
        outcome, error = "ok", None
        try:
            if asyncio.iscoroutinefunction(task_callable):
                await task_callable(**kwargs)
            else:
                await self.server.run_sync(lambda: task_callable(**kwargs))
        except Exception as exc:
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            logging.getLogger(__name__).exception(
                "task %r (schedule %r) failed", task_name, code
            )
        duration = round(time.time() - started, 3)
        try:
            next_ts = TaskCadence(row["kind"], row["spec"]).get_next_run(started)
        except ValueError as exc:
            next_ts = None
            outcome, error = "error", error or f"invalid schedule: {exc}"
        await self.server.run_sync(
            lambda: self.store.update_run(
                code,
                last_run_ts=started,
                last_outcome=outcome,
                last_error=error,
                last_duration=duration,
                next_run_ts=next_ts,
            )
        )
        await self.server.run_sync(
            lambda: self.store.append_log(
                task_name,
                {"ts": started, "code": code, "outcome": outcome, "error": error,
                 "duration": duration},
            )
        )
        self._running.discard(code)

    def run_now(self, code: str) -> str:
        """Fire a schedule immediately (the UI's Run now). Thread-safe.

        Returns:
            ``"started"`` (fired on the live loop), ``"done"`` (executed
            inline — no loop running, e.g. in tests), or ``"running"``
            (skipped: the previous run is still in flight).

        Raises:
            LookupError: If the code is unknown or its task_name is an orphan.
        """
        row = self.store.get(code)
        if row is None:
            raise LookupError(f"schedule not found: {code}")
        info = self.scan().get(row["task_name"])
        if info is None:
            raise LookupError(f"orphan task: {row['task_name']} (no mounted route declares it)")
        if code in self._running:
            return "running"
        self._running.add(code)
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._execute(row, info["callable"]), self._loop)
            return "started"
        asyncio.run(self._execute(row, info["callable"]))
        return "done"
