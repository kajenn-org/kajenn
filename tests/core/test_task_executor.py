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

"""Tests for LocalTaskExecutor (core 1e Phase 2): in-process task execution.

Real objects, no mocks: a REAL ``AsgiServer`` (storage on tmp_path) hosting a
``RoutedApplication`` with sync + async ``@route`` handlers, and the real
``TaskSpool`` the executor opens over ``server.storage``. Each test drives the
full create → assign → execute lifecycle and asserts the settled folder position
(terminated / aborted), the round-tripped result and the error stamping. The
tests are ``async def`` so ``server.run_sync`` (the sync-handler pool seam) has a
live event loop, matching ``test_routed_application.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from genro_routes import route

from tests.storage_support import site_mounts

from kajenn import AsgiServer, RoutedApplication
from kajenn.tasks import WORKER_ID, LocalTaskExecutor, TaskSpool, new_descriptor


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


@pytest.fixture
def executor(server: AsgiServer) -> LocalTaskExecutor:
    """The executor bound to the live server (it opens the spool over server.storage)."""
    return LocalTaskExecutor(server)


def stage(spool: TaskSpool, node_path: str, params: dict[str, Any], task_id: str = "t1") -> str:
    """Create a pending task on the primary (empty mount) and assign it to WORKER_ID."""
    descriptor = new_descriptor(task_id, owner="alice", mount="", node_path=node_path)
    spool.create(descriptor, params)
    spool.assign(task_id, WORKER_ID)
    return task_id


class TestExecuteSuccess:
    """A handler that returns settles terminated, with the result written."""

    async def test_sync_handler_terminates_with_result(self, executor: LocalTaskExecutor) -> None:
        stage(executor.spool, "sum_sync", {"a": 2, "b": 3})
        outcome = await executor.execute("t1", WORKER_ID)
        assert outcome == "ok"
        assert executor.spool.read_result("t1") == 5
        descriptor = executor.spool.get("t1")
        assert descriptor is not None
        assert descriptor["status"] == "terminated"
        assert descriptor["outcome"] == "ok"
        assert descriptor["error"] is None

    async def test_async_handler_terminates_with_result(self, executor: LocalTaskExecutor) -> None:
        stage(executor.spool, "sum_async", {"a": 10, "b": 5})
        outcome = await executor.execute("t1", WORKER_ID)
        assert outcome == "ok"
        assert executor.spool.read_result("t1") == 15
        descriptor = executor.spool.get("t1")
        assert descriptor is not None
        assert descriptor["status"] == "terminated"


class TestExecuteFailure:
    """A handler that raises settles aborted, with the error stamped."""

    async def test_raising_handler_aborts_with_error(self, executor: LocalTaskExecutor) -> None:
        stage(executor.spool, "boom", {})
        outcome = await executor.execute("t1", WORKER_ID)
        assert outcome == "error"
        descriptor = executor.spool.get("t1")
        assert descriptor is not None
        assert descriptor["status"] == "aborted"
        assert descriptor["outcome"] == "error"
        assert "ValueError: handler exploded" == descriptor["error"]
        assert executor.spool.read_result("t1") is None


class TestResolveErrors:
    """Missing task and unknown mount both raise LookupError."""

    async def test_unknown_task_raises(self, executor: LocalTaskExecutor) -> None:
        with pytest.raises(LookupError):
            await executor.execute("nope", WORKER_ID)

    async def test_unknown_mount_aborts_with_lookup_error(self, executor: LocalTaskExecutor) -> None:
        descriptor = new_descriptor("t1", owner="alice", mount="ghost", node_path="sum_sync")
        executor.spool.create(descriptor, {})
        executor.spool.assign("t1", WORKER_ID)
        outcome = await executor.execute("t1", WORKER_ID)
        assert outcome == "error"
        aborted = executor.spool.get("t1")
        assert aborted is not None
        assert aborted["status"] == "aborted"
        assert aborted["error"].startswith("LookupError:")


class TestSpoolSeam:
    """The executor opens its own spool over the server's storage."""

    def test_spool_is_bound_to_server_storage(self, server: AsgiServer, executor: LocalTaskExecutor) -> None:
        assert isinstance(executor.spool, TaskSpool)
        assert executor.spool.storage is server.storage
