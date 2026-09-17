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

"""Request-registry tests (SPECIFICATION.md §4): two concurrent requests each
see their OWN request via the instance-owned ContextVar (the ContextVar test);
``in_flight`` counts both at a rendezvous and returns to zero afterwards;
``snapshot()`` exposes the slotted record fields; registration is cleaned up
even when the handler raises.

Driven at the ASGI level (no uvicorn): two concurrent tasks call the server and
an ``asyncio.Barrier`` holds both in-flight so the picture is observed
simultaneously — deterministic, and it mirrors the other Phase 0 tests.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kajenn import BaseApplication, BaseServer, MiddlewareMixin
from kajenn.request_registry import RegisteredRequest
from kajenn.server import QUITTING, REFUSED_RETRY_AFTER_SECONDS, RUNNING
from kajenn.types import Receive, Scope, Send


class RendezvousApp(BaseApplication):
    """Primary app recording the registry picture while blocked at a barrier.

    Constructor kwargs peeled here (cooperative chain): ``barrier`` (shared by
    both requests) and ``observed`` (a dict the app writes its picture into).
    """

    def __init__(self, **kwargs: Any) -> None:
        self.barrier: asyncio.Barrier = kwargs.pop("barrier")
        self.observed: dict[int, dict[str, Any]] = kwargs.pop("observed")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        registry = self.server.requests
        own = registry.current
        assert own is not None  # inside a request the ContextVar is always set
        await self.barrier.wait()  # both requests are registered past this point
        self.observed[own.request_id] = {
            "current_is_own": registry.current is own,
            "in_flight": registry.in_flight,
            "snapshot": registry.snapshot(),
            "path": own.path,
            "scope_type": own.scope_type,
        }
        await self.barrier.wait()  # hold both here so neither unregisters early
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class RaisingApp(BaseApplication):
    """Primary app that always raises — to test cleanup on the error path."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("boom")


