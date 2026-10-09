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

"""Spawners: who starts the process that plays one role of a configuration.

A spawner is told a role (``application:<code>``), the URL the process
connects to — ``<scheme>://<code>:<token>@<location>`` — and the environment
to hand it. ``ensure`` starts the process without waiting for it to join;
``stop`` ends it; ``joined`` records that it joined; ``relaunch`` ensures
again a role whose member was lost or whose process ended without the server
stopping it. ``on_exit(role)`` reports such an end. The backend is chosen by
the ``spawner`` value of the configuration through ``SPAWNERS``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
import time
from typing import Any, Callable

__all__ = ["PARENT_VARIABLE", "SPAWNERS", "Spawner", "SubprocessSpawner"]

#: The environment variable carrying the URL a spawned process connects to.
PARENT_VARIABLE = "KAJENN_KBUS_PARENT"


class Spawner:
    """The interface every backend implements."""

    def __init__(
        self,
        source: str,
        *,
        shutdown_timeout: float = 5.0,
        on_exit: Callable[..., Any] | None = None,
    ) -> None:
        self.source = source
        self.shutdown_timeout = shutdown_timeout
        self.on_exit = on_exit

    async def ensure(self, role: str, *, url: str, environment: dict[str, str]) -> None:
        """Start the process playing ``role``, handing it ``url``."""
        raise NotImplementedError

    async def stop(self, role: str) -> None:
        """End the process playing ``role``."""
        raise NotImplementedError

    def joined(self, role: str) -> None:
        """Record that the process of ``role`` joined."""
        raise NotImplementedError

    def relaunch(
        self,
        role: str,
        *,
        mint: Callable[[], str],
        environment: dict[str, str],
        pid: int | None = None,
    ) -> None:
        """Ensure ``role`` again after its member was lost or its process ended.

        ``mint`` gives the new URL. ``pid`` is the pid of the lost member: a
        relaunch asked for a process already replaced is ignored.
        """
        raise NotImplementedError


class SubprocessSpawner(Spawner):
    """Run each role as ``python -m kajenn serve <source> --role <role>``.

    The URL travels in the environment variable ``KAJENN_KBUS_PARENT``, never
    on the command line. Every started process is watched: an exit that
    ``stop`` did not request, before or after it joined, calls
    ``on_exit(role)``. A relaunch waits for the previous process to end and
    starts the new one at least the backoff after the previous start. The
    backoff starts at ``RELAUNCH_INTERVAL``, doubles at every relaunch up to
    ``RELAUNCH_CEILING``, and starts over when the process stayed joined
    ``RELAUNCH_RESET`` seconds. A relaunch is never queued twice for one role;
    attempts are not limited.
    """

    RELAUNCH_INTERVAL = 1.0
    RELAUNCH_CEILING = 30.0
    RELAUNCH_RESET = 60.0

    def __init__(self, source: str, **kwargs: Any) -> None:
        super().__init__(source, **kwargs)
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.started_at: dict[str, float] = {}
        self.joined_at: dict[str, float] = {}
        self.backoff: dict[str, float] = {}
        self.relaunches: dict[str, asyncio.Task[None]] = {}
        self.watchers: dict[str, asyncio.Task[None]] = {}
        self.logger = logging.getLogger(__name__)

    async def ensure(self, role: str, *, url: str, environment: dict[str, str]) -> None:
        process = self.processes.get(role)
        if process is not None and process.returncode is None:
            return
        process = self.processes[role] = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "kajenn", "serve", self.source, "--role", role,
            env={**os.environ, **environment, PARENT_VARIABLE: url},
        )
        self.started_at[role] = time.monotonic()
        self.joined_at.pop(role, None)
        self.watchers[role] = asyncio.create_task(self._watch(role, process))

    def joined(self, role: str) -> None:
        self.joined_at[role] = time.monotonic()

    def relaunch(
        self,
        role: str,
        *,
        mint: Callable[[], str],
        environment: dict[str, str],
        pid: int | None = None,
    ) -> None:
        if role not in self.processes or role in self.relaunches:
            return
        if pid is not None and self.processes[role].pid != pid:
            return
        self.relaunches[role] = asyncio.create_task(self._relaunch(role, mint, environment))

    async def _watch(self, role: str, process: asyncio.subprocess.Process) -> None:
        """Wait for ``process`` to end; an end ``stop`` did not request calls ``on_exit``."""
        await process.wait()
        if self.processes.get(role) is process and role not in self.relaunches:
            self.logger.warning("Process of %s ended with code %s", role, process.returncode)
            await self._report_exit(role)

    async def _report_exit(self, role: str) -> None:
        """Run ``on_exit(role)``, sync or async; its failure is logged, never raised."""
        if self.on_exit is None:
            return
        try:
            result = self.on_exit(role)
            if inspect.isawaitable(result):
                await result
        except Exception:
            self.logger.exception("on_exit of %s failed", role)

    async def _relaunch(
        self, role: str, mint: Callable[[], str], environment: dict[str, str]
    ) -> None:
        try:
            await self._end(self.processes[role])
            now = time.monotonic()
            joined_at = self.joined_at.get(role)
            if joined_at is not None and now - joined_at >= self.RELAUNCH_RESET:
                self.backoff.pop(role, None)
            backoff = self.backoff.get(role, self.RELAUNCH_INTERVAL)
            self.backoff[role] = min(backoff * 2, self.RELAUNCH_CEILING)
            delay = self.started_at[role] + backoff - now
            if delay > 0:
                await asyncio.sleep(delay)
            await self.ensure(role, url=mint(), environment=environment)
        finally:
            self.relaunches.pop(role, None)

    async def stop(self, role: str) -> None:
        """Cancel a pending relaunch, terminate the process, kill it past ``shutdown_timeout``."""
        relaunch = self.relaunches.pop(role, None)
        if relaunch is not None:
            await cancel_and_wait(relaunch)
        process = self.processes.pop(role, None)
        if process is not None:
            await self._end(process)
        watcher = self.watchers.pop(role, None)
        if watcher is not None:
            await cancel_and_wait(watcher)

    async def _end(self, process: asyncio.subprocess.Process) -> None:
        """Terminate ``process``, then kill it past ``shutdown_timeout``."""
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), self.shutdown_timeout)
            except TimeoutError:
                process.kill()
        await process.wait()


async def cancel_and_wait(task: asyncio.Task[Any]) -> None:
    """Cancel ``task`` and wait for it to end.

    The ``CancelledError`` of ``task`` is absorbed; a cancellation of the task
    calling this function is re-raised.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise


SPAWNERS: dict[str, type[Spawner]] = {"subprocess": SubprocessSpawner}
