# Copyright 2026 Softwell S.r.l.
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

"""The families that describe one process: build, process, pool, tasks, scheduler, startup.

Private to ``kajenn``: the server reaches it through ``server.process_metrics``.

``ProcessMetrics`` is held by the server (``server.process_metrics``, dual
relationship: ``self.server``) and owns ``prometheus_client`` metric objects
created with ``registry=None``: they belong to the server instance, never to
the module-level ``REGISTRY``. Every process has one server, so the same
families describe the server process and the dedicated process of a
``spawner`` application. ``collect()`` writes them in two places:
``ServerApplication.prometheus_metrics`` in the server process, and
``BaseApplication.prometheus_metrics`` of the hosted application in a
dedicated process (``server.kbus_role`` set). The section's ``app`` label
tells the processes apart.

Who counts what:

- pool queue and run time: ``WorkPool.run``, from the submit to the start of
  the function in a pool thread, and from that start to its end;
- tasks: ``LocalTaskExecutor.execute``, one per task it settles;
- scheduler: ``TaskScheduler.tick`` (ticks, and the delay of every schedule
  it launches) and ``TaskScheduler._execute`` (launches by outcome, the
  launches of ``run_now`` included);
- startup duration: ``Lifespan.__call__``, from ``lifespan.startup`` to
  ``lifespan.startup.complete``.
"""

from __future__ import annotations

import os
import platform
from typing import TYPE_CHECKING

import psutil
from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.metrics_core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    InfoMetricFamily,
    Metric,
)

import kajenn

if TYPE_CHECKING:
    from .server import BaseServer

__all__: list[str] = []


class ProcessMetrics:
    """The process, pool, task, scheduler and startup families of one process."""

    def __init__(self, server: BaseServer) -> None:
        self.server = server
        self._pool_queue = Histogram(
            "kajenn_pool_queue_seconds",
            "Time from the submit of a WorkPool call until its function starts, in seconds.",
            registry=None)
        self._pool_run = Histogram(
            "kajenn_pool_run_seconds",
            "Time a WorkPool function runs in its thread, in seconds.",
            registry=None)
        self._tasks = Counter(
            "kajenn_tasks_completed",
            "Tasks settled, per application, task and outcome.",
            ["application", "task", "outcome"], registry=None)
        self._task_duration = Histogram(
            "kajenn_task_duration_seconds",
            "Time a task handler runs, in seconds.",
            ["application", "task"], registry=None)
        self._ticks = Counter(
            "kajenn_scheduler_ticks", "Passes of the task scheduler.", registry=None)
        self._launches = Counter(
            "kajenn_scheduler_launches",
            "Scheduled runs completed, per task and outcome.",
            ["task", "outcome"], registry=None)
        self._delay = Histogram(
            "kajenn_scheduler_delay_seconds",
            "Time from the expected run of a schedule until the scheduler launches it, "
            "in seconds.",
            ["task"], registry=None)
        self._startup = Gauge(
            "kajenn_startup_duration_seconds",
            "Time from lifespan startup until startup complete, in seconds.",
            registry=None)

    def record_pool_call(self, queued: float, ran: float) -> None:
        """Observe one WorkPool call: ``queued`` seconds waiting, ``ran`` seconds running."""
        self._pool_queue.observe(queued)
        self._pool_run.observe(ran)

    def record_task(self, application: str, task: str, outcome: str,
                    seconds: float | None) -> None:
        """Count one task of ``application`` settled ``ok`` or ``error``.

        ``seconds`` is the time its handler ran, ``None`` when the handler was
        never called (the resolution failed): then no duration is observed.
        """
        self._tasks.labels(application, task, outcome).inc()
        if seconds is not None:
            self._task_duration.labels(application, task).observe(seconds)

    def record_scheduler_tick(self) -> None:
        """Count one pass of the scheduler."""
        self._ticks.inc()

    def record_scheduler_launch(self, task: str, outcome: str) -> None:
        """Count one scheduled run of ``task`` that ended ``ok`` or ``error``."""
        self._launches.labels(task, outcome).inc()

    def record_scheduler_delay(self, task: str, seconds: float) -> None:
        """Observe how late the scheduler launched ``task`` against its expected time."""
        self._delay.labels(task).observe(seconds)

    def record_startup(self, seconds: float) -> None:
        """Set the duration of the lifespan startup."""
        self._startup.set(seconds)

    def collect(self) -> list[Metric]:
        """The families of this process, with their current values.

        Build (kajenn and Python versions), the process (pid, start time, CPU
        seconds, resident memory, open file descriptors, from psutil), the
        ``WorkPool`` gauges (``pool.metrics``) and the counted families.
        """
        process = psutil.Process()
        cpu = process.cpu_times()
        pool = self.server.pool.metrics
        return [
            InfoMetricFamily(
                "kajenn_build", "kajenn and Python versions of the process.",
                value={"version": kajenn.__version__,
                       "python_version": platform.python_version()}),
            GaugeMetricFamily("kajenn_process_pid", "Process id of the process.", value=os.getpid()),
            GaugeMetricFamily(
                "kajenn_process_start_time_seconds",
                "Start time of the process since the Unix epoch, in seconds.",
                value=process.create_time()),
            CounterMetricFamily(
                "kajenn_process_cpu_seconds",
                "User and system CPU time of the process, in seconds.",
                value=cpu.user + cpu.system),
            GaugeMetricFamily(
                "kajenn_process_resident_memory_bytes",
                "Resident memory size of the process, in bytes.",
                value=process.memory_info().rss),
            GaugeMetricFamily(
                "kajenn_process_open_fds", "Open file descriptors of the process.",
                value=process.num_fds()),
            GaugeMetricFamily(
                "kajenn_pool_threads", "Threads of the WorkPool, 0 before the first sync call.",
                value=pool["total"]),
            GaugeMetricFamily(
                "kajenn_pool_calls_in_flight",
                "Calls entered in the WorkPool and not yet exited, queued ones included.",
                value=pool["busy"]),
            *[family
              for metric in (self._pool_queue, self._pool_run, self._tasks,
                             self._task_duration, self._ticks, self._launches,
                             self._delay, self._startup)
              for family in metric.collect()],
        ]
