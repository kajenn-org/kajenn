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

"""The database handler contract, the ``owner:db`` registry and the call boundary.

No driver: a ``RecordingHandler`` stands in for a backend and records the
thread and the call of every operation. The tests read what a caller sees —
the object a route gets, the events the handler received — through real
servers driven over http, WSX, kbus, the task executor and the lifespan.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from genro_routes import route

from tests.storage_support import site_mounts

from kajenn import (
    AsgiConfigBuilder,
    AsgiServer,
    BaseApplication,
    BaseServer,
    DbHandlerBase,
    RoutedApplication,
)
from kajenn.request import Request
from kajenn.request_registry import RegisteredRequest
from kajenn.tasks import WORKER_ID, LocalTaskExecutor, new_descriptor
from kajenn.wsx import WsxConnection, WsxEnvelope


def thread_name() -> str:
    return threading.current_thread().name


class FakeSession:
    """What ``take`` hands out: knows its database and call, has one terminal."""

    def __init__(self, name: str, call_id: int, params: dict[str, Any]) -> None:
        self.name = name
        self.call_id = call_id
        self.params = params

    def fetch(self) -> tuple[str, str]:
        return self.name, thread_name()

    def table(self, name: str) -> str:
        return f"table {name}"

    def hold(self, work: Any) -> None:
        work()


class RecordingHandler(DbHandlerBase):
    """A handler that records every operation: what, on which thread, for which call."""

    terminals = ("fetch", "hold")

    def __init__(self, server: BaseServer) -> None:
        super().__init__(server)
        self.params: dict[str, dict[str, Any]] = {}
        self.events: list[tuple[Any, ...]] = []

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        self.params[name] = params

    async def open(self) -> None:
        self.events.append(("open",))

    async def close(self) -> None:
        self.events.append(("close",))

    def take(self, name: str, call: RegisteredRequest) -> FakeSession:
        self.events.append(("take", name, call.request_id, thread_name()))
        return FakeSession(name, call.request_id, self.params[name])

    def release(self, call: RegisteredRequest) -> None:
        mine = sorted(name for name in call.databases if self.server.databases[name] is self)
        self.events.append(("release", tuple(mine), call.request_id, thread_name()))

    def of(self, kind: str) -> list[tuple[Any, ...]]:
        return [event for event in self.events if event[0] == kind]


class OtherHandler(RecordingHandler):
    """A second handler class: served by its own instance."""


def on_pool(event: tuple[Any, ...]) -> bool:
    return event[-1].startswith("kajenn-pool")


class Shop(RoutedApplication):
    """Routes that take databases from sync and async handlers."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: dict[str, Any] = {}
        self.taken = asyncio.Event()
        self.held = threading.Event()
        self.gate = threading.Event()

    def hold(self) -> None:
        """Blocking work on the pool: held until the test opens ``gate``."""
        self.held.set()
        self.gate.wait()
        self.server.databases["default"].events.append(("work_end", thread_name()))

    @route()
    def sync_take(self, _request=None) -> dict[str, Any]:
        first = _request.db()
        again = _request.db()
        self.seen["sync"] = (first, again)
        return {"name": first.name}

    @route()
    async def async_take(self, _request=None) -> dict[str, Any]:
        view = _request.async_db()
        name, thread = await view.fetch()
        self.seen["async"] = (view, _request.db(), thread)
        return {"name": name, "table": view.table("t")}

    @route()
    def named(self, _request=None) -> dict[str, Any]:
        self.seen["named"] = {
            "archive": _request.db("shop:archive"),
            "reports": _request.db("reports"),
            "audit": _request.db("audit"),
            "default": _request.db(),
        }
        return {}

    @route()
    async def hang(self, _request=None) -> None:
        _request.db()
        self.taken.set()
        await asyncio.Event().wait()

    @route()
    def sync_hold(self, _request=None) -> None:
        _request.db()
        self.hold()

    @route()
    async def async_hold(self, _request=None) -> None:
        await _request.async_db().hold(self.hold)

    @route()
    def task_sync(self) -> str:
        return self.db().name

    @route()
    async def task_async(self) -> str:
        name, _ = await self.async_db().fetch()
        return name


class DatabasesRecipe(AsgiConfigBuilder):
    """Two server databases and one application database on one handler; one apart."""

    default_config = False

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        dbs = cfg.databases(handler_class=RecordingHandler)
        dbs.database(code="default", dsn="main")
        dbs.database(code="reports", dsn="ro")
        dbs.database(code="audit", handler_class=OtherHandler, dsn="audit")
        apps = cfg.applications()
        apps.application(code="shop", mount="", app_class=Shop).databases().database(
            code="archive", dsn="arc"
        )


