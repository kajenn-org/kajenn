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

"""Well-known URLs of an application in its dedicated process.

The process presents its ``well_known`` paths when it joins the KajennBus; the
server answers them through the process exactly as for an in-process
application, and again after a relaunch.
"""

from __future__ import annotations

import os
import signal

import pytest

from tests.core.test_external_application import answering, request, running

RECIPE = '''
import os

from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Billing(RoutedApplication):
    well_known = {"/.well-known/probe": "probe", "/robots.txt": "robots"}

    @route()
    def probe(self):
        return {"served": "probe", "pid": os.getpid()}

    @route()
    def robots(self):
        return "User-agent: *\\n"

    @route()
    def prepared_state(self):
        return {"prepared": True, "pid": os.getpid()}


class Local(RoutedApplication):
    well_known = LOCAL_WELL_KNOWN
    default_well_known = LOCAL_DEFAULTS

    @route()
    def probe(self):
        return {"served": "local"}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    site_name = "billingsite"

    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess",
                         request_timeout=5)
        apps.application(code="local", app_class=Local)
'''


def write_recipe(tmp_path, *, local_well_known: str = "{}", local_defaults: str = "{}") -> str:
    path = tmp_path / "config.py"
    path.write_text(RECIPE.replace("LOCAL_WELL_KNOWN", local_well_known)
                    .replace("LOCAL_DEFAULTS", local_defaults))
    return str(path)


async def test_the_paths_of_a_spawned_application_answer_through_its_process(tmp_path):
    # wf:contract: an application with spawner="subprocess" declaring /.well-known/probe
    # wf:contract: and /robots.txt answers both through its process, and again after the
    # wf:contract: process is killed and relaunched.
    async with running(write_recipe(tmp_path)) as server:
        status, probe = await request(server, "/.well-known/probe")
        robots = await request(server, "/robots.txt")
        first = await answering(server)
        os.kill(first["pid"], signal.SIGKILL)
        await answering(server, other_than=first["pid"])
        again_status, again = await request(server, "/.well-known/probe")
    assert (status, probe["served"]) == (200, "probe")
    assert probe["pid"] != os.getpid()
    assert robots == (200, b"User-agent: *\n")
    assert (again_status, again["served"]) == (200, "probe")
    assert again["pid"] != probe["pid"]


async def test_an_undeclared_path_answers_404_beside_a_spawned_application(tmp_path):
    # wf:contract: with a spawned application declaring paths, an undeclared well-known
    # wf:contract: path answers 404 "unimplemented well-known URL".
    async with running(write_recipe(tmp_path)) as server:
        status, body = await request(server, "/.well-known/absent")
    assert status == 404
    assert b"unimplemented well-known URL" in body


async def test_a_join_presenting_a_held_path_is_refused(tmp_path):
    # wf:contract: when the in-process application local already declares
    # wf:contract: /.well-known/probe, the spawned billing presenting the same path at its
    # wf:contract: join is refused (kbus.Refused on the server side): /.well-known/probe
    # wf:contract: keeps answering {"served": "local"} and billing never answers its mount.
    import asyncio
    import time

    recipe = write_recipe(tmp_path, local_well_known='{"/.well-known/probe": "probe"}')
    async with running(recipe, wait=False) as server:
        # A spawned process joins well within this bound (see the test above).
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert await request(server, "/.well-known/probe") == (200, {"served": "local"})
            status, state = await request(server, "/billing/prepared_state")
            if status == 200:
                pytest.fail(f"billing answered its mount after a refused join: {state}")
            await asyncio.sleep(0.1)


async def test_a_spawned_declaration_wins_over_an_inprocess_default(tmp_path):
    # The in-process local gives /.well-known/probe as a default; the spawned
    # billing declares it at its join, after the boot, and answers it.
    recipe = write_recipe(tmp_path, local_defaults='{"/.well-known/probe": "probe"}')
    async with running(recipe) as server:
        status, probe = await request(server, "/.well-known/probe")
    assert (status, probe["served"]) == (200, "probe")
    assert probe["pid"] != os.getpid()
