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

"""An external process that dies before answering, and a post from the process.

A process killed while it holds a request it has not started answering makes
the mount answer 503, the same as a process that has not joined. A
``kbus_post`` from the process to an application of the server runs that route.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time

from .test_external_application import RECIPE, answering, request, running
from .test_external_application_streaming import wait_for_file

EXTRA = '''

class Hanging(Billing):
    @route()
    async def hang(self, marker):
        with open(marker, "w") as handle:
            handle.write(str(os.getpid()))
        await asyncio.sleep(3600)

    @route()
    async def post_note(self):
        await self.server.kbus_post("/local/note", {"text": "posted"})
        return {"posted": True}


class Notes(Local):
    notes: list = []

    @route()
    def note(self, text):
        self.notes.append(text)

    @route()
    def notes_seen(self):
        return {"notes": self.notes}
'''


def write_recipe(tmp_path) -> str:
    text = (RECIPE.replace("\nclass Recipe(", EXTRA + "\nclass Recipe(")
            .replace("SERVER_SECTION", "")
            .replace("app_class=Billing, DECLARATION", 'app_class=Hanging, spawner="subprocess"')
            .replace('app_class=Local)', 'app_class=Notes)')
            .replace("STARTUP_DELAY", "0.2"))
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


async def test_a_process_dying_before_its_answer_starts_answers_503(tmp_path):
    marker = tmp_path / "marker"
    async with running(write_recipe(tmp_path)) as server:
        pending = asyncio.create_task(
            request(server, "/billing/hang", query=f"marker={marker}".encode()))
        pid = int(await wait_for_file(marker))
        os.kill(pid, signal.SIGKILL)
        status, _ = await asyncio.wait_for(pending, 10)
        await answering(server, other_than=pid)
    assert status == 503


async def test_a_process_killed_before_accepting_the_request_answers_503(tmp_path):
    async with running(write_recipe(tmp_path)) as server:
        state = await answering(server)
        os.kill(state["pid"], signal.SIGSTOP)
        pending = asyncio.create_task(request(server, "/billing/total", query=b"order=2"))
        await asyncio.sleep(0.5)
        os.kill(state["pid"], signal.SIGKILL)
        status, _ = await asyncio.wait_for(pending, 10)
        await answering(server, other_than=state["pid"])
    assert status == 503


async def test_a_post_from_the_process_runs_a_route_of_the_server(tmp_path):
    async with running(write_recipe(tmp_path)) as server:
        assert await request(server, "/billing/post_note") == (200, {"posted": True})
        deadline = time.monotonic() + 10
        while (await request(server, "/local/notes_seen"))[1]["notes"] != ["posted"]:
            assert time.monotonic() < deadline, "the post never ran"
            await asyncio.sleep(0.05)
