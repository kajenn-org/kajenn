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

"""``request_timeout`` is one budget: the wait for a ``max_calls`` slot and the
wait for the first message of the answer.

A request queued behind a saturated mount is answered 503 within the same
``request_timeout`` as a request that got its slot at once; the queue never
stretches the time a caller waits.
"""

from __future__ import annotations

import asyncio
import time

from .test_external_application import request, running, write_recipe

SATURATED = 'spawner="subprocess", max_calls=1, request_timeout=0.2'


async def test_queued_requests_are_answered_within_the_same_budget(tmp_path) -> None:
    # wf:contract: four simultaneous GET /billing/slow (3 s handler) against a mount with
    # wf:contract: max_calls=1 and request_timeout=0.2 all answer 503, and their durations differ
    # wf:contract: by less than 0.3 s: the slot wait counts against request_timeout, the queue
    # wf:contract: does not stretch the wait (without the budget they spread 0.2 -> 0.8 s).
    async with running(write_recipe(tmp_path, declaration=SATURATED)) as server:
        async def timed() -> tuple[int, float]:
            started = time.monotonic()
            status, _ = await request(server, "/billing/slow")
            return status, time.monotonic() - started

        outcomes = await asyncio.gather(*(timed() for _ in range(4)))
    assert [status for status, _ in outcomes] == [503] * 4
    durations = [duration for _, duration in outcomes]
    assert max(durations) - min(durations) < 0.3, outcomes
