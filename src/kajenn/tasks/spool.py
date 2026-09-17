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

"""TaskSpool — the file spool of batch tasks (folder-move model, ◆D22).

A batch task is a FOLDER on storage; its STATE is its POSITION in the tree. The
sender (an app handler, or the scheduler) creates the folder under ``pending/``;
the manager MOVES it into the assigned worker's folder; the worker runs it and
MOVES it to ``terminated/`` or ``aborted/``. A move is the only state transition
— one mount, one ``StorageNode.move_to``.

Layout (all on the plain ``site`` mount — these are service data, never secrets,
so NO encryption, exactly like the task STORE alongside it)::

    batches/
      pending/<task_id>/            sender writes here; the manager polls it
      active/<worker_id>/<task_id>/ the manager moves it here to assign it
      terminated/<task_id>/         completed
      aborted/<task_id>/            interrupted (user cancel / error / crash)

Inside a task folder::

    descriptor.json   the TaskDescriptor (identity + node_path + status + outcome)
    params.pkl        the call kwargs (pickled: may carry Python objects)
    progress.json     the worker's latest progress snapshot (single-writer: the worker)
    cancel            marker file: present == the user requested a stop
    result            the batch result (written by the worker on completion)

A ``batch_id`` is terminal (§5.7): it never resumes or relaunches itself. An
orphan (a task left under ``active/`` whose worker died) is settled ``aborted``
at boot; relaunch = a NEW id. There are no locks: safety is structural — one
single writer per state directory (sender on pending, one worker per active
subfolder), and a claim is an atomic rename.

The spool is the shared object the sender, the manager and the worker all use,
each with the methods that concern it. Storage is synchronous by construction
(core 1b): async callers dispatch blocking spool calls via ``server.run_sync``.
"""

from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genro_storage import StorageManager, StorageNode

__all__ = ["TaskSpool", "new_descriptor", "STATUSES"]

MOUNT = "site"          # plain mount: spool data are service data, never encrypted
ROOT = "batches"
PENDING = "pending"
ACTIVE = "active"
TERMINATED = "terminated"
ABORTED = "aborted"
STATUSES = (PENDING, ACTIVE, TERMINATED, ABORTED)

DESCRIPTOR_FILE = "descriptor.json"
PARAMS_FILE = "params.pkl"
PROGRESS_FILE = "progress.json"
CANCEL_FILE = "cancel"
RESULT_FILE = "result"


def _now() -> float:
    """Current epoch seconds (UTC-based)."""
    return datetime.now(timezone.utc).timestamp()


