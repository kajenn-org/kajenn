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
"""Run a consumer callback so that its failure never severs the KajennBus,
and cancel an inner task without swallowing the caller's own cancellation."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

__all__ = ["cancel_and_wait", "run_callback"]


async def run_callback(
    callback: Callable[..., Any] | None, *args: Any, logger: logging.Logger
) -> None:
    """Run a sync-or-async callback; ``None`` is a no-op, an exception is logged."""
    if callback is None:
        return
    try:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("KajennBus callback %r failed", callback)


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
