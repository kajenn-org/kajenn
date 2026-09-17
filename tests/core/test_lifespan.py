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

"""Lifespan protocol tests (SPECIFICATION.md §4): startup in order, shutdown
in reverse, one app's error does not block the others.

The ASGI lifespan protocol is driven directly through ``BaseServer.__call__``
(no uvicorn needed): a canned ``receive()`` queue delivers ``startup`` then
``shutdown``, and the recording apps append to a shared event list from their
hooks so ordering and error isolation can be asserted. The signal path is
driven the same way: ``UvicornServer.handle_exit`` is called by hand, which is
exactly what the C-level handler does, and no process is ever signalled.
"""

from __future__ import annotations

import asyncio
import signal

import uvicorn

import kajenn.server as server_module
from kajenn import BaseApplication, BaseServer
from kajenn.lifespan import QUITTING, STOPPING, FatalBootError, Lifespan
from kajenn.server import UvicornServer


class SyncRecordingApp(BaseApplication):
    """Test app recording sync ``on_startup``/``on_shutdown`` to a shared list.

    Constructor kwargs peeled here: ``name`` — identifies this app in the
    recorded events; ``events`` — the shared list; ``raise_on`` — an optional
    iterable of hook names on which this app raises instead of recording.
    """

    def __init__(self, **kwargs: object) -> None:
        self.name: str = kwargs.pop("name")
        self.events: list[str] = kwargs.pop("events")
        self.raise_on: frozenset[str] = frozenset(kwargs.pop("raise_on", ()))
        super().__init__(**kwargs)

    def on_startup(self) -> None:
        self._record_or_raise("on_startup")

    def on_shutdown(self) -> None:
        self._record_or_raise("on_shutdown")

    def _record_or_raise(self, hook: str) -> None:
        if hook in self.raise_on:
            raise RuntimeError(f"{self.name}.{hook} failed")
        self.events.append(f"{self.name}.{hook}")


class AsyncRecordingApp(SyncRecordingApp):
    """Same recording behaviour, as async hooks."""

    async def on_startup(self) -> None:
        self._record_or_raise("on_startup")

    async def on_shutdown(self) -> None:
        self._record_or_raise("on_shutdown")


