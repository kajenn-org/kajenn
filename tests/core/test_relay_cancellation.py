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

"""Cancelling the ASGI task of a proxied answer leaves nothing behind.

A client leaving sends ``http.disconnect``; a server shutting down or a
middleware timing out CANCELS the ASGI task instead. On that path the relay
of ``RemoteApplication`` must cancel and await the tasks it started and
retrieve their outcome: no task of its own survives, no exception is left
unretrieved on the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
from typing import Any

from .test_external_application import running
from .test_external_application_streaming import Client, wait_for_file, write_streaming_recipe


async def test_cancelling_the_asgi_task_mid_stream_leaves_no_relay_task(tmp_path) -> None:
    # wf:contract: the ASGI task of GET /billing/endless (spawned) is cancelled after two
    # wf:contract: body chunks; 0.5 s later no task of RemoteApplication._relay is alive, the
    # wf:contract: loop's exception handler saw no unretrieved exception between the
    # wf:contract: cancellation and that check, and the spawned application's iterator was
    # wf:contract: closed (the marker file says "closed").
    marker = tmp_path / "marker"
    async with running(write_streaming_recipe(tmp_path)) as server:
        client = Client(server, "/billing/endless", f"marker={marker}".encode())
        client.start()
        assert (await client.next())["type"] == "http.response.start"
        await client.next()
        await client.next()
        unretrieved: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
        try:
            client.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client.task
            await asyncio.sleep(0.5)
            gc.collect()
            await asyncio.sleep(0)
            relays = [task for task in asyncio.all_tasks()
                      if "RemoteApplication._relay" in task.get_coro().__qualname__]
            assert relays == []
            assert unretrieved == []
        finally:
            loop.set_exception_handler(previous)
        assert await wait_for_file(marker) == "closed"
