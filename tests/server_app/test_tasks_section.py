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

"""Tests for the ``/_server/tasks`` section (core 1e Phase 7).

Real objects, no mocks: a real ``AsgiServer`` (storage on tmp_path) whose
primary declares a scheduled task, driven at the ASGI level. Auth is stamped by
the same test middleware ``test_tokens_section.py`` uses (a fixed ``Avatar`` on
``scope["auth"]``) — SUPERADMIN passes, everyone else is 403. The disabled
server (``tasks=False``) answers every endpoint with the ``{"error": ...}``
document at HTTP 200 (D26 fixed structure).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from genro_routes import route

from tests.storage_support import site_mounts

from kajenn import AsgiServer, Avatar, RoutedApplication
from kajenn_server_app import ServerApplication
from kajenn.middleware.base import BaseMiddleware
from kajenn.tasks import new_descriptor
from kajenn.types import Message, Scope

RUN_MARKS: list[str] = []


class TaskApp(RoutedApplication):
    """Primary app: a schedulable task and a plain handler for the spool."""

    @route(task="cleanup", task_every="1s")
    def cleanup(self) -> str:
        RUN_MARKS.append("cleanup")
        return "cleaned"

    @route()
    def sum_sync(self, a: int = 0, b: int = 0) -> int:
        return a + b


class StampAuthMiddleware(BaseMiddleware):
    """Test middleware stamping a fixed avatar on every request."""

    middleware_order = 500

    def __init__(self, app: Any, server: Any, *, avatar: Avatar | None = None,
                 **options: Any) -> None:
        super().__init__(app, server, **options)
        self._avatar = avatar

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        scope["auth"] = self._avatar
        await self.app(scope, receive, send)


SUPERADMIN = Avatar("root", ["SUPERADMIN"])


def make_server(tmp_path: Path, avatar: Avatar | None = SUPERADMIN,
                tasks: Any = True) -> AsgiServer:
    """A real server: TaskApp primary, stamped auth, storage on tmp_path."""
    return AsgiServer(
        applications=[ServerApplication, (TaskApp, {"mount": ""})],
        storage=site_mounts(tmp_path),
        tasks=tasks,
        middleware={"stamp": {"avatar": avatar}},
        middleware_registry={"stamp": StampAuthMiddleware},
    )


async def drive(server: AsgiServer, path: str, method: str = "GET",
                body: dict | None = None) -> list[Message]:
    """Drive one request through the server at the ASGI level."""
    raw = json.dumps(body).encode() if body is not None else b""
    headers = [(b"content-type", b"application/json")] if body is not None else []
    clean_path, _, query = path.partition("?")
    scope: Scope = {"type": "http", "method": method, "path": clean_path,
                    "query_string": query.encode(), "headers": headers}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def status(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def payload(sent: list[Message]) -> Any:
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(body)


def stage(server: AsgiServer, task_id: str, owner: str = "alice") -> None:
    """Drop a pending spool task on the primary."""
    descriptor = new_descriptor(task_id, owner=owner, mount="", node_path="sum_sync")
    server.tasks.spool.create(descriptor, {"a": 2, "b": 3})


class TestAuthGate:
    """SUPERADMIN passes; an anonymous caller is 401, a plain user 403."""

    async def test_anonymous_is_401(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path, avatar=None), "/_server/tasks/list")
        assert status(sent) == 401

    async def test_plain_user_is_403(self, tmp_path: Path) -> None:
        server = make_server(tmp_path, avatar=Avatar("bob", ["staff"]))
        sent = await drive(server, "/_server/tasks/list")
        assert status(sent) == 403

    async def test_superadmin_passes(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/list")
        assert status(sent) == 200
        assert payload(sent) == {"schedules": []}


class TestSchedules:
    """CRUD + run_now + logs over the schedule store."""

    async def test_create_and_list(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        sent = await drive(server, "/_server/tasks/create", method="POST",
                           body={"code": "cleanup", "kind": "every", "spec": "1s"})
        record = payload(sent)["schedule"]
        assert record["task_name"] == "cleanup" and record["enabled"] is True
        assert record["next_run_ts"] is not None
        listed = payload(await drive(server, "/_server/tasks/list"))
        assert [r["code"] for r in listed["schedules"]] == ["cleanup"]

    async def test_create_requires_fields(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/create",
                           method="POST", body={"code": "x"})
        assert "required" in payload(sent)["error"]

    async def test_create_bad_spec_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/create",
                           method="POST", body={"code": "x", "kind": "every", "spec": "bogus"})
        assert "invalid every spec" in payload(sent)["error"]

    async def test_create_duplicate_is_error(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        body = {"code": "cleanup", "kind": "every", "spec": "1s"}
        await drive(server, "/_server/tasks/create", method="POST", body=body)
        sent = await drive(server, "/_server/tasks/create", method="POST", body=body)
        assert "already exists" in payload(sent)["error"]

    async def test_update_recomputes_next_run(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        await drive(server, "/_server/tasks/create", method="POST",
                    body={"code": "cleanup", "kind": "every", "spec": "1s"})
        sent = await drive(server, "/_server/tasks/update", method="POST",
                           body={"code": "cleanup", "spec": "2h"})
        record = payload(sent)["schedule"]
        assert record["spec"] == "2h"

    async def test_update_unknown_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/update",
                           method="POST", body={"code": "ghost"})
        assert "not found" in payload(sent)["error"]

    async def test_enable_disable(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        await drive(server, "/_server/tasks/create", method="POST",
                    body={"code": "cleanup", "kind": "every", "spec": "1s"})
        off = payload(await drive(server, "/_server/tasks/disable?code=cleanup",
                                  method="POST"))
        assert off["schedule"]["enabled"] is False
        on = payload(await drive(server, "/_server/tasks/enable?code=cleanup",
                                 method="POST"))
        assert on["schedule"]["enabled"] is True

    async def test_enable_unknown_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/enable?code=ghost",
                           method="POST")
        assert "not found" in payload(sent)["error"]

    async def test_run_now_executes_and_logs(self, tmp_path: Path) -> None:
        RUN_MARKS.clear()
        server = make_server(tmp_path)
        await drive(server, "/_server/tasks/create", method="POST",
                    body={"code": "cleanup", "kind": "every", "spec": "1s"})
        sent = await drive(server, "/_server/tasks/run_now?code=cleanup", method="POST")
        assert payload(sent) == {"code": "cleanup", "run": "done"}   # inline, no live loop
        assert "cleanup" in RUN_MARKS
        logged = payload(await drive(server, "/_server/tasks/logs?task_name=cleanup"))
        assert logged["log"][-1]["outcome"] == "ok"

    async def test_run_now_unknown_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/run_now?code=ghost",
                           method="POST")
        assert "not found" in payload(sent)["error"]

    async def test_delete(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        await drive(server, "/_server/tasks/create", method="POST",
                    body={"code": "cleanup", "kind": "every", "spec": "1s"})
        gone = payload(await drive(server, "/_server/tasks/delete?code=cleanup",
                                   method="POST"))
        assert gone == {"code": "cleanup", "deleted": True}
        again = payload(await drive(server, "/_server/tasks/delete?code=cleanup",
                                    method="POST"))
        assert again["deleted"] is False

    async def test_logs_requires_task_name(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/logs")
        assert "required" in payload(sent)["error"]


class TestSpool:
    """spool_list / progress / cancel / result over the batch folders."""

    async def test_spool_list_by_status_and_owner(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        stage(server, "t1", owner="alice")
        by_status = payload(await drive(server, "/_server/tasks/spool_list?status=pending"))
        assert [t["task_id"] for t in by_status["tasks"]] == ["t1"]
        by_owner = payload(await drive(server, "/_server/tasks/spool_list?owner=alice"))
        assert [t["task_id"] for t in by_owner["tasks"]] == ["t1"]

    async def test_spool_list_requires_filter(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/spool_list")
        assert "required" in payload(sent)["error"]

    async def test_spool_list_bad_status_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/spool_list?status=bogus")
        assert "unknown status" in payload(sent)["error"]

    async def test_progress_roundtrip(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        stage(server, "t1")
        server.tasks.spool.assign("t1", "local")
        server.tasks.publish_progress("t1", {"pct": 40})
        sent = await drive(server, "/_server/tasks/progress?task_id=t1")
        assert payload(sent) == {"task_id": "t1", "progress": {"pct": 40}}

    async def test_progress_unknown_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/progress?task_id=ghost")
        assert "not found" in payload(sent)["error"]

    async def test_cancel(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        stage(server, "t1")
        sent = await drive(server, "/_server/tasks/cancel?task_id=t1", method="POST")
        assert payload(sent) == {"task_id": "t1", "cancelled": True}
        assert server.tasks.spool.is_cancelled("t1") is True

    async def test_cancel_unknown_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/cancel?task_id=ghost",
                           method="POST")
        assert "not found" in payload(sent)["error"]

    async def test_result_roundtrip(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        stage(server, "t1")
        server.tasks.spool.assign("t1", "local")
        server.tasks.spool.write_result("t1", "local", {"answer": 42})
        sent = await drive(server, "/_server/tasks/result?task_id=t1")
        assert payload(sent) == {"task_id": "t1", "result": {"answer": 42}}


class TestSchedulesMoreErrors:
    """The remaining schedule/spool validation branches."""

    async def test_update_bad_spec_is_error(self, tmp_path: Path) -> None:
        # A schedule exists; updating it with an unparseable spec surfaces the
        # next_run ValueError as the error document (not a 500).
        server = make_server(tmp_path)
        await drive(server, "/_server/tasks/create", method="POST",
                    body={"code": "cleanup", "kind": "every", "spec": "1s"})
        sent = await drive(server, "/_server/tasks/update", method="POST",
                           body={"code": "cleanup", "kind": "every", "spec": "bogus"})
        assert status(sent) == 200
        assert "invalid every spec" in payload(sent)["error"]

    async def test_result_unknown_is_error(self, tmp_path: Path) -> None:
        sent = await drive(make_server(tmp_path), "/_server/tasks/result?task_id=ghost")
        assert status(sent) == 200
        assert "not found" in payload(sent)["error"]


class TestDisabled:
    """tasks=False: every endpoint answers the error document at 200."""

    async def test_all_endpoints_answer_error_shape(self, tmp_path: Path) -> None:
        server = make_server(tmp_path, tasks=False)
        for path, method in [
            ("/_server/tasks/list", "GET"),
            ("/_server/tasks/create", "POST"),
            ("/_server/tasks/update", "POST"),
            ("/_server/tasks/enable?code=x", "POST"),
            ("/_server/tasks/disable?code=x", "POST"),
            ("/_server/tasks/run_now?code=x", "POST"),
            ("/_server/tasks/delete?code=x", "POST"),
            ("/_server/tasks/logs?task_name=x", "GET"),
            ("/_server/tasks/spool_list?owner=x", "GET"),
            ("/_server/tasks/progress?task_id=x", "GET"),
            ("/_server/tasks/cancel?task_id=x", "POST"),
            ("/_server/tasks/result?task_id=x", "GET"),
        ]:
            body = {} if method == "POST" else None
            sent = await drive(server, path, method=method, body=body)
            assert status(sent) == 200
            assert payload(sent) == {"error": "Tasks are disabled"}
