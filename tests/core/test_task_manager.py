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

"""Tests for TaskManager + TaskMixin lifespan hook (core 1e Phase 3): the backbone.

Real objects, no mocks: a REAL ``AsgiServer`` (storage on tmp_path) hosting a
``RoutedApplication`` with a sync + an async ``@route`` handler, its real spool,
and the real ASGI ``lifespan`` protocol driven through ``server.__call__``.

Driving the loop end-to-end is the crux: ``server.__call__`` on a ``lifespan``
scope blocks until it receives ``lifespan.shutdown``, while the worker loop runs
in the background. The ``run_until_settled`` driver returns ``startup`` first,
then polls the spool and returns ``shutdown`` only once every staged task has
settled (with a timeout guard) — so the assertions run against a fully drained
spool and a cleanly stopped manager.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from genro_routes import route

from tests.storage_support import site_mounts

from kajenn import AsgiServer, BaseServer, RoutedApplication
from kajenn.application import BaseApplication
from kajenn.tasks import TaskManager, new_descriptor
from kajenn.tasks.manager import POLL_SECONDS
from kajenn.tasks.scheduler import TaskScheduler
from kajenn.tasks.store import FileTaskStore


class DemoApp(RoutedApplication):
    """Test app: a sync handler, an async handler, and a failing one."""

    @route()
    def sum_sync(self, a: int = 0, b: int = 0) -> int:
        return a + b

    @route()
    async def sum_async(self, a: int = 0, b: int = 0) -> int:
        return a + b

    @route()
    def boom(self) -> None:
        raise ValueError("handler exploded")


@pytest.fixture
def server(tmp_path: Path) -> AsgiServer:
    """A real AsgiServer whose primary is the DemoApp, storage on tmp_path."""
    return AsgiServer(applications=[(DemoApp, {"mount": ""})], storage=site_mounts(tmp_path))


def stage(server: AsgiServer, node_path: str, params: dict[str, int], task_id: str) -> None:
    """Drop a pending task on the primary (empty mount) for the loop to pick up."""
    descriptor = new_descriptor(task_id, owner="alice", mount="", node_path=node_path)
    server.tasks.spool.create(descriptor, params)


async def run_until_settled(server: AsgiServer, task_ids: list[str], timeout: float = 5.0) -> None:
    """Drive one startup/shutdown round-trip, releasing shutdown once tasks settle.

    Returns ``lifespan.startup`` first (arming the worker loop), then polls the
    spool and returns ``lifespan.shutdown`` only when every ``task_id`` has left
    the pending/active states (settled terminated or aborted) — or the timeout
    fires, so a stuck loop fails the test instead of hanging.
    """
    spool = server.tasks.spool
    started = False
    deadline = asyncio.get_running_loop().time() + timeout

    def settled() -> bool:
        for task_id in task_ids:
            descriptor = spool.get(task_id)
            if descriptor is None or descriptor["status"] not in ("terminated", "aborted"):
                return False
        return True

    async def receive() -> dict[str, object]:
        nonlocal started
        if not started:
            started = True
            return {"type": "lifespan.startup"}
        while not settled() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(POLL_SECONDS / 2)
        return {"type": "lifespan.shutdown"}

    async def send(message: dict[str, object]) -> None:
        pass

    await server({"type": "lifespan"}, receive, send)


class TestManagerWiring:
    """The manager owns the spool/executor/hub and reuses the storage seam."""

    def test_manager_owns_the_seam(self, server: AsgiServer) -> None:
        manager = server.tasks
        assert isinstance(manager, TaskManager)
        assert manager.server is server
        assert manager.spool is manager.executor.spool
        assert manager.spool.storage is server.storage
        assert isinstance(manager.scheduler, TaskScheduler)      # wired in Phase 4
        assert manager.scheduler.manager is manager
        assert isinstance(manager.task_store, FileTaskStore)
        assert manager.task_store.storage is server.storage
        assert manager.worker_id == "local"

    def test_manager_built_lazily_and_cached(self, server: AsgiServer) -> None:
        assert server.tasks is server.tasks         # same instance on re-access

    def test_no_mixin_no_tasks(self) -> None:
        plain = BaseServer(applications=[BaseApplication(mount="")])
        assert not hasattr(plain, "tasks_enabled")

    def test_disabled_server_raises_on_access(self, tmp_path: Path) -> None:
        disabled = AsgiServer(applications=[(DemoApp, {"mount": ""})], tasks=False,
                              storage=site_mounts(tmp_path))
        assert disabled.tasks_enabled is False
        with pytest.raises(RuntimeError, match="disabled"):
            disabled.tasks


class TestLifespanDrivenExecution:
    """The lifespan hook runs the worker loop: pending tasks get executed."""

    async def test_pending_task_runs_and_terminates(self, server: AsgiServer) -> None:
        stage(server, "sum_sync", {"a": 2, "b": 3}, "t1")
        await run_until_settled(server, ["t1"])
        descriptor = server.tasks.spool.get("t1")
        assert descriptor is not None
        assert descriptor["status"] == "terminated"
        assert server.tasks.spool.read_result("t1") == 5
        assert server.tasks.running is False        # loop stopped at shutdown

    async def test_async_and_failing_tasks_both_settle(self, server: AsgiServer) -> None:
        stage(server, "sum_async", {"a": 10, "b": 5}, "ok")
        stage(server, "boom", {}, "bad")
        await run_until_settled(server, ["ok", "bad"])
        ok = server.tasks.spool.get("ok")
        bad = server.tasks.spool.get("bad")
        assert ok is not None and ok["status"] == "terminated"
        assert server.tasks.spool.read_result("ok") == 15
        assert bad is not None and bad["status"] == "aborted"
        assert bad["error"] == "ValueError: handler exploded"

    async def test_loop_not_running_before_startup(self, server: AsgiServer) -> None:
        assert server.tasks.running is False        # armed but idle until lifespan


class TestNonLifespanPassThrough:
    """A disabled server passes the lifespan straight through (no loop)."""

    async def test_disabled_lifespan_still_acks(self, tmp_path: Path) -> None:
        disabled = AsgiServer(applications=[(DemoApp, {"mount": ""})], tasks=False,
                              storage=site_mounts(tmp_path))
        sent: list[dict[str, object]] = []
        queue = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive() -> dict[str, object]:
            return queue.pop(0)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await disabled({"type": "lifespan"}, receive, send)
        assert {"type": "lifespan.startup.complete"} in sent
        assert {"type": "lifespan.shutdown.complete"} in sent
