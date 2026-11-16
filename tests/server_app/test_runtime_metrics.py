# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Process, pool, task, scheduler, session, spawned-process and boot families.

Every test drives a real ``AsgiServer`` and reads ``GET /_server/metrics``
with an API key holding only ``SERVER_METRICS``. Each test produces an event
and checks that the expected counter or histogram grew.

What the suite pins:

- the dedicated process of a ``spawner="subprocess"`` application writes the
  process, pool and startup families with the server's names, its own pid
  and its ``app`` label, next to ``app="_server"``; it writes none of the
  server-only families;
- a session created for a request is counted;
- a sync call through the pool is observed in the queue and run histograms;
- a task settled ok and one settled error are counted with their duration;
- the scheduler counts its ticks, its launches by outcome and their delay;
- a killed spawned process is counted as a restart; a process that ends
  before it joins is counted as a start failure;
- a ``remote_call`` and a forwarded HTTP request past ``request_timeout`` are
  counted as kbus timeouts;
- the startup duration is written for the server and for the child;
- a ``FatalBootError`` the server stays up after is counted.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.db import DbHandlerBase
from kajenn.tasks import new_descriptor
from tests.conftest import LifespanRunner, call, read_samples, running, start_server
from tests.storage_support import MemoryApiKeyStore, site_mounts

PROCESS_FAMILIES = {
    "kajenn_build_info",
    "kajenn_process_pid",
    "kajenn_process_start_time_seconds",
    "kajenn_process_cpu_seconds_total",
    "kajenn_process_resident_memory_bytes",
    "kajenn_process_open_fds",
    "kajenn_pool_threads",
    "kajenn_pool_calls_in_flight",
    "kajenn_pool_queue_seconds_count",
    "kajenn_pool_run_seconds_count",
    "kajenn_startup_duration_seconds",
}

SERVER_ONLY_PREFIXES = (
    "kajenn_http_", "kajenn_credential_", "kajenn_wsx_", "kajenn_websocket_",
    "kajenn_sessions_", "kajenn_spawned_", "kajenn_kbus_", "kajenn_fatal_",
    "kajenn_requests_in_flight",
)


def total(samples: list[Any], name: str, **labels: str) -> float:
    """The sum of the samples ``name`` whose labels include ``labels`` (``app="_server"`` default)."""
    wanted = {"app": "_server", **labels}
    return sum(sample.value for sample in samples
               if sample.name == name and wanted.items() <= sample.labels.items())


async def eventually(server: AsgiServer, check: Any, seconds: float = 10) -> list[Any]:
    """Scrape until ``check(samples)`` is true, within ``seconds``; return the samples."""
    deadline = time.monotonic() + seconds
    while True:
        samples = await read_samples(server)
        if check(samples):
            return samples
        assert time.monotonic() < deadline, "the expected samples never appeared"
        await asyncio.sleep(0.05)


class Desk(RoutedApplication):
    """Sync and async routes, and two task handlers."""

    @route()
    def nap(self) -> dict[str, bool]:
        time.sleep(0.05)
        return {"rested": True}

    @route()
    def add(self, a: int = 0, b: int = 0) -> int:
        time.sleep(0.02)
        return a + b

    @route()
    def boom(self) -> None:
        raise ValueError("task exploded")


async def desk_server(tmp_path: Path, **kwargs: Any) -> AsgiServer:
    return await start_server(AsgiServer(
        applications=[(Desk, {"code": "desk"})], storage=site_mounts(tmp_path),
        tokens={"store_class": MemoryApiKeyStore}, **kwargs))


async def test_a_session_created_for_a_request_is_counted(tmp_path) -> None:
    server = await desk_server(tmp_path)
    before = total(await read_samples(server), "kajenn_sessions_created_total")
    assert (await call(server, "GET", "/desk/nap"))["status"] == 200
    after = total(await read_samples(server), "kajenn_sessions_created_total")
    # The request and the second scrape carry no cookie: each gets a session.
    assert after - before == 2


