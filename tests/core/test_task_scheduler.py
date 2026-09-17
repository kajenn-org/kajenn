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

"""Tests for tasks.scheduler (core 1e Phase 4): the recurring loop.

Real objects, no mocks: a real ``AsgiServer`` (storage on tmp_path) whose
primary + mount declare tasks via ``@route(task=..., task_every=...)``. The
scan/sync_defaults/tick/run_now steps are driven DIRECTLY (no wall-clock
sleeping); a final lifespan-driven test asserts the manager starts and stops the
scheduler loop alongside the worker loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from genro_routes import route

from tests.storage_support import site_mounts

from kajenn import AsgiServer, RoutedApplication
from kajenn.tasks.scheduler import TaskScheduler

RUN_MARKS: list[str] = []


async def settle(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    """Wait until ``predicate()`` is truthy, sampling every ``interval`` seconds.

    The spawned ``_execute`` task crosses two thread-pool hops plus a storage
    write, so a fixed sleep is a race by construction. While polling, ANY
    exception counts as "not yet" — a store record read mid-write raises
    ``JSONDecodeError`` — but the caller's own asserts run after this returns,
    so a condition that never converges still fails loudly on the real check.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        await asyncio.sleep(interval)


class DemoApp(RoutedApplication):
    """Primary app: a default-scheduled task, a store-only task, a failing one."""

    @route(task="cleanup", task_every="1s")
    def cleanup(self) -> str:
        RUN_MARKS.append("cleanup")
        return "cleaned"

    @route(task="report")                 # schedulable only through a store record
    def report(self) -> str:
        RUN_MARKS.append("report")
        return "reported"

    @route(task="boom", task_every="1s")
    def boom(self) -> None:
        raise ValueError("scheduled boom")

    @route(task="slow", task_every="1s")
    async def slow(self) -> str:
        RUN_MARKS.append("slow")
        return "slow-done"


class MountApp(RoutedApplication):
    """A secondary mount contributing its own task (tests multi-app scan)."""

    @route(task="mounted", task_every="1s")
    def mounted(self) -> str:
        return "mounted"


@pytest.fixture(autouse=True)
def _clear_marks() -> None:
    RUN_MARKS.clear()


@pytest.fixture
def server(tmp_path: Path) -> AsgiServer:
    """A real AsgiServer: DemoApp primary + MountApp mounted, storage on tmp_path."""
    srv = AsgiServer(
        applications=[(DemoApp, {"mount": ""}), (MountApp, {"code": "extra"})],
        storage=site_mounts(tmp_path),
    )
    return srv


def scheduler(server: AsgiServer) -> TaskScheduler:
    """The server's scheduler (built with the manager, lazily)."""
    return server.tasks.scheduler


class TestScan:
    """The routing tree is the live registry."""

    def test_scan_collects_tasks_from_every_application(self, server: AsgiServer) -> None:
        registry = scheduler(server).scan()
        assert {"cleanup", "report", "boom", "slow", "mounted"} <= set(registry)
        assert callable(registry["cleanup"]["callable"])
        assert registry["cleanup"]["metadata"]["task_every"] == "1s"

    def test_duplicate_task_name_excluded(self, tmp_path: Path) -> None:
        class Dup(RoutedApplication):
            @route(task="twin")
            def one(self) -> None: ...

            @route(task="twin")
            def two(self) -> None: ...

        srv = AsgiServer(applications=[(Dup, {"mount": ""})], storage=site_mounts(tmp_path))
        assert "twin" not in srv.tasks.scheduler.scan()   # both excluded, no silent pick


