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

"""Tests for TaskSpool (core 1e Phase 1): the folder-move task model.

Real filesystem (a one-mount ``StorageManager`` on tmp_path), no mocks. Covers the descriptor
shape, the create → assign → progress → settle lifecycle, the folder positions
behind each state, cancel/result round-trips, terminal invariants (re-settle
raises), owner/status queries and purge.
"""

from pathlib import Path
from typing import Any

import pytest

from genro_storage import StorageManager

from tests.storage_support import site_storage

from kajenn.tasks import STATUSES, TaskSpool, new_descriptor


@pytest.fixture
def storage(tmp_path: Path) -> StorageManager:
    """Storage rooted in tmp_path (the spool lives on the 'site' mount)."""
    return site_storage(tmp_path)


@pytest.fixture
def spool(storage: StorageManager) -> TaskSpool:
    """A TaskSpool bound to the temporary storage."""
    return TaskSpool(storage)


def make_task(spool: TaskSpool, task_id: str = "t1", owner: str = "alice", **kwargs: Any) -> str:
    """Create a pending task with a standard descriptor; returns the task_id."""
    descriptor = new_descriptor(task_id, owner=owner, mount="shop", node_path="cleanup", **kwargs)
    return spool.create(descriptor, {"pkeys": [1, 2, 3]})


class TestDescriptor:
    """new_descriptor shape."""

    def test_fresh_descriptor_fields(self) -> None:
        desc = new_descriptor("t1", owner="alice", mount="shop", node_path="cleanup")
        assert desc["task_id"] == "t1"
        assert desc["owner"] == "alice"
        assert desc["mount"] == "shop"
        assert desc["node_path"] == "cleanup"
        assert desc["status"] == "pending"
        assert desc["worker_id"] is None
        assert desc["created_ts"] > 0
        assert desc["started_ts"] is None
        assert desc["ended_ts"] is None
        assert desc["outcome"] is None
        assert desc["error"] is None

    def test_session_id_default_none(self) -> None:
        desc = new_descriptor("t1", owner="alice", mount="shop", node_path="cleanup")
        assert desc["session_id"] is None

    def test_session_id_carried(self) -> None:
        desc = new_descriptor(
            "t1", owner="alice", mount="shop", node_path="cleanup", session_id="mcp-abc"
        )
        assert desc["session_id"] == "mcp-abc"

    def test_statuses_constant(self) -> None:
        assert STATUSES == ("pending", "active", "terminated", "aborted")


class TestLifecycle:
    """create → assign → progress → result → settle, and the folder positions."""

    def test_create_lands_in_pending(self, spool: TaskSpool, tmp_path: Path) -> None:
        make_task(spool)
        assert (tmp_path / "batches" / "pending" / "t1" / "descriptor.json").is_file()
        assert (tmp_path / "batches" / "pending" / "t1" / "params.pkl").is_file()
        assert [d["task_id"] for d in spool.list_pending()] == ["t1"]

    def test_read_params_round_trip(self, spool: TaskSpool) -> None:
        make_task(spool)
        assert spool.read_params("t1") == {"pkeys": [1, 2, 3]}

    def test_read_params_missing_raises(self, spool: TaskSpool) -> None:
        with pytest.raises(LookupError):
            spool.read_params("ghost")

    def test_assign_moves_to_active_worker(self, spool: TaskSpool, tmp_path: Path) -> None:
        make_task(spool)
        spool.assign("t1", "w1")
        assert not (tmp_path / "batches" / "pending" / "t1").exists()
        assert (tmp_path / "batches" / "active" / "w1" / "t1").is_dir()
        (active,) = spool.list_active("w1")
        assert active["status"] == "active"
        assert active["worker_id"] == "w1"
        assert active["started_ts"] is not None

    def test_assign_not_pending_raises(self, spool: TaskSpool) -> None:
        with pytest.raises(LookupError):
            spool.assign("ghost", "w1")

    def test_progress_round_trip(self, spool: TaskSpool) -> None:
        make_task(spool)
        spool.assign("t1", "w1")
        assert spool.read_progress("t1") is None
        spool.write_progress("t1", "w1", {"progress": 2, "maximum": 3})
        assert spool.read_progress("t1") == {"progress": 2, "maximum": 3}

    def test_read_progress_missing_task(self, spool: TaskSpool) -> None:
        assert spool.read_progress("ghost") is None

    def test_settle_ok_lands_in_terminated(self, spool: TaskSpool, tmp_path: Path) -> None:
        make_task(spool)
        spool.assign("t1", "w1")
        spool.settle("t1", "w1", "ok")
        assert (tmp_path / "batches" / "terminated" / "t1").is_dir()
        descriptor = spool.get("t1")
        assert descriptor is not None
        assert descriptor["status"] == "terminated"
        assert descriptor["outcome"] == "ok"
        assert descriptor["error"] is None
        assert descriptor["ended_ts"] is not None

    def test_settle_error_lands_in_aborted(self, spool: TaskSpool, tmp_path: Path) -> None:
        """The orphan/error path: any outcome != 'ok' settles aborted with the error stamped."""
        make_task(spool)
        spool.assign("t1", "w1")
        spool.settle("t1", "w1", "error", error="boom")
        assert (tmp_path / "batches" / "aborted" / "t1").is_dir()
        descriptor = spool.get("t1")
        assert descriptor is not None
        assert descriptor["status"] == "aborted"
        assert descriptor["outcome"] == "error"
        assert descriptor["error"] == "boom"

    def test_settle_not_active_raises(self, spool: TaskSpool) -> None:
        make_task(spool)
        with pytest.raises(LookupError):
            spool.settle("t1", "w1", "ok")

    def test_terminal_resettle_raises(self, spool: TaskSpool) -> None:
        """batch_id is terminal: a settled folder is no longer active, so settle raises."""
        make_task(spool)
        spool.assign("t1", "w1")
        spool.settle("t1", "w1", "ok")
        with pytest.raises(LookupError):
            spool.settle("t1", "w1", "ok")