async def test_a_sync_call_is_observed_in_the_pool_histograms(tmp_path) -> None:
    server = await desk_server(tmp_path)
    before = await read_samples(server)
    assert (await call(server, "GET", "/desk/nap"))["status"] == 200
    after = await read_samples(server)
    for name in ("kajenn_pool_queue_seconds_count", "kajenn_pool_run_seconds_count"):
        assert total(after, name) >= total(before, name) + 1, name
    ran = total(after, "kajenn_pool_run_seconds_sum") - total(before, "kajenn_pool_run_seconds_sum")
    assert ran >= 0.05


async def test_tasks_settled_ok_and_error_are_counted(tmp_path) -> None:
    server = await desk_server(tmp_path)
    spool = server.tasks.spool
    for task_id, mount, node_path, params in (
            ("ok1", "desk", "add", {"a": 1, "b": 2}),
            ("err1", "desk", "boom", {}),
            ("lost1", "nowhere", "add", {})):
        spool.create(new_descriptor(task_id, owner="alice", mount=mount, node_path=node_path),
                     params)
    samples = await eventually(server, lambda samples: total(
        samples, "kajenn_tasks_completed_total") == 3)
    completed = "kajenn_tasks_completed_total"
    assert total(samples, completed, application="desk", task="add", outcome="ok") == 1
    assert total(samples, completed, application="desk", task="boom", outcome="error") == 1
    assert total(samples, completed, application="", task="add", outcome="error") == 1
    duration = "kajenn_task_duration_seconds_count"
    assert total(samples, duration, application="desk", task="add") == 1
    assert total(samples, duration, application="desk", task="boom") == 1
    assert total(samples, duration, application="") == 0
    assert total(samples, "kajenn_task_duration_seconds_sum", application="desk",
                 task="add") >= 0.02


class Clock(RoutedApplication):
    """Two schedules every second: one runs, one raises."""

    @route(task="chime", task_every="1s")
    def chime(self) -> str:
        return "chimed"

    @route(task="crack", task_every="1s")
    def crack(self) -> None:
        raise ValueError("scheduled crack")


async def test_the_scheduler_counts_ticks_launches_and_delay(tmp_path) -> None:
    tick = 0.05
    server = await start_server(AsgiServer(
        applications=[(Clock, {"code": "clock"})], storage=site_mounts(tmp_path),
        tokens={"store_class": MemoryApiKeyStore}, tasks={"tick_seconds": tick}))
    launches = "kajenn_scheduler_launches_total"
    samples = await eventually(server, lambda samples: (
        total(samples, launches, task="chime", outcome="ok") >= 1
        and total(samples, launches, task="crack", outcome="error") >= 1))
    assert total(samples, "kajenn_scheduler_ticks_total") >= 2
    count = total(samples, "kajenn_scheduler_delay_seconds_count", task="chime")
    assert count >= 1
    # Launched at the first tick past the expected time, never a whole period late.
    assert total(samples, "kajenn_scheduler_delay_seconds_sum", task="chime") / count < 1.0


class Unopenable(DbHandlerBase):
    """A database handler whose ``open`` fails: a fatal boot of the server."""

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        """Nothing to keep."""

    async def open(self) -> None:
        raise ConnectionError("database unreachable")


async def test_a_fatal_boot_error_with_the_server_kept_up_is_counted() -> None:
    server = AsgiServer(tokens={"store_class": MemoryApiKeyStore},
                        abort_on_fatal_boot_error=False)
    server.add_database("main", {}, Unopenable)
    runner = LifespanRunner(server)
    await runner.startup()
    try:
        assert runner.sent[-1]["type"] == "lifespan.startup.complete"
        samples = await read_samples(server)
        assert total(samples, "kajenn_fatal_boot_errors_total") == 1
    finally:
        await runner.shutdown()


async def test_the_server_writes_its_startup_duration(tmp_path) -> None:
    server = await desk_server(tmp_path)
    samples = await read_samples(server)
    assert total(samples, "kajenn_startup_duration_seconds") > 0