async def drive_lifespan(
    server: BaseServer, messages: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Feed ``messages`` to ``server``'s lifespan scope; return what it sent."""
    queue = list(messages)
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return queue.pop(0)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await server({"type": "lifespan"}, receive, send)
    return sent


def startup_then_shutdown() -> list[dict[str, object]]:
    """A canned message queue: one full startup/shutdown round-trip."""
    return [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]


class TestHandlerWiring:
    def test_the_handler_holds_the_server_that_built_it(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        assert Lifespan(server).server is server
        assert server.lifespan.server is server


class TestOrdering:
    async def test_startup_runs_the_applications_in_registration_order(self) -> None:
        events: list[str] = []
        server = BaseServer(
            applications=[
                SyncRecordingApp(mount="", name="root", events=events),
                SyncRecordingApp(name="api", code="api", events=events),
                SyncRecordingApp(name="admin", code="admin", events=events),
            ]
        )

        sent = await drive_lifespan(server, startup_then_shutdown())

        startup_events = [e for e in events if e.endswith("on_startup")]
        assert startup_events == ["root.on_startup", "api.on_startup", "admin.on_startup"]
        assert {"type": "lifespan.startup.complete"} in sent

    async def test_shutdown_runs_in_reverse_order(self) -> None:
        events: list[str] = []
        server = BaseServer(
            applications=[
                SyncRecordingApp(mount="", name="root", events=events),
                SyncRecordingApp(name="api", code="api", events=events),
                SyncRecordingApp(name="admin", code="admin", events=events),
            ]
        )

        sent = await drive_lifespan(server, startup_then_shutdown())

        shutdown_events = [e for e in events if e.endswith("on_shutdown")]
        assert shutdown_events == ["admin.on_shutdown", "api.on_shutdown", "root.on_shutdown"]
        assert {"type": "lifespan.shutdown.complete"} in sent


class TestErrorIsolation:
    async def test_raising_sync_startup_hook_does_not_block_others(self) -> None:
        events: list[str] = []
        server = BaseServer(
            applications=[
                SyncRecordingApp(mount="", name="root", events=events),
                SyncRecordingApp(name="api", code="api", events=events, raise_on={"on_startup"}),
                SyncRecordingApp(name="admin", code="admin", events=events),
            ]
        )

        sent = await drive_lifespan(server, startup_then_shutdown())

        startup_events = [e for e in events if e.endswith("on_startup")]
        assert startup_events == ["root.on_startup", "admin.on_startup"]
        assert {"type": "lifespan.startup.complete"} in sent
        assert {"type": "lifespan.shutdown.complete"} in sent

    async def test_raising_async_shutdown_hook_does_not_block_others(self) -> None:
        events: list[str] = []
        server = BaseServer(
            applications=[
                AsyncRecordingApp(mount="", name="root", events=events),
                AsyncRecordingApp(name="api", code="api", events=events, raise_on={"on_shutdown"}),
                AsyncRecordingApp(name="admin", code="admin", events=events),
            ]
        )

        sent = await drive_lifespan(server, startup_then_shutdown())

        shutdown_events = [e for e in events if e.endswith("on_shutdown")]
        assert shutdown_events == ["admin.on_shutdown", "root.on_shutdown"]
        assert {"type": "lifespan.startup.complete"} in sent
        assert {"type": "lifespan.shutdown.complete"} in sent


class FatalStartupApp(SyncRecordingApp):
    """Its startup failure is declared fatal: the server must not start."""

    def on_startup(self) -> None:
        raise FatalBootError(f"{self.name}: the server must not start")


class TestFatalBoot:
    async def test_a_fatal_startup_failure_stops_the_server(self) -> None:
        events: list[str] = []
        server = BaseServer(
            applications=[
                SyncRecordingApp(mount="", name="root", events=events),
                FatalStartupApp(name="api", code="api", events=events),
                SyncRecordingApp(name="admin", code="admin", events=events),
            ]
        )

        sent = await drive_lifespan(server, [{"type": "lifespan.startup"}])

        assert sent == [
            {"type": "lifespan.startup.failed", "message": "api: the server must not start"}
        ]
        assert [e for e in events if e.endswith("on_startup")] == ["root.on_startup"]

    async def test_a_fatal_error_on_shutdown_keeps_the_ordinary_isolation(self) -> None:
        events: list[str] = []

        class FatalOnShutdown(SyncRecordingApp):
            def on_shutdown(self) -> None:
                raise FatalBootError(f"{self.name}.on_shutdown failed")

        server = BaseServer(
            applications=[
                SyncRecordingApp(mount="", name="root", events=events),
                FatalOnShutdown(name="api", code="api", events=events),
            ]
        )

        sent = await drive_lifespan(server, startup_then_shutdown())

        shutdown_events = [e for e in events if e.endswith("on_shutdown")]
        assert shutdown_events == ["root.on_shutdown"]
        assert {"type": "lifespan.shutdown.complete"} in sent


class TestShutdownState:
    async def test_the_shutdown_stops_accepting_before_any_hook_runs(self) -> None:
        seen: list[str] = []

        class Watcher(BaseApplication):
            def on_shutdown(self) -> None:
                seen.append(self.server.state)

        server = BaseServer(applications=[Watcher(mount="")])
        await Lifespan(server).shutdown()

        assert seen == [STOPPING]

    async def test_the_reload_trigger_makes_the_shutdown_a_quit(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.shutdown_mode = QUITTING
        await Lifespan(server).shutdown()
        assert server.state == QUITTING

    async def test_a_state_somebody_already_chose_is_respected(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.state = QUITTING
        await Lifespan(server).shutdown()
        assert server.state == QUITTING

    async def test_the_shutdown_drains_what_is_in_flight_before_the_hooks(self) -> None:
        import asyncio

        gate = asyncio.Event()
        in_flight_at_hook: list[int] = []

        class Held(BaseApplication):
            async def __call__(self, scope, receive, send) -> None:
                await gate.wait()
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

            def on_shutdown(self) -> None:
                in_flight_at_hook.append(self.server.requests.in_flight)

        server = BaseServer(applications=[Held(mount="")])

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            pass

        request = asyncio.ensure_future(server({"type": "http", "path": "/"}, receive, send))
        await asyncio.sleep(0)
        closing = asyncio.ensure_future(Lifespan(server).shutdown())
        await asyncio.sleep(0)
        gate.set()
        await closing
        await request

        assert in_flight_at_hook == [0]


class TestLeavingAtSignal:
    """SIGINT/SIGTERM turns the state, at the signal and not at the hooks.

    ``UvicornServer`` owns the signal handlers: uvicorn's three shutdown steps
    (close the listeners, wait the grace, send the lifespan shutdown) then run
    over a server that already refuses new work.
    """

    def uvicorn_server(self, server: BaseServer) -> UvicornServer:
        return UvicornServer(server, uvicorn.Config(server))

    def test_the_signal_turns_the_state(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        self.uvicorn_server(server).handle_exit(signal.SIGTERM, None)
        assert server.state == STOPPING

    def test_the_signal_raises_uvicorn_own_exit_flag(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        uvicorn_server = self.uvicorn_server(server)
        uvicorn_server.handle_exit(signal.SIGINT, None)
        assert uvicorn_server.should_exit is True

    def test_the_signal_makes_a_quit_when_the_shutdown_mode_says_so(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.shutdown_mode = QUITTING
        self.uvicorn_server(server).handle_exit(signal.SIGTERM, None)
        assert server.state == QUITTING

    async def test_the_hooks_find_the_state_the_signal_chose(self) -> None:
        seen: list[str] = []

        class Watcher(BaseApplication):
            def on_shutdown(self) -> None:
                seen.append(self.server.state)

        server = BaseServer(applications=[Watcher(mount="")])
        self.uvicorn_server(server).handle_exit(signal.SIGTERM, None)
        await Lifespan(server).shutdown()

        assert seen == [STOPPING]

    def test_serve_builds_a_server_that_owns_the_signals(self, monkeypatch) -> None:
        built: list[UvicornServer] = []

        class XT_UvicornServer(UvicornServer):
            def run(self) -> None:
                built.append(self)

        monkeypatch.setattr(server_module, "UvicornServer", XT_UvicornServer)
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.serve()

        assert built[0].base_server is server
        assert server.uvicorn_server is built[0]


class TestLeavingEvent:
    """The one awaitable an endless response watches to end by itself."""

    def test_a_running_server_is_not_leaving(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        assert server.leaving.is_set() is False

    def test_the_event_is_set_when_the_state_leaves_running(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.state = STOPPING
        assert server.leaving.is_set() is True

    async def test_get_until_leaving_answers_the_queue_item(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        queue: asyncio.Queue[str] = asyncio.Queue()
        queue.put_nowait("event")
        assert await server.get_until_leaving(queue) == "event"

    async def test_get_until_leaving_answers_none_when_the_server_leaves(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        queue: asyncio.Queue[str] = asyncio.Queue()
        reader = asyncio.ensure_future(server.get_until_leaving(queue))
        await asyncio.sleep(0)
        server.state = STOPPING
        assert await asyncio.wait_for(reader, timeout=2.0) is None

    async def test_get_until_leaving_leaves_no_task_behind(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        queue: asyncio.Queue[str] = asyncio.Queue()
        before = len(asyncio.all_tasks())
        queue.put_nowait("event")
        await server.get_until_leaving(queue)
        await asyncio.sleep(0)
        assert len(asyncio.all_tasks()) == before
