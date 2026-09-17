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

"""TaskStore — the persistent half of the task backbone (NO SQLite).

The routing tree declares tasks (the live registry); the store remembers the
SCHEDULES: when each one runs, whether it is enabled, what happened last time.
Filesystem on the storage layer behind a small contract — one JSON per schedule
record at ``tasks/<code>.json``, one capped JSONL log per task at
``tasks/logs/<task_name>.jsonl``. Records live plain on the ``site`` mount
(overridable with the ``tasks(mount=...)`` config element): a schedule is
operational data, not a credential — it shares the deployment tree with the
task SPOOL, and only the credential stores declare ``encrypted=True`` at their
write sites. A future Db-backed store swaps behind the same contract.

The record::

    {
      "code": "shop_cleanup",          # PK; the default row's code IS the task name
      "task_name": "shop_cleanup",     # joins the live registry (the tree)
      "target_kind": "task",           # "path" reserved for a future privileged mode
      "kwargs": {},                    # passed to the callable
      "kind": "every",                 # every | cron | at
      "spec": "15m",                   # interval | cron string | ISO list
      "enabled": true,
      "next_run_ts": 1783900000.0,     # epoch; null = nothing due (exhausted "at")
      "last_run_ts": null, "last_outcome": null,
      "last_error": null, "last_duration": null
    }

The store never computes schedules (that is ``tasks.schedule``) and never
resolves callables (that is the scheduler): it persists, lists and filters.
``upsert_default`` is the code-default rule: created when absent, an existing
record ALWAYS wins (the config/preferences pattern). Storage is synchronous by
construction (core 1b): async callers dispatch store calls via ``server.run_sync``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genro_storage import StorageManager, StorageNode

__all__ = ["TaskStore", "FileTaskStore"]

DEFAULT_MOUNT = "site"
TASKS_DIR = "tasks"
LOG_CAP = 200  # JSONL lines kept per task


class TaskStore:
    """Contract for the schedule store (see module docstring)."""

    __slots__ = ()

    def load_all(self) -> list[dict[str, Any]]:
        """Return every schedule record."""
        raise NotImplementedError

    def get(self, code: str) -> dict[str, Any] | None:
        """Return the record for ``code`` or None."""
        raise NotImplementedError

    def save(self, record: dict[str, Any]) -> None:
        """Persist ``record`` (create or update), atomically."""
        raise NotImplementedError

    def delete(self, code: str) -> bool:
        """Remove ``code``. True if a record was removed, False if absent."""
        raise NotImplementedError

    def append_log(self, task_name: str, entry: dict[str, Any]) -> None:
        """Append one JSONL line to the task's log, keeping the last LOG_CAP."""
        raise NotImplementedError

    def read_log(self, task_name: str, limit: int = LOG_CAP) -> list[dict[str, Any]]:
        """The task's most recent log entries, oldest first."""
        raise NotImplementedError

    def due_rows(self, now: float) -> list[dict[str, Any]]:
        """The enabled records whose ``next_run_ts`` has expired."""
        return [
            record
            for record in self.load_all()
            if record.get("enabled")
            and record.get("next_run_ts") is not None
            and record["next_run_ts"] <= now
        ]

    def upsert_default(self, record: dict[str, Any]) -> None:
        """Create the code-default record when absent; an existing one wins."""
        if self.get(record["code"]) is None:
            self.save(record)

    def update_run(self, code: str, **fields: Any) -> None:
        """Merge run-outcome fields (``last_*``, ``next_run_ts``) into a record."""
        record = self.get(code)
        if record is None:
            return
        record.update(fields)
        self.save(record)

    def set_enabled(self, code: str, value: bool) -> dict[str, Any] | None:
        """Flip the enabled flag; the updated record, or None if absent."""
        record = self.get(code)
        if record is None:
            return None
        record["enabled"] = value
        self.save(record)
        return record


class FileTaskStore(TaskStore):
    """One JSON per schedule + one JSONL log per task, on the storage layer.

    Note:
        The store holds the shared ``StorageManager`` instance (dual relationship:
        ``self.storage``), never raw paths. Schedules are operational data, not
        credentials: they are written plain.
    """

    __slots__ = ("storage", "_mount")

    def __init__(self, storage: StorageManager, mount: str | None = None) -> None:
        """Bind the store to the server's storage service.

        Args:
            storage: The server's StorageManager; task files live under
                ``<mount>:tasks/``.
            mount: Explicit mount override (the ``tasks(mount=...)`` config
                element); ``None`` keeps the default ``site``.
        """
        self.storage = storage
        self._mount = mount

    @property
    def mount(self) -> str:
        """The task-files mount: the explicit override, else ``site``."""
        return self._mount if self._mount is not None else DEFAULT_MOUNT

    def _tasks_node(self) -> StorageNode:
        """The ``<mount>:tasks`` directory node."""
        return self.storage.node(f"{self.mount}:{TASKS_DIR}")

    def _record_node(self, code: str) -> StorageNode:
        """The node for one schedule's JSON file."""
        return self.storage.node(f"{self.mount}:{TASKS_DIR}/{code}.json")

    def _log_node(self, task_name: str) -> StorageNode:
        """The node for one task's JSONL log."""
        return self.storage.node(f"{self.mount}:{TASKS_DIR}/logs/{task_name}.jsonl")

    def load_all(self) -> list[dict[str, Any]]:
        """Read every ``*.json`` record under ``<mount>:tasks/``."""
        directory = self._tasks_node()
        if not directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for child in directory.children():
            if child.ext == "json":
                records.append(json.loads(child.read_text()))
        return records

    def get(self, code: str) -> dict[str, Any] | None:
        """Read one schedule's record, or None if the file does not exist."""
        node = self._record_node(code)
        if not node.exists():
            return None
        result: dict[str, Any] = json.loads(node.read_text())
        return result

    def save(self, record: dict[str, Any]) -> None:
        """Persist ``record`` through the storage node.

        ``code`` is the file identity. ``created_at`` is kept from the existing
        record on update; ``updated_at`` is refreshed on every write.
        """
        code = record["code"]
        node = self._record_node(code)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = self.get(code)
        record["created_at"] = existing["created_at"] if existing else record.get(
            "created_at", now
        )
        record["updated_at"] = now
        node.write_text(json.dumps(record, indent=2))

    def delete(self, code: str) -> bool:
        """Remove one schedule's file. True if it existed, False otherwise."""
        node = self._record_node(code)
        if not node.exists():
            return False
        node.delete()
        return True

    def append_log(self, task_name: str, entry: dict[str, Any]) -> None:
        """Append one JSONL line, rewriting with only the last LOG_CAP lines."""
        node = self._log_node(task_name)
        lines = node.read_text().splitlines() if node.exists() else []
        lines.append(json.dumps(entry))
        node.write_text("\n".join(lines[-LOG_CAP:]) + "\n")

    def read_log(self, task_name: str, limit: int = LOG_CAP) -> list[dict[str, Any]]:
        """The task's most recent ``limit`` entries, oldest first."""
        node = self._log_node(task_name)
        if not node.exists():
            return []
        lines = node.read_text().splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()]