def new_descriptor(
    task_id: str,
    owner: str,
    mount: str,
    node_path: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build a fresh TaskDescriptor dict in the ``pending`` state.

    The autosufficient shape a worker needs to run the task without the sender's
    context: identity, the owner (for ``list_by_owner`` and progress), and how to
    resolve the code — ``mount`` (the app) + ``node_path`` (resolved by the worker
    with ``Router.node(node_path)``, callable, no HTTP). ``session_id`` is the
    launching MCP session, the key the executor publishes progress events under
    (None when the sender has no push channel). ``params`` travel in a separate
    pickled file, not inline here.
    """
    return {
        "task_id": task_id,
        "owner": owner,
        "mount": mount,
        "node_path": node_path,
        "session_id": session_id,
        "status": PENDING,
        "worker_id": None,
        "created_ts": _now(),
        "started_ts": None,
        "ended_ts": None,
        "outcome": None,
        "error": None,
    }


class TaskSpool:
    """The file spool: create in pending, move between states, read progress/cancel.

    Note:
        Holds the shared ``StorageManager`` (dual relationship: ``self.storage``),
        never raw paths. All I/O is plain synchronous storage calls.
    """

    __slots__ = ("storage",)

    def __init__(self, storage: StorageManager) -> None:
        """Bind the spool to the server's storage service (the ``site`` mount)."""
        self.storage = storage

    # -- folder addressing --

    def _task_node(self, status: str, task_id: str, worker_id: str | None = None) -> StorageNode:
        """The folder node of ``task_id`` in ``status`` (worker_id only for ACTIVE)."""
        if status == ACTIVE:
            return self.storage.node(f"{MOUNT}:{ROOT}/{ACTIVE}/{worker_id}/{task_id}")
        return self.storage.node(f"{MOUNT}:{ROOT}/{status}/{task_id}")

    def _state_dir(self, *parts: str) -> StorageNode:
        """A state directory node (e.g. ``pending/`` or ``active/<worker_id>/``)."""
        return self.storage.node(f"{MOUNT}:{ROOT}/" + "/".join(parts))

    # -- I/O helpers --

    def _read_json(self, node: StorageNode) -> dict[str, Any] | None:
        if not node.exists():
            return None
        return json.loads(node.read_text())

    def _write_json(self, node: StorageNode, data: dict[str, Any]) -> None:
        node.write_text(json.dumps(data, indent=2))

    def _find_folder(self, task_id: str) -> StorageNode | None:
        """Locate a task's folder in whatever state it currently sits (or None)."""
        for status in (PENDING, TERMINATED, ABORTED):
            node = self._task_node(status, task_id)
            if node.exists():
                return node
        active_root = self._state_dir(ACTIVE)
        if active_root.is_dir():
            for worker_dir in active_root.children():
                candidate = worker_dir.child(task_id)
                if candidate.exists():
                    return candidate
        return None

    # -- creation (used by the SENDER: an app handler, or the scheduler) --

    def create(self, descriptor: dict[str, Any], params: dict[str, Any]) -> str:
        """Create a task folder under ``pending/`` with its descriptor and params.

        Writes ``descriptor.json`` and the pickled ``params.pkl``. Returns the
        ``task_id``. This is a plain storage fact done by whoever launches the
        batch; the manager only ever MOVES the folder afterwards.
        """
        task_id = descriptor["task_id"]
        folder = self._task_node(PENDING, task_id)
        self._write_json(folder.child(DESCRIPTOR_FILE), descriptor)
        folder.child(PARAMS_FILE).write_bytes(pickle.dumps(params))
        return task_id

    def read_params(self, task_id: str) -> dict[str, Any]:
        """Unpickle a task's params (the worker reads them to run the task)."""
        folder = self._find_folder(task_id)
        if folder is None:
            raise LookupError(f"task not found: {task_id}")
        return pickle.loads(folder.child(PARAMS_FILE).read_bytes())

    # -- discovery (the manager polls pending; a worker polls its own active) --

    def list_pending(self) -> list[dict[str, Any]]:
        """The descriptors of every task under ``pending/`` (the manager's queue)."""
        return self._list_descriptors(self._state_dir(PENDING))

    def list_active(self, worker_id: str) -> list[dict[str, Any]]:
        """The descriptors of ``worker_id``'s active tasks (that worker's queue)."""
        return self._list_descriptors(self._state_dir(ACTIVE, worker_id))

    def _list_descriptors(self, directory: StorageNode) -> list[dict[str, Any]]:
        if not directory.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for folder in directory.children():
            if folder.is_dir():
                descriptor = self._read_json(folder.child(DESCRIPTOR_FILE))
                if descriptor is not None:
                    result.append(descriptor)
        return result

    # -- state transitions = MOVE --

    def assign(self, task_id: str, worker_id: str) -> None:
        """Move ``pending/<id>`` -> ``active/<worker_id>/<id>`` (the manager's act).

        Stamps ``status=active``, ``worker_id`` and ``started_ts`` on the
        descriptor, then moves the folder. The move is atomic on the mount.

        Raises:
            LookupError: if the task is not in ``pending``.
        """
        src = self._task_node(PENDING, task_id)
        if not src.exists():
            raise LookupError(f"task not pending: {task_id}")
        descriptor = self._read_json(src.child(DESCRIPTOR_FILE)) or {}
        descriptor.update(status=ACTIVE, worker_id=worker_id, started_ts=_now())
        self._write_json(src.child(DESCRIPTOR_FILE), descriptor)
        src.move_to(self._task_node(ACTIVE, task_id, worker_id))

    def settle(
        self, task_id: str, worker_id: str, outcome: str, error: str | None = None
    ) -> None:
        """Move ``active/<worker_id>/<id>`` -> ``terminated/`` or ``aborted/``.

        ``outcome`` == "ok" settles under ``terminated/``; anything else (error,
        aborted, orphan) settles under ``aborted/``. Stamps the descriptor with
        ``ended_ts``/``outcome``/``error``. Terminal: a settled task never moves
        again — re-settling raises because the folder is no longer active.

        Raises:
            LookupError: if the task is not active on ``worker_id``.
        """
        src = self._task_node(ACTIVE, task_id, worker_id)
        if not src.exists():
            raise LookupError(f"task not active on {worker_id}: {task_id}")
        target_status = TERMINATED if outcome == "ok" else ABORTED
        descriptor = self._read_json(src.child(DESCRIPTOR_FILE)) or {}
        descriptor.update(
            status=target_status, ended_ts=_now(), outcome=outcome, error=error
        )
        self._write_json(src.child(DESCRIPTOR_FILE), descriptor)
        src.move_to(self._task_node(target_status, task_id))

    # -- progress / cancel (files inside the task folder) --

    def write_progress(self, task_id: str, worker_id: str, data: dict[str, Any]) -> None:
        """Write the worker's latest progress snapshot (single-writer: the worker)."""
        folder = self._task_node(ACTIVE, task_id, worker_id)
        self._write_json(folder.child(PROGRESS_FILE), data)

    def read_progress(self, task_id: str) -> dict[str, Any] | None:
        """Read a task's latest progress snapshot, or None (monitor / SSE baseline)."""
        folder = self._find_folder(task_id)
        if folder is None:
            return None
        return self._read_json(folder.child(PROGRESS_FILE))

    def request_cancel(self, task_id: str) -> None:
        """Drop the ``cancel`` marker in the task folder (the user's stop request)."""
        folder = self._find_folder(task_id)
        if folder is None:
            raise LookupError(f"task not found: {task_id}")
        folder.child(CANCEL_FILE).write_text("")

    def is_cancelled(self, task_id: str) -> bool:
        """True if a ``cancel`` marker is present (the worker checks at each tick)."""
        folder = self._find_folder(task_id)
        return folder is not None and folder.child(CANCEL_FILE).exists()

    def write_result(self, task_id: str, worker_id: str, data: Any) -> None:
        """Persist the batch result in the task folder (pickled)."""
        folder = self._task_node(ACTIVE, task_id, worker_id)
        folder.child(RESULT_FILE).write_bytes(pickle.dumps(data))

    def read_result(self, task_id: str) -> Any:
        """Unpickle a task's result, or None if not written yet."""
        folder = self._find_folder(task_id)
        if folder is None:
            return None
        node = folder.child(RESULT_FILE)
        if not node.exists():
            return None
        return pickle.loads(node.read_bytes())

    # -- queries (the page and the monitor) --

    def get(self, task_id: str) -> dict[str, Any] | None:
        """The descriptor of ``task_id`` in whatever state it sits, or None."""
        folder = self._find_folder(task_id)
        if folder is None:
            return None
        return self._read_json(folder.child(DESCRIPTOR_FILE))

    def list_by_owner(self, owner: str) -> list[dict[str, Any]]:
        """Every task of ``owner`` across ALL states (pending/active/terminated/aborted).

        The user's full picture: queued, running, finished, aborted. Reads all
        state directories; the monitor uses the raw states, the page filters by
        owner here.
        """
        return [d for d in self._all_descriptors() if d.get("owner") == owner]

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        """Every task in one state (the monitor's pending / per-worker views).

        For ``active`` this spans all worker subfolders; each descriptor carries its
        ``worker_id``, so the monitor can group per worker.
        """
        if status == ACTIVE:
            active_root = self._state_dir(ACTIVE)
            if not active_root.is_dir():
                return []
            result: list[dict[str, Any]] = []
            for worker_dir in active_root.children():
                if worker_dir.is_dir():
                    result.extend(self._list_descriptors(worker_dir))
            return result
        return self._list_descriptors(self._state_dir(status))

    def _all_descriptors(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for status in STATUSES:
            result.extend(self.list_by_status(status))
        return result

    # -- housekeeping --

    def purge(self, task_id: str) -> bool:
        """Remove a settled task's folder entirely (tree-removal). True if removed."""
        folder = self._find_folder(task_id)
        if folder is None:
            return False
        folder.delete()
        return True
