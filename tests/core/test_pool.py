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

"""Thread-pool tests (SPECIFICATION.md §4): a sync handler runs on the pool
(thread identity asserted), an async handler stays on the loop, the pool is
provisioned lazily on the first sync dispatch and torn down at shutdown; a
cancelled caller waits for work already running and withdraws work still
queued (#89).

``run_sync`` is exercised directly (no uvicorn) and the throwaway app's sync
route is driven through ``BaseServer.__call__`` at the ASGI level.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
from typing import Any

import pytest

from kajenn import BaseServer

from ..throwaway_app import ThrowawayApp


def make_server() -> BaseServer:
    """A server with a throwaway primary — enough to exercise the pool."""
    return BaseServer(applications=[ThrowawayApp(mount="", name="primary")])


class TestDispatch:
    async def test_sync_handler_runs_on_a_pool_thread(self) -> None:
        server = make_server()
        name = await server.run_sync(lambda: threading.current_thread().name)
        assert name.startswith("kajenn-pool")

    async def test_async_stays_on_loop_sync_goes_off_loop(self) -> None:
        server = make_server()
        loop_ident = threading.get_ident()

        async def async_handler() -> int:
            return threading.get_ident()

        assert await async_handler() == loop_ident
        assert await server.run_sync(threading.get_ident) != loop_ident


class TestMaxThreads:
    async def test_max_threads_reaches_the_executor(self) -> None:
        server = BaseServer(applications=[ThrowawayApp(mount="", name="primary")], max_threads=2)
        await server.run_sync(lambda: None)
        assert server.pool.executor._max_workers == 2


class TestProvisioning:
    async def test_pool_is_not_provisioned_before_first_dispatch(self) -> None:
        server = make_server()
        assert server.pool.provisioned is False
        await server.run_sync(lambda: None)
        assert server.pool.provisioned is True

    async def test_shutdown_resets_the_pool_for_reprovisioning(self) -> None:
        server = make_server()
        await server.run_sync(lambda: None)
        assert server.pool.provisioned is True

        server.pool.shutdown(wait=True)
        assert server.pool.provisioned is False

        # a later dispatch lazily re-provisions: server reuse via repeated serve()
        name = await server.run_sync(lambda: threading.current_thread().name)
        assert name.startswith("kajenn-pool")
        assert server.pool.provisioned is True


class TestThrowawayRoute:
    async def test_sync_route_dispatches_through_the_pool(self) -> None:
        server = make_server()
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await server({"type": "http", "path": "/sync"}, receive, send)

        assert server.pool.provisioned is True
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        assert body == b"sync:primary"


class TestContextPropagation:
    async def test_sync_handler_sees_its_own_current_request(self) -> None:
        server = make_server()
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await server({"type": "http", "path": "/sync-current"}, receive, send)

        # the pool copies the caller's context: the worker thread reads the
        # registry's ContextVar and sees the request being served
        body = next(m["body"] for m in sent if m["type"] == "http.response.body")
        assert body == b"current:/sync-current"


class TestMetrics:
    async def test_metrics_are_zeros_until_the_pool_is_provisioned(self) -> None:
        """Zeros, honestly: a pool that does not exist reports no pressure."""
        server = make_server()
        assert server.pool.metrics == {"total": 0, "busy": 0}

    async def test_busy_returns_to_zero_after_a_run(self) -> None:
        server = make_server()
        await server.run_sync(lambda: None)
        assert server.pool.metrics["busy"] == 0

    async def test_total_mirrors_max_threads(self) -> None:
        """The resolution is ours, not read off a private executor attribute."""
        server = BaseServer(applications=[ThrowawayApp(mount="", name="primary")], max_threads=3)
        await server.run_sync(lambda: None)
        assert server.pool.metrics["total"] == 3

    async def test_total_mirrors_the_executor_own_resolution(self) -> None:
        """The mirror against the reality, on ANY interpreter: the frozen total
        equals the thread count the stdlib default actually decided (3.13 moved
        it from cpu_count to process_cpu_count — the mirror must move with it).
        Reading the private attribute is legitimate HERE: this is the one test
        that verifies the mirror, so the source never has to."""
        server = make_server()
        await server.run_sync(lambda: None)
        assert server.pool.metrics["total"] == server.pool.executor._max_workers

    async def test_busy_counts_the_call_in_flight_while_it_runs(self) -> None:
        """``busy`` is demand — every run() entered and not yet exited, queued
        included — not slots held; past saturation it exceeds ``total``."""
        server = make_server()
        seen: list[int] = []

        def probe() -> None:
            seen.append(server.pool.metrics["busy"])

        await server.run_sync(probe)
        assert seen == [1]


class Blocker:
    """Blocking work the test holds: it says when it started, ends on release."""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.result = result
        self.error = error

    def __call__(self) -> Any:
        self.started.set()
        try:
            self.release.wait()
            if self.error is not None:
                raise self.error
            return self.result
        finally:
            self.finished.set()


async def started(blocker: Blocker) -> None:
    """Wait, off the loop, until ``blocker`` runs on its pool thread."""
    assert await asyncio.to_thread(blocker.started.wait, 2)


async def still_pending(task: asyncio.Task[Any]) -> bool:
    """Whether ``task`` is still not done after a short look."""
    done, _ = await asyncio.wait({task}, timeout=0.1)
    return not done


class TestCancellation:
    """#89: a cancelled caller waits for work already running on the pool."""

    async def test_a_cancelled_caller_ends_only_after_the_running_work(self) -> None:
        server = make_server()
        blocker = Blocker()
        task = asyncio.create_task(server.run_sync(blocker))
        await started(blocker)
        task.cancel()
        assert await still_pending(task)
        assert server.pool.metrics["busy"] == 1
        blocker.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert blocker.finished.is_set()
        assert server.pool.metrics["busy"] == 0

    async def test_repeated_cancellations_while_waiting_are_absorbed(self) -> None:
        server = make_server()
        blocker = Blocker()
        task = asyncio.create_task(server.run_sync(blocker))
        await started(blocker)
        for _ in range(3):
            task.cancel()
            assert await still_pending(task)
        blocker.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert blocker.finished.is_set()
        assert server.pool.metrics["busy"] == 0

    async def test_the_cancellation_wins_over_the_work_result(self) -> None:
        server = make_server()
        blocker = Blocker(result="done")
        task = asyncio.create_task(server.run_sync(blocker))
        await started(blocker)
        task.cancel()
        blocker.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    async def test_the_work_error_after_a_cancellation_is_logged_and_observed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        server = make_server()
        blocker = Blocker(error=ValueError("late failure"))
        task = asyncio.create_task(server.run_sync(blocker))
        await started(blocker)
        task.cancel()
        blocker.release.set()
        loop = asyncio.get_running_loop()
        reported: list[dict[str, Any]] = []
        loop.set_exception_handler(lambda _loop, context: reported.append(context))
        try:
            with caplog.at_level(logging.ERROR, logger="kajenn.pool"):
                with pytest.raises(asyncio.CancelledError):
                    await task
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(None)
        assert reported == []
        [record] = caplog.records
        assert isinstance(record.exc_info[1], ValueError)

    async def test_a_timeout_still_surfaces_as_timeout_after_the_work_ends(self) -> None:
        server = make_server()
        blocker = Blocker()

        async def bounded() -> None:
            async with asyncio.timeout(0.05):
                await server.run_sync(blocker)

        task = asyncio.create_task(bounded())
        await started(blocker)
        assert await still_pending(task)
        blocker.release.set()
        with pytest.raises(TimeoutError):
            await task
        assert blocker.finished.is_set()

    async def test_queued_work_is_withdrawn_and_never_runs(self) -> None:
        server = BaseServer(applications=[ThrowawayApp(mount="", name="primary")], max_threads=1)
        blocker = Blocker()
        first = asyncio.create_task(server.run_sync(blocker))
        await started(blocker)
        ran: list[bool] = []
        queued = asyncio.create_task(server.run_sync(lambda: ran.append(True)))
        await asyncio.sleep(0)
        assert server.pool.metrics["busy"] == 2
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert server.pool.metrics["busy"] == 1
        blocker.release.set()
        await first
        await server.run_sync(lambda: None)
        assert ran == []
        assert server.pool.metrics["busy"] == 0