def configured() -> AsgiServer:
    return AsgiServer(config=DatabasesRecipe)


def shop(server: BaseServer) -> Shop:
    return server.applications["shop"]


async def drive_http(server: BaseServer, path: str) -> int:
    """One http GET through the full server dispatch; the answered status."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []}
    await server(scope, receive, send)
    return sent[0]["status"]


class TestRegistry:
    def test_names_follow_the_owner_rule_and_one_handler_serves_a_class(self) -> None:
        server = configured()
        assert set(server.databases) == {"default", "reports", "audit", "shop:archive"}
        handler = server.databases["default"]
        assert type(handler) is RecordingHandler
        assert server.databases["reports"] is handler
        assert server.databases["shop:archive"] is handler
        assert type(server.databases["audit"]) is OtherHandler
        assert list(server.db_handlers.values()) == [handler, server.databases["audit"]]
        assert handler.params == {
            "default": {"dsn": "main"},
            "reports": {"dsn": "ro"},
            "shop:archive": {"dsn": "arc"},
        }

    def test_an_application_section_inherits_the_server_default(self) -> None:
        assert type(configured().databases["shop:archive"]) is RecordingHandler

    def test_a_database_without_a_handler_class_is_a_boot_error(self) -> None:
        class NoHandler(AsgiConfigBuilder):
            default_config = False

            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.databases().database(code="default", dsn="x")
                cfg.applications().application(code="shop", mount="", app_class=Shop)

        with pytest.raises(ValueError, match="handler_class"):
            AsgiServer(config=NoHandler)

    def test_add_database_reuses_the_handler_and_refuses_a_claimed_name(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.add_database("one", {"n": 1}, RecordingHandler)
        server.add_database("two", {"n": 2}, RecordingHandler)
        assert server.databases["one"] is server.databases["two"]
        with pytest.raises(ValueError):
            server.add_database("one", {}, RecordingHandler)

    def test_add_database_takes_the_configured_default_class(self) -> None:
        server = configured()
        server.add_database("late", {"dsn": "late"})
        assert server.databases["late"] is server.databases["default"]

    def test_an_application_in_its_dedicated_process_mounts_the_server_databases_and_its_own(self) -> None:
        class TwoApps(AsgiConfigBuilder):
            default_config = False

            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.databases(handler_class=RecordingHandler).database(code="default")
                apps = cfg.applications()
                apps.application(code="shop", app_class=Shop).databases().database(code="a")
                apps.application(code="chat", app_class=Shop).databases().database(code="b")

        server = AsgiServer(config=TwoApps, role="application:shop")
        assert set(server.databases) == {"default", "shop:a"}

    def test_a_handler_that_implements_nothing_says_so(self) -> None:
        class Bare(DbHandlerBase):
            pass

        server = BaseServer()
        with pytest.raises(NotImplementedError, match="Bare does not implement add_database"):
            server.add_database("default", {}, Bare)
        handler = Bare(server)
        call = RegisteredRequest(1, "http", "/")
        assert handler.capabilities == frozenset()
        with pytest.raises(NotImplementedError, match="Bare does not implement take"):
            handler.take("default", call)
        with pytest.raises(NotImplementedError, match="Bare does not implement release"):
            handler.release(call)
        assert repr(handler.async_view(FakeSession("default", 1, {}))) == "AsyncDbView(FakeSession)"

    def test_a_base_server_refuses_a_database_without_a_handler_class(self) -> None:
        with pytest.raises(ValueError, match="has no handler_class"):
            BaseServer().add_database("default", {})

    def test_a_take_outside_a_registered_call_is_an_error(self) -> None:
        with pytest.raises(RuntimeError, match="outside a registered call"):
            configured().take_database("default")


class TestResolution:
    async def test_one_take_per_name_per_call_shared_by_db_and_async_db(self) -> None:
        server = configured()
        assert await drive_http(server, "/sync_take") == 200
        first, again = shop(server).seen["sync"]
        assert first is again
        assert first.name == "default"
        assert await drive_http(server, "/async_take") == 200
        view, plain, thread = shop(server).seen["async"]
        assert plain.name == "default"
        assert thread.startswith("kajenn-pool")
        handler = server.databases["default"]
        assert [event[1] for event in handler.of("take")] == ["default", "default"]

    async def test_names_resolve_by_rule_and_each_handler_serves_its_own(self) -> None:
        server = configured()
        await drive_http(server, "/named")
        seen = shop(server).seen["named"]
        assert {key: value.name for key, value in seen.items()} == {
            "archive": "shop:archive", "reports": "reports", "audit": "audit",
            "default": "default",
        }
        assert server.databases["audit"].of("take")[0][1] == "audit"
        assert [event[1] for event in server.databases["default"].of("release")] == [
            ("default", "reports", "shop:archive")
        ]
        assert [event[1] for event in server.databases["audit"].of("release")] == [("audit",)]

    async def test_the_application_default_wins_over_the_server_default(self) -> None:
        server = configured()
        server.add_database("shop:default", {})
        await drive_http(server, "/sync_take")
        assert shop(server).seen["sync"][0].name == "shop:default"

    async def test_db_name_picks_the_default_with_the_name_syntax(self) -> None:
        server = configured()
        shop(server).db_name = "shop:archive"
        await drive_http(server, "/sync_take")
        assert shop(server).seen["sync"][0].name == "shop:archive"

    async def test_an_unknown_name_is_an_error(self) -> None:
        server = configured()
        shop(server).db_name = "missing"
        assert await drive_http(server, "/sync_take") == 500

    async def test_the_async_view_runs_terminals_on_the_pool_and_passes_the_rest(self) -> None:
        server = configured()
        await drive_http(server, "/async_take")
        view = shop(server).seen["async"][0]
        assert view.table("x") == "table x"
        assert view.params == {"dsn": "main"}


class TestRelease:
    async def test_http_sync_releases_on_the_handler_thread(self) -> None:
        server = configured()
        await drive_http(server, "/sync_take")
        handler = server.databases["default"]
        [take], [release] = handler.of("take"), handler.of("release")
        assert release[1] == ("default",)
        assert on_pool(release) and release[3] == take[3]

    async def test_http_async_releases_on_a_pool_thread(self) -> None:
        server = configured()
        await drive_http(server, "/async_take")
        [release] = server.databases["default"].of("release")
        assert on_pool(release)

    async def test_a_call_that_takes_nothing_releases_nothing(self) -> None:
        class Plain(RoutedApplication):
            @route()
            def nothing(self) -> dict[str, Any]:
                return {}

        server = BaseServer(applications=[Plain(mount="")])
        server.add_database("default", {}, RecordingHandler)
        await drive_http(server, "/nothing")
        assert server.databases["default"].events == []

    async def test_a_cancelled_call_still_releases(self) -> None:
        server = configured()
        call = asyncio.create_task(drive_http(server, "/hang"))
        await shop(server).taken.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        [release] = server.databases["default"].of("release")
        assert on_pool(release)
        assert server.requests.in_flight == 0

    @pytest.mark.parametrize("path", ["/sync_hold", "/async_hold"])
    async def test_a_cancelled_call_releases_only_after_its_pool_work_ends(self, path) -> None:
        """#89: the release never overlaps the blocking work still using the session."""
        server = configured()
        app = shop(server)
        handler = server.databases["default"]
        call = asyncio.create_task(drive_http(server, path))
        try:
            assert await asyncio.to_thread(app.held.wait, 2)
            call.cancel()
            done, _ = await asyncio.wait({call}, timeout=0.1)
            assert not done
            assert handler.of("release") == []
        finally:
            app.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert [event[0] for event in handler.events] == ["take", "work_end", "release"]
        assert server.requests.in_flight == 0

    @pytest.mark.parametrize("message_id", ["m1", None])
    async def test_a_wsx_message_releases_with_and_without_id(self, message_id) -> None:
        server = configured()
        fields = {"id": message_id} if message_id else {}
        texts = [WsxEnvelope(method="WSK", path="/sync_take", **fields).encode()]
        incoming = [{"type": "websocket.connect"}]
        incoming += [{"type": "websocket.receive", "text": text} for text in texts]

        async def receive() -> dict[str, Any]:
            if incoming:
                return incoming.pop(0)
            for _ in range(50):
                if server.databases["default"].of("release"):
                    break
                await asyncio.sleep(0.01)
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message: dict[str, Any]) -> None:
            pass

        scope = {
            "type": "websocket", "path": "/_wsx", "headers": [(b"host", b"example.org")],
            "query_string": b"", "subprotocols": [],
        }
        await WsxConnection(server, scope, receive, send).serve()
        [release] = server.databases["default"].of("release")
        assert release[1] == ("default",) and on_pool(release)

    async def test_a_kbus_call_releases(self) -> None:
        server = configured()
        assert await server.kbus_call("/sync_take") == {"name": "default"}
        [release] = server.databases["default"].of("release")
        assert on_pool(release)

    @pytest.mark.parametrize("node_path", ["task_sync", "task_async"])
    async def test_a_task_releases(self, tmp_path: Path, node_path: str) -> None:
        server = AsgiServer(applications=[(Shop, {"mount": ""})], storage=site_mounts(tmp_path))
        server.add_database("default", {}, RecordingHandler)
        executor = LocalTaskExecutor(server)
        executor.spool.create(new_descriptor("t1", owner="a", mount="", node_path=node_path), {})
        executor.spool.assign("t1", WORKER_ID)
        assert await executor.execute("t1", WORKER_ID) == "ok"
        assert executor.spool.read_result("t1") == "default"
        handler = server.databases["default"]
        [release] = handler.of("release")
        assert on_pool(release)
        assert server.requests.in_flight == 0

    async def test_a_release_that_raises_is_logged_and_the_next_handler_releases(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class FailingRelease(RecordingHandler):
            def release(self, call: RegisteredRequest) -> None:
                raise ConnectionError("release failed")

        class Recipe(DatabasesRecipe):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                dbs = cfg.databases(handler_class=FailingRelease)
                dbs.database(code="default")
                dbs.database(code="reports")
                dbs.database(code="audit", handler_class=OtherHandler)
                apps = cfg.applications()
                apps.application(code="shop", mount="", app_class=Shop).databases().database(
                    code="archive"
                )

        server = AsgiServer(config=Recipe)
        assert await drive_http(server, "/named") == 200
        assert server.databases["audit"].of("release")
        assert "Database release by" in caplog.text


class TestLifespan:
    async def test_open_and_close_run_once_around_the_hooks(self) -> None:
        events: list[tuple[Any, ...]] = []

        class Hooked(Shop):
            def on_startup(self) -> None:
                events.append(("on_startup",))

            def on_shutdown(self) -> None:
                events.append(("on_shutdown",))

        class Recipe(DatabasesRecipe):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.databases(handler_class=RecordingHandler).database(code="default")
                cfg.applications().application(code="shop", mount="", app_class=Hooked)

        server = AsgiServer(config=Recipe)
        handler = server.databases["default"]
        handler.events = events
        messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive() -> dict[str, Any]:
            return messages.pop(0)

        async def send(message: dict[str, Any]) -> None:
            pass

        await server({"type": "lifespan"}, receive, send)
        assert [event[0] for event in events] == ["open", "on_startup", "on_shutdown", "close"]

    async def test_a_failed_open_stops_the_startup_and_closes_what_was_opened(self) -> None:
        class Unreachable(OtherHandler):
            async def open(self) -> None:
                raise ConnectionError("database unreachable")

        server = BaseServer(applications=[Shop(mount="")])
        server.add_database("default", {}, RecordingHandler)
        server.add_database("archive", {}, Unreachable)
        messages = [{"type": "lifespan.startup"}]
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return messages.pop(0)

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await server({"type": "lifespan"}, receive, send)
        assert [message["type"] for message in sent] == ["lifespan.startup.failed"]
        assert "database unreachable" in sent[0]["message"]
        assert [event[0] for event in server.databases["default"].events] == ["open", "close"]

    async def test_a_close_that_raises_is_logged_and_the_next_handler_closes(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class FailingClose(RecordingHandler):
            async def close(self) -> None:
                raise ConnectionError("close failed")

        server = BaseServer(applications=[Shop(mount="")])
        server.add_database("default", {}, FailingClose)
        server.add_database("audit", {}, OtherHandler)
        messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive() -> dict[str, Any]:
            return messages.pop(0)

        async def send(message: dict[str, Any]) -> None:
            pass

        await server({"type": "lifespan"}, receive, send)
        assert server.databases["audit"].of("close")
        assert "FailingClose.close raised" in caplog.text


def test_a_request_bound_to_no_application_has_no_databases() -> None:
    request = Request({"type": "http", "path": "/", "headers": []}, None)
    with pytest.raises(RuntimeError, match="bound to no application"):
        request.db()