class TestCancelAndResult:
    """Cancel marker and pickled result inside the task folder."""

    def test_cancel_round_trip(self, spool: TaskSpool) -> None:
        make_task(spool)
        assert spool.is_cancelled("t1") is False
        spool.request_cancel("t1")
        assert spool.is_cancelled("t1") is True

    def test_cancel_missing_raises(self, spool: TaskSpool) -> None:
        with pytest.raises(LookupError):
            spool.request_cancel("ghost")

    def test_is_cancelled_missing_task(self, spool: TaskSpool) -> None:
        assert spool.is_cancelled("ghost") is False

    def test_result_round_trip(self, spool: TaskSpool) -> None:
        make_task(spool)
        spool.assign("t1", "w1")
        spool.write_result("t1", "w1", {"done": 3, "ids": [1, 2, 3]})
        spool.settle("t1", "w1", "ok")
        assert spool.read_result("t1") == {"done": 3, "ids": [1, 2, 3]}

    def test_result_not_written(self, spool: TaskSpool) -> None:
        make_task(spool)
        assert spool.read_result("t1") is None

    def test_result_missing_task(self, spool: TaskSpool) -> None:
        assert spool.read_result("ghost") is None


class TestQueries:
    """Owner/status queries across states."""

    def test_get_missing(self, spool: TaskSpool) -> None:
        assert spool.get("ghost") is None

    def test_list_by_owner_spans_states(self, spool: TaskSpool) -> None:
        make_task(spool, "t1", owner="alice")
        make_task(spool, "t2", owner="alice")
        make_task(spool, "t3", owner="bob")
        spool.assign("t1", "w1")
        spool.settle("t1", "w1", "ok")
        spool.assign("t2", "w1")
        alice = {d["task_id"]: d["status"] for d in spool.list_by_owner("alice")}
        assert alice == {"t1": "terminated", "t2": "active"}
        assert [d["task_id"] for d in spool.list_by_owner("bob")] == ["t3"]

    def test_list_by_status_active_spans_workers(self, spool: TaskSpool) -> None:
        make_task(spool, "t1")
        make_task(spool, "t2")
        spool.assign("t1", "w1")
        spool.assign("t2", "w2")
        active = {d["task_id"]: d["worker_id"] for d in spool.list_by_status("active")}
        assert active == {"t1": "w1", "t2": "w2"}

    def test_list_by_status_empty_states(self, spool: TaskSpool) -> None:
        for status in STATUSES:
            assert spool.list_by_status(status) == []

    def test_list_active_unknown_worker(self, spool: TaskSpool) -> None:
        assert spool.list_active("ghost") == []


class TestPurge:
    """Tree removal of a task folder."""

    def test_purge_removes_tree(self, spool: TaskSpool, tmp_path: Path) -> None:
        make_task(spool)
        spool.assign("t1", "w1")
        spool.settle("t1", "w1", "ok")
        assert spool.purge("t1") is True
        assert not (tmp_path / "batches" / "terminated" / "t1").exists()
        assert spool.get("t1") is None

    def test_purge_missing(self, spool: TaskSpool) -> None:
        assert spool.purge("ghost") is False
