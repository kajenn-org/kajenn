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

"""The discovery documents of an external application answer under ``/.well-known/``.

The role process presents the names of its application's ``_well_known``
branch in its REGISTER; the server indexes them on the mount of that
application, so ``/.well-known/<name>`` reaches the process as it reaches the
same class declared in-process. A relaunched process presents them again. A
REGISTER whose ``well_known`` is not a list of non-empty strings is refused.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time

import pytest

from kajenn.kbus import KBusClient
from kajenn.kbus.spawner import SubprocessSpawner

from .test_kbus_external_application import RECIPE, get, running

DISCOVERED = '''

from genro_routes import RoutingClass

from kajenn.well_known import WELL_KNOWN_ROOT


class Discovery(RoutingClass):
    @route()
    def probe(self):
        return {"served": "probe"}


class Discovered(Billing):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.route.add_branches({"name": WELL_KNOWN_ROOT, "instance": Discovery()})

'''


def write_recipe(tmp_path, declaration: str) -> str:
    """The shared recipe with ``billing`` declared by ``declaration``."""
    text = RECIPE.replace("\nclass Recipe(", DISCOVERED + "\nclass Recipe(").replace(
        'code="billing", app_class=Billing, SPAWNER', declaration)
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


SPAWNED = 'code="billing", app_class=Discovered, spawner="subprocess"'
INPROCESS = 'code="billing", app_class=Discovered'


@pytest.fixture
def no_process(monkeypatch):
    async def ensure(self, role, *, token, environment):
        return None
    monkeypatch.setattr(SubprocessSpawner, "ensure", ensure)


async def test_a_discovery_document_answers_the_same_spawned_and_inprocess(tmp_path):
    async with running(write_recipe(tmp_path, INPROCESS), wait_member=False) as server:
        inprocess = await get(server, "/.well-known/probe")
    async with running(write_recipe(tmp_path, SPAWNED)) as server:
        spawned = await get(server, "/.well-known/probe")
    assert inprocess == (200, {"served": "probe"})
    assert spawned == inprocess


async def test_a_relaunched_process_answers_its_discovery_documents_again(tmp_path):
    async with running(write_recipe(tmp_path, SPAWNED)) as server:
        first = server.children_kbus.resolve("billing")
        os.kill(first.pid, signal.SIGKILL)
        deadline = time.monotonic() + 30
        while server.children_kbus.resolve("billing") in (None, first):
            assert time.monotonic() < deadline, "the process was never relaunched"
            await asyncio.sleep(0.05)
        second = server.children_kbus.resolve("billing")
        probe = await get(server, "/.well-known/probe")
        _, state = await get(server, "/billing/prepared_state")
    assert second.pid != first.pid
    assert state["pid"] == second.pid
    assert probe == (200, {"served": "probe"})


async def test_a_register_with_an_invalid_well_known_is_refused(tmp_path, no_process):
    async with running(write_recipe(tmp_path, SPAWNED), wait_member=False) as server:
        intruder = KBusClient(server.children_kbus.address, "billing",
                              presentation={"role": "application:billing",
                                            "token": server._kbus_tokens["billing"],
                                            "well_known": ["probe", ""]})
        with pytest.raises(ConnectionError, match="well_known"):
            await intruder.connect()
        admitted = server.children_kbus.resolve("billing")
        indexed = dict(server.well_known_applications)
    assert admitted is None
    assert "probe" not in indexed
