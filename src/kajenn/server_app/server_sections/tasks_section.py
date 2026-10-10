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

"""The ``_server/tasks`` section: SUPERADMIN-gated task backbone endpoints.

A ``RoutingClass`` the ``ServerApplication`` attaches (``attach_section``), so
its routes live at ``/_server/tasks/...``. JSON endpoints ONLY: no HTML or JS
panel ships with this section. Two surfaces over the server's ``TaskManager``:

- **schedules** (the recurring scheduler's store): ``list``/``create``/
  ``update``/``enable``/``disable``/``run_now``/``delete``/``logs``;
- **spool** (the batch folders): ``spool_list`` (by ``owner`` or ``status``),
  ``progress``, ``cancel``, ``result``.

Every route is gated ``auth_rule="SUPERADMIN"``. A server composed without the
task backbone — or with ``tasks=False`` — answers every endpoint with the
``{"error": ...}`` document (HTTP 200): the section is ALWAYS declared, fixed
structure (D26), the payload states the availability.

Parent (dual relationship): the section holds its ``ServerApplication`` as
``self.application`` and reaches the manager via ``self.application.server.tasks``
(guarded by ``tasks_enabled`` — the property raises when disabled).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

from kajenn.tasks.schedule import TaskCadence
from kajenn.tasks.spool import STATUSES

if TYPE_CHECKING:
    from kajenn.tasks.manager import TaskManager
    from ..server_app import ServerApplication

__all__ = ["TasksSection"]

TASKS_DISABLED_ERROR = {"error": "Tasks are disabled"}

# The record fields a client may set through create/update (the run outcome
# fields — last_*, next_run_ts — belong to the scheduler, never to the wire).
EDITABLE_FIELDS = ("task_name", "kwargs", "kind", "spec", "enabled")


class TasksSection(RoutingClass):
    """The ``/_server/tasks`` endpoints over the server's task backbone.

    Note:
        Bound to its ``ServerApplication`` (dual relationship:
        ``self.application``); the manager, store, scheduler and spool are
        reached through ``self.application.server.tasks``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application

    @property
    def manager(self) -> TaskManager | None:
        """The server's TaskManager, or ``None`` when tasks are off/absent."""
        server = self.application.server
        if not getattr(server, "tasks_enabled", False):
            return None
        return server.tasks

    # -- schedules (the recurring scheduler's store) --

    @route(auth_rule="SUPERADMIN")
    def list(self) -> dict[str, Any]:
        """Every schedule record."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        return {"schedules": manager.task_store.load_all()}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def create(self, body_data: dict | None = None) -> dict[str, Any]:
        """Create a schedule: ``code``, ``kind``, ``spec`` required.

        ``task_name`` defaults to ``code``; ``kwargs`` and ``enabled`` are
        optional. ``next_run_ts`` is computed here (an invalid spec is the
        ``{"error": ...}`` answer, not a record).
        """
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        body = body_data or {}
        code = body.get("code") or ""
        kind, spec = body.get("kind"), body.get("spec")
        if not code or not kind or spec is None:
            return {"error": "code, kind and spec are required"}
        if manager.task_store.get(code) is not None:
            return {"error": f"schedule already exists: {code}"}
        try:
            first_run = TaskCadence(kind, spec).get_next_run(time.time())
        except ValueError as exc:
            return {"error": str(exc)}
        record = {
            "code": code,
            "task_name": body.get("task_name") or code,
            "target_kind": "task",
            "kwargs": body.get("kwargs") or {},
            "kind": kind,
            "spec": spec,
            "enabled": bool(body.get("enabled", True)),
            "next_run_ts": first_run,
            "last_run_ts": None,
            "last_outcome": None,
            "last_error": None,
            "last_duration": None,
        }
        manager.task_store.save(record)
        return {"schedule": record}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def update(self, body_data: dict | None = None) -> dict[str, Any]:
        """Merge the editable fields into a schedule (``code`` names it).

        A changed ``kind``/``spec`` recomputes ``next_run_ts``; the run-outcome
        fields are the scheduler's and never settable from the wire.
        """
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        body = body_data or {}
        code = body.get("code") or ""
        record = manager.task_store.get(code)
        if record is None:
            return {"error": f"schedule not found: {code}"}
        record.update({field: body[field] for field in EDITABLE_FIELDS if field in body})
        if "kind" in body or "spec" in body:
            try:
                record["next_run_ts"] = TaskCadence(record["kind"], record["spec"]).get_next_run(
                    time.time()
                )
            except ValueError as exc:
                return {"error": str(exc)}
        manager.task_store.save(record)
        return {"schedule": record}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def enable(self, code: str = "") -> dict[str, Any]:
        """Arm a schedule."""
        return self._set_enabled(code, value=True)

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def disable(self, code: str = "") -> dict[str, Any]:
        """Disarm a schedule (the record stays)."""
        return self._set_enabled(code, value=False)

    def _set_enabled(self, code: str, *, value: bool) -> dict[str, Any]:
        """Flip a schedule's enabled flag; unknown code is the error shape."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        record = manager.task_store.set_enabled(code, value)
        if record is None:
            return {"error": f"schedule not found: {code}"}
        return {"schedule": record}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def run_now(self, code: str = "") -> dict[str, Any]:
        """Fire a schedule immediately (same no-overlap guard as the loop)."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        try:
            outcome = manager.scheduler.run_now(code)
        except LookupError as exc:
            return {"error": str(exc)}
        return {"code": code, "run": outcome}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def delete(self, code: str = "") -> dict[str, Any]:
        """Remove a schedule record. ``deleted`` reports whether it existed."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        return {"code": code, "deleted": manager.task_store.delete(code)}

    @route(auth_rule="SUPERADMIN")
    def logs(self, task_name: str = "", limit: str = "") -> dict[str, Any]:
        """A task's capped JSONL run log, oldest first."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        if not task_name:
            return {"error": "task_name is required"}
        store = manager.task_store
        entries = store.read_log(task_name, int(limit)) if limit else store.read_log(task_name)
        return {"task_name": task_name, "log": entries}

    # -- spool (the batch folders) --

    @route(auth_rule="SUPERADMIN")
    def spool_list(self, owner: str = "", status: str = "") -> dict[str, Any]:
        """Task descriptors by ``owner`` OR by ``status`` (one filter required)."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        if owner:
            return {"tasks": manager.spool.list_by_owner(owner)}
        if status:
            if status not in STATUSES:
                return {"error": f"unknown status: {status} (want one of {', '.join(STATUSES)})"}
            return {"tasks": manager.spool.list_by_status(status)}
        return {"error": "owner or status is required"}

    @route(auth_rule="SUPERADMIN")
    def progress(self, task_id: str = "") -> dict[str, Any]:
        """A task's latest progress snapshot (``None`` until the worker writes)."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        if manager.spool.get(task_id) is None:
            return {"error": f"task not found: {task_id}"}
        return {"task_id": task_id, "progress": manager.spool.read_progress(task_id)}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def cancel(self, task_id: str = "") -> dict[str, Any]:
        """Drop the cancel marker (the worker honors it at its own pace)."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        try:
            manager.spool.request_cancel(task_id)
        except LookupError as exc:
            return {"error": str(exc)}
        return {"task_id": task_id, "cancelled": True}

    @route(auth_rule="SUPERADMIN")
    def result(self, task_id: str = "") -> dict[str, Any]:
        """A task's result (``None`` until written; JSON-encodable results only)."""
        manager = self.manager
        if manager is None:
            return TASKS_DISABLED_ERROR
        if manager.spool.get(task_id) is None:
            return {"error": f"task not found: {task_id}"}
        return {"task_id": task_id, "result": manager.spool.read_result(task_id)}