class TestSyncDefaults:
    """A declared task_every/task_cron auto-creates the code-default record."""

    def test_default_record_created(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=1000.0)
        rec = sch.store.get("cleanup")
        assert rec is not None
        assert rec["kind"] == "every" and rec["spec"] == "1s"
        assert rec["next_run_ts"] == 1001.0            # now + 1s

    def test_store_only_task_gets_no_default(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=1000.0)
        assert sch.store.get("report") is None          # no task_every/task_cron

    def test_existing_record_wins(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.store.save({
            "code": "cleanup", "task_name": "cleanup", "target_kind": "task",
            "kwargs": {}, "kind": "every", "spec": "1s", "enabled": True,
            "next_run_ts": 42.0, "last_run_ts": None, "last_outcome": None,
            "last_error": None, "last_duration": None,
        })
        sch.sync_defaults(sch.scan(), now=1000.0)
        rec = sch.store.get("cleanup")
        assert rec is not None and rec["next_run_ts"] == 42.0   # user record preserved


class TestTick:
    """tick: scan -> sync_defaults -> run every due schedule."""

    async def test_due_task_runs_and_records_outcome(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=0.0)          # creates cleanup at 1.0
        await sch.tick()                                # now >> 1.0 -> due
        # The log append is _execute's LAST write (record first, log after),
        # so a non-empty log implies every earlier assertion target is settled.
        await settle(lambda: sch.store.read_log("cleanup"))
        rec = sch.store.get("cleanup")
        assert rec is not None and rec["last_outcome"] == "ok"
        assert "cleanup" in RUN_MARKS
        assert sch.store.read_log("cleanup")[-1]["outcome"] == "ok"

    async def test_failing_task_recorded_error(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=0.0)
        await sch.tick()
        await settle(lambda: (sch.store.get("boom") or {}).get("last_outcome"))
        rec = sch.store.get("boom")
        assert rec is not None and rec["last_outcome"] == "error"
        assert "scheduled boom" in rec["last_error"]

    async def test_async_task_runs(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=0.0)
        await sch.tick()
        await settle(lambda: "slow" in RUN_MARKS)
        assert "slow" in RUN_MARKS

    async def test_no_overlap(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=0.0)
        sch._running.add("cleanup")                     # simulate an in-flight run
        await sch.tick()
        await asyncio.sleep(0.05)
        assert "cleanup" not in RUN_MARKS               # skipped, not re-run

    async def test_orphan_record_never_runs(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.store.save({
            "code": "ghost", "task_name": "ghost", "target_kind": "task",
            "kwargs": {}, "kind": "every", "spec": "1s", "enabled": True,
            "next_run_ts": 0.0, "last_run_ts": None, "last_outcome": None,
            "last_error": None, "last_duration": None,
        })
        await sch.tick()
        await asyncio.sleep(0.05)
        rec = sch.store.get("ghost")
        assert rec is not None and rec["last_run_ts"] is None   # never executed


class TestRunNow:
    """run_now fires immediately with the no-overlap guard and outcome path."""

    def test_run_now_inline_when_no_loop(self, server: AsgiServer) -> None:
        # sync context: no running loop -> the schedule executes inline ("done")
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=0.0)
        assert sch.run_now("cleanup") == "done"
        assert "cleanup" in RUN_MARKS

    async def test_run_now_started_on_live_loop(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.start()
        try:
            sch.sync_defaults(sch.scan(), now=0.0)
            assert sch.run_now("cleanup") == "started"
            await settle(lambda: "cleanup" in RUN_MARKS)
            assert "cleanup" in RUN_MARKS
        finally:
            await sch.stop()

    def test_run_now_unknown_raises(self, server: AsgiServer) -> None:
        with pytest.raises(LookupError, match="schedule not found"):
            scheduler(server).run_now("nope")

    def test_run_now_orphan_raises(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.store.save({
            "code": "ghost", "task_name": "ghost", "target_kind": "task",
            "kwargs": {}, "kind": "every", "spec": "1s", "enabled": True,
            "next_run_ts": 0.0, "last_run_ts": None, "last_outcome": None,
            "last_error": None, "last_duration": None,
        })
        with pytest.raises(LookupError, match="orphan task"):
            sch.run_now("ghost")

    def test_run_now_running_skips(self, server: AsgiServer) -> None:
        sch = scheduler(server)
        sch.sync_defaults(sch.scan(), now=0.0)
        sch._running.add("cleanup")
        assert sch.run_now("cleanup") == "running"


class TestLifespanLifecycle:
    """The manager starts and stops the scheduler loop with the worker loop."""

    async def test_scheduler_started_and_stopped(self, server: AsgiServer) -> None:
        sent: list[dict[str, object]] = []
        queue = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sch_seen_running = asyncio.Event()

        async def receive() -> dict[str, object]:
            if queue[0]["type"] == "lifespan.shutdown":
                # by now startup has run: the scheduler loop task must be live
                assert server.tasks.scheduler._loop_task is not None
                assert not server.tasks.scheduler._loop_task.done()
                sch_seen_running.set()
            return queue.pop(0)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await server({"type": "lifespan"}, receive, send)
        assert sch_seen_running.is_set()
        assert server.tasks.scheduler._loop_task is None      # stopped at shutdown