class CleanupThenRaiseApp(BaseApplication):
    """Registers a request cleanup on ``current`` and then raises.

    Constructor kwarg peeled here: ``ran`` — a list the cleanup appends to, so
    the test can assert the cleanup ran despite the handler raising.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.ran: list[str] = kwargs.pop("ran")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        current = self.server.requests.current
        assert current is not None
        current.add_cleanup(lambda: self.ran.append("cleanup"))
        raise RuntimeError("boom")


async def drive(server: BaseServer, path: str) -> None:
    """Drive one http request through the server at the ASGI level."""

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    async def send(message: dict[str, object]) -> None:
        pass

    await server({"type": "http", "path": path}, receive, send)


class TestRegisteredRequest:
    def test_record_is_slotted_and_exposes_its_fields(self) -> None:
        item = RegisteredRequest(1, "http", "/x")
        assert item.request_id == 1
        assert item.scope_type == "http"
        assert item.path == "/x"
        assert isinstance(item.started_at, float)
        assert not hasattr(item, "__dict__")  # D18: slotted, no per-instance dict


class TestEmptyRegistry:
    def test_fresh_registry_is_empty(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        assert server.requests.current is None
        assert server.requests.in_flight == 0
        assert server.requests.snapshot() == []


class TestConcurrentRequests:
    async def test_each_request_sees_its_own_current_and_in_flight_counts_both(
        self,
    ) -> None:
        barrier = asyncio.Barrier(2)
        observed: dict[int, dict[str, Any]] = {}
        server = BaseServer(applications=[RendezvousApp(mount="", barrier=barrier, observed=observed)])

        await asyncio.gather(drive(server, "/a"), drive(server, "/b"))

        assert len(observed) == 2
        for picture in observed.values():
            assert picture["current_is_own"] is True  # the ContextVar test
            assert picture["in_flight"] == 2
        # monotonic ids: two distinct requests numbered 1 and 2
        assert set(observed) == {1, 2}
        # the two requests carry the two distinct paths
        assert {p["path"] for p in observed.values()} == {"/a", "/b"}

    async def test_snapshot_lists_the_in_flight_records(self) -> None:
        barrier = asyncio.Barrier(2)
        observed: dict[int, dict[str, Any]] = {}
        server = BaseServer(applications=[RendezvousApp(mount="", barrier=barrier, observed=observed)])

        await asyncio.gather(drive(server, "/a"), drive(server, "/b"))

        snapshot = next(iter(observed.values()))["snapshot"]
        assert len(snapshot) == 2
        assert all(isinstance(r, RegisteredRequest) for r in snapshot)
        assert all(r.scope_type == "http" for r in snapshot)
        assert {r.path for r in snapshot} == {"/a", "/b"}

    async def test_registry_is_empty_after_both_complete(self) -> None:
        barrier = asyncio.Barrier(2)
        observed: dict[int, dict[str, Any]] = {}
        server = BaseServer(applications=[RendezvousApp(mount="", barrier=barrier, observed=observed)])

        await asyncio.gather(drive(server, "/a"), drive(server, "/b"))

        assert server.requests.in_flight == 0
        assert server.requests.snapshot() == []
        assert server.requests.current is None


class TestErrorPath:
    async def test_request_is_unregistered_even_when_handler_raises(self) -> None:
        server = BaseServer(applications=[RaisingApp(mount="")])
        with pytest.raises(RuntimeError, match="boom"):
            await drive(server, "/boom")
        assert server.requests.in_flight == 0
        assert server.requests.current is None

    async def test_cleanups_drained_even_when_handler_raises(self) -> None:
        ran: list[str] = []
        server = BaseServer(applications=[CleanupThenRaiseApp(mount="", ran=ran)])
        with pytest.raises(RuntimeError, match="boom"):
            await drive(server, "/boom")
        assert ran == ["cleanup"]  # the finally drained the cleanup despite the raise
        assert server.requests.in_flight == 0


class TestCleanups:
    def test_add_cleanup_runs_lifo(self) -> None:
        item = RegisteredRequest(1, "http", "/")
        order: list[str] = []
        item.add_cleanup(lambda: order.append("a"))
        item.add_cleanup(lambda: order.append("b"))
        item.run_cleanups()
        assert order == ["b", "a"]  # last registered runs first

    def test_exception_in_one_cleanup_does_not_stop_the_rest(self) -> None:
        item = RegisteredRequest(1, "http", "/")
        order: list[str] = []

        def boom() -> None:
            raise RuntimeError("cleanup failure")

        item.add_cleanup(lambda: order.append("first"))
        item.add_cleanup(boom)
        item.add_cleanup(lambda: order.append("last"))
        item.run_cleanups()
        assert order == ["last", "first"]  # LIFO; the raising one is isolated

    def test_exception_in_a_cleanup_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = RegisteredRequest(1, "http", "/")

        def boom() -> None:
            raise RuntimeError("cleanup failure")

        item.add_cleanup(boom)
        with caplog.at_level("ERROR", logger="kajenn.request_registry"):
            item.run_cleanups()
        assert any(
            "Request cleanup" in record.message and record.exc_info
            for record in caplog.records
        )

    def test_run_cleanups_is_noop_without_any(self) -> None:
        item = RegisteredRequest(1, "http", "/")
        item.run_cleanups()  # no cleanups queued → nothing happens, no error


class OkApp(BaseApplication):
    """Test app: a plain 200 for every path."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class HeldApp(BaseApplication):
    """Test app blocked on an Event until the test lets it answer.

    Constructor kwargs peeled here (cooperative chain): ``gate`` — the Event the
    test sets to let the request finish.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.gate: asyncio.Event = kwargs.pop("gate")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class MwServer(MiddlewareMixin, BaseServer):
    """The middleware capability over the base server, for the chain test."""


class TestServerState:
    async def test_a_server_that_is_not_running_refuses_a_new_request(
        self, http_request, response_status, response_headers
    ) -> None:
        server = BaseServer(applications=[OkApp(mount="")])
        server.state = QUITTING
        sent = await http_request(server, "/")
        assert response_status(sent) == 503
        retry_after = response_headers(sent)[b"retry-after"]
        assert retry_after == str(REFUSED_RETRY_AFTER_SECONDS).encode()

    async def test_a_server_back_to_running_serves_again(
        self, http_request, response_status
    ) -> None:
        server = BaseServer(applications=[OkApp(mount="")])
        server.state = QUITTING
        server.state = RUNNING
        assert response_status(await http_request(server, "/")) == 200

    async def test_a_refused_request_is_never_registered(self, http_request) -> None:
        server = BaseServer(applications=[OkApp(mount="")])
        server.state = QUITTING
        await http_request(server, "/")
        assert server.requests.in_flight == 0
        assert server.requests.snapshot() == []

    async def test_an_empty_registry_drains_at_once(self) -> None:
        server = BaseServer(applications=[OkApp(mount="")])
        assert await server.requests.await_drain(timeout=0.1) == 0

    async def test_the_drain_returns_as_the_last_request_ends(
        self, http_request, response_status
    ) -> None:
        gate = asyncio.Event()
        server = BaseServer(applications=[HeldApp(mount="", gate=gate)])
        in_flight = asyncio.ensure_future(http_request(server, "/"))
        await asyncio.sleep(0)
        server.state = QUITTING
        assert server.requests.in_flight == 1
        draining = asyncio.ensure_future(server.requests.await_drain())
        gate.set()
        assert await draining == 0
        assert response_status(await in_flight) == 200

    async def test_the_drain_reports_what_is_still_in_flight_past_its_timeout(
        self, http_request
    ) -> None:
        gate = asyncio.Event()
        server = BaseServer(applications=[HeldApp(mount="", gate=gate)])
        in_flight = asyncio.ensure_future(http_request(server, "/"))
        await asyncio.sleep(0)
        server.state = QUITTING
        assert await server.requests.await_drain(timeout=0.05) == 1
        gate.set()
        await in_flight

    async def test_what_the_chain_answers_itself_passes_a_server_not_running(
        self, http_request, response_status
    ) -> None:
        server = MwServer(applications=[OkApp(mount="")], middleware={"cors": True})
        server.state = QUITTING
        # The cors middleware answers a preflight itself, before the dispatch
        # the state guards: 200 from the chain, never the 503.
        preflight = await http_request(
            server, "/", method="OPTIONS", headers=[(b"origin", b"https://example.test")]
        )
        assert response_status(preflight) in (200, 204)
        assert response_status(await http_request(server, "/")) == 503