RECIPE = '''
import asyncio
import os

from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryApiKeyStore


class Billing(RoutedApplication):
    @route()
    def state(self):
        return {"pid": os.getpid()}

    @route()
    async def slow(self):
        await asyncio.sleep(3)
        return {"slow": True}

    async def stuck(self):
        await asyncio.sleep(3)


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess",
                         request_timeout=1)
'''

FAILING_RECIPE = '''
import os

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryApiKeyStore


class Doomed(RoutedApplication):
    def on_startup(self):
        os._exit(3)


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        cfg.applications().application(code="doomed", app_class=Doomed,
                                       spawner="subprocess", request_timeout=1)
'''


def write_config(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


async def billing_pid(server: AsgiServer) -> int:
    answer = await call(server, "GET", "/billing/state")
    assert answer["status"] == 200
    return json.loads(answer["body"])["pid"]


async def test_the_spawned_process_writes_its_own_process_families(tmp_path) -> None:
    async with running(write_config(tmp_path, RECIPE)) as server:
        pid = await billing_pid(server)
        samples = await read_samples(server)
    for name in PROCESS_FAMILIES:
        apps = {sample.labels["app"] for sample in samples if sample.name == name}
        assert apps == {"_server", "billing"}, name
    pids = {sample.labels["app"]: sample.value for sample in samples
            if sample.name == "kajenn_process_pid"}
    assert pids == {"_server": os.getpid(), "billing": pid}
    assert total(samples, "kajenn_startup_duration_seconds", app="billing") > 0
    assert total(samples, "kajenn_process_resident_memory_bytes", app="billing") > 0
    child = {sample.name for sample in samples if sample.labels["app"] == "billing"}
    assert not [name for name in child if name.startswith(SERVER_ONLY_PREFIXES)]


async def test_a_killed_spawned_process_is_counted_as_a_restart(tmp_path) -> None:
    restarts = "kajenn_spawned_process_restarts_total"
    async with running(write_config(tmp_path, RECIPE)) as server:
        pid = await billing_pid(server)
        os.kill(pid, signal.SIGKILL)
        samples = await eventually(server, lambda samples: total(
            samples, restarts, application="billing") >= 1)
    # The end of the process and the loss of its kbus member race: the first
    # one seen asks for the relaunch and is the only one counted.
    causes = {sample.labels["cause"]: sample.value for sample in samples
              if sample.name == restarts and sample.labels["application"] == "billing"}
    assert sum(causes.values()) == 1
    assert set(causes) <= {"exited", "lost"}
    assert total(samples, "kajenn_spawned_process_start_failures_total",
                 application="billing") == 0


@asynccontextmanager
async def in_lifespan(config: str):
    """A server built from ``config`` inside its lifespan, without waiting for its applications."""
    server = AsgiServer(config=config)
    runner = LifespanRunner(server)
    await runner.startup()
    try:
        yield server
    finally:
        await runner.shutdown()


async def test_a_process_that_ends_before_joining_is_a_start_failure(tmp_path) -> None:
    async with in_lifespan(write_config(tmp_path, FAILING_RECIPE)) as server:
        samples = await eventually(server, lambda samples: total(
            samples, "kajenn_spawned_process_start_failures_total", application="doomed") >= 1)
    assert total(samples, "kajenn_spawned_process_restarts_total",
                 application="doomed", cause="exited") >= 1


async def test_a_remote_call_past_the_timeout_is_counted(tmp_path) -> None:
    timeouts = "kajenn_kbus_timeouts_total"
    async with running(write_config(tmp_path, RECIPE)) as server:
        before = total(await read_samples(server), timeouts,
                       application="billing", operation="remote_call")
        with pytest.raises(TimeoutError):
            await server.applications["billing"].remote_call("stuck")
        samples = await read_samples(server)
    assert total(samples, timeouts, application="billing", operation="remote_call") == before + 1


async def test_a_forwarded_request_past_the_timeout_is_counted(tmp_path) -> None:
    timeouts = "kajenn_kbus_timeouts_total"
    async with running(write_config(tmp_path, RECIPE)) as server:
        assert (await call(server, "GET", "/billing/slow"))["status"] == 503
        samples = await read_samples(server)
    assert total(samples, timeouts, application="billing", operation="http_forward") == 1
