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

"""Tests for tasks.store (core 1e Phase 4): the persistent schedule store.

Real objects, no mocks: a real one-mount ``StorageManager`` on ``tmp_path``, one
JSON per schedule + one capped JSONL log per task. Covers the round-trip, the
concrete filter/upsert/update logic on the base contract, the log cap, and the
mount choice — ``site`` by default, the ``tasks(mount=...)`` override otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.storage_support import site_storage

from kajenn.tasks.store import LOG_CAP, FileTaskStore


def record(code: str, next_run_ts: float | None = 0.0, enabled: bool = True) -> dict[str, Any]:
    """A minimal schedule record for the store."""
    return {
        "code": code,
        "task_name": code,
        "target_kind": "task",
        "kwargs": {},
        "kind": "every",
        "spec": "15m",
        "enabled": enabled,
        "next_run_ts": next_run_ts,
        "last_run_ts": None,
        "last_outcome": None,
        "last_error": None,
        "last_duration": None,
    }


@pytest.fixture
def store(tmp_path: Path) -> FileTaskStore:
    """A FileTaskStore over the default ``site`` mount (schedules are plain data)."""
    return FileTaskStore(site_storage(tmp_path))


class TestRoundTrip:
    """save / get / load_all / delete."""

    def test_save_and_get(self, store: FileTaskStore) -> None:
        store.save(record("alpha"))
        got = store.get("alpha")
        assert got is not None
        assert got["code"] == "alpha" and got["spec"] == "15m"
        assert "created_at" in got and "updated_at" in got

    def test_get_absent_is_none(self, store: FileTaskStore) -> None:
        assert store.get("ghost") is None

    def test_load_all_empty(self, store: FileTaskStore) -> None:
        assert store.load_all() == []

    def test_load_all_lists_every_record(self, store: FileTaskStore) -> None:
        store.save(record("a"))
        store.save(record("b"))
        assert {r["code"] for r in store.load_all()} == {"a", "b"}

    def test_delete(self, store: FileTaskStore) -> None:
        store.save(record("gone"))
        assert store.delete("gone") is True
        assert store.get("gone") is None
        assert store.delete("gone") is False

    def test_created_at_preserved_on_update(self, store: FileTaskStore) -> None:
        store.save(record("x"))
        first = store.get("x")
        assert first is not None
        store.save(record("x"))            # overwrite
        second = store.get("x")
        assert second is not None
        assert second["created_at"] == first["created_at"]


class TestConcreteContract:
    """due_rows / upsert_default / update_run / set_enabled (backend-agnostic)."""

    def test_due_rows_filters(self, store: FileTaskStore) -> None:
        store.save(record("due", next_run_ts=10.0))
        store.save(record("future", next_run_ts=1_000.0))
        store.save(record("disabled", next_run_ts=10.0, enabled=False))
        store.save(record("exhausted", next_run_ts=None))
        due = {r["code"] for r in store.due_rows(now=100.0)}
        assert due == {"due"}

    def test_upsert_default_existing_wins(self, store: FileTaskStore) -> None:
        store.save(record("shared", next_run_ts=1.0))
        store.upsert_default(record("shared", next_run_ts=999.0))
        got = store.get("shared")
        assert got is not None and got["next_run_ts"] == 1.0   # existing kept

    def test_upsert_default_creates_when_absent(self, store: FileTaskStore) -> None:
        store.upsert_default(record("new"))
        assert store.get("new") is not None

    def test_update_run_merges_fields(self, store: FileTaskStore) -> None:
        store.save(record("run"))
        store.update_run("run", last_outcome="ok", last_duration=1.5, next_run_ts=500.0)
        got = store.get("run")
        assert got is not None
        assert got["last_outcome"] == "ok" and got["next_run_ts"] == 500.0

    def test_update_run_noop_when_absent(self, store: FileTaskStore) -> None:
        store.update_run("ghost", last_outcome="ok")     # no error, no record
        assert store.get("ghost") is None

    def test_set_enabled(self, store: FileTaskStore) -> None:
        store.save(record("toggle"))
        result = store.set_enabled("toggle", value=False)
        assert result is not None and result["enabled"] is False
        got = store.get("toggle")
        assert got is not None and got["enabled"] is False

    def test_set_enabled_absent_is_none(self, store: FileTaskStore) -> None:
        assert store.set_enabled("ghost", value=True) is None


class TestLog:
    """append_log caps at LOG_CAP; read_log returns the tail, oldest first."""

    def test_append_and_read(self, store: FileTaskStore) -> None:
        store.append_log("job", {"n": 1})
        store.append_log("job", {"n": 2})
        assert [e["n"] for e in store.read_log("job")] == [1, 2]

    def test_read_absent_is_empty(self, store: FileTaskStore) -> None:
        assert store.read_log("never") == []

    def test_cap_enforced(self, store: FileTaskStore) -> None:
        for i in range(LOG_CAP + 50):
            store.append_log("busy", {"n": i})
        entries = store.read_log("busy")
        assert len(entries) == LOG_CAP
        assert entries[0]["n"] == 50 and entries[-1]["n"] == LOG_CAP + 49

    def test_read_log_limit(self, store: FileTaskStore) -> None:
        for i in range(10):
            store.append_log("job", {"n": i})
        tail = store.read_log("job", limit=3)
        assert [e["n"] for e in tail] == [7, 8, 9]


class TestMountChoice:
    """The store lives on ``site`` unless the ``tasks(mount=...)`` override names another."""

    def test_default_mount_is_site(self, tmp_path: Path) -> None:
        store = FileTaskStore(site_storage(tmp_path))
        assert store.mount == "site"

    def test_explicit_mount_override_wins(self, tmp_path: Path) -> None:
        (tmp_path / "vault").mkdir()
        storage = site_storage(tmp_path)
        storage.add_mount(
            {"name": "vault", "protocol": "local", "base_path": str(tmp_path / "vault")}
        )
        store = FileTaskStore(storage, mount="vault")
        assert store.mount == "vault"
        store.save(record("scheduled"))
        assert (tmp_path / "vault" / "tasks" / "scheduled.json").exists()
