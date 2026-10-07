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

"""An application in another process, declared like any other.

The configuration names the real application class and a ``spawner``; the
server starts the process, which builds the same configuration in the role of
that one application, connects to the server's hub and presents itself after
its ``on_startup``. The server answers that mount through the member. The
skeletons below fix the behaviour; the bindings are the phase's to write.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest

from kajenn import AsgiServer
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.kbus import KBusCallError, KBusClient
from kajenn.kbus.spawner import SubprocessSpawner

RECIPE = '''
import asyncio
import os

from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Billing(RoutedApplication):
    prepared = False

    async def on_startup(self):
        await asyncio.sleep(0.5)
        self.prepared = True

    @route()
    def total(self, order):
        return {"total": int(order) * 2}

    @route()
    def prepared_state(self):
        return {"prepared": self.prepared, "pid": os.getpid()}

    @route()
    async def ask_local(self):
        return await self.server.kbus_call("/local/ping", {"x": 7})

    @route()
    def configuration(self):
        codes = [kwargs["code"] for _, kwargs in self.server.config.applications()[0]]
        return {"codes": codes, "site": self.server.config.site_kwargs().get("site_name")}


class Local(RoutedApplication):
    @route()
    def ping(self, x):
        return {"pong": x}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    site_name = "billingsite"

    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, SPAWNER)
        apps.application(code="local", app_class=Local)
'''


def write_recipe(tmp_path, spawner='spawner="subprocess"') -> str:
    path = tmp_path / "config.py"
    path.write_text(RECIPE.replace("SPAWNER", spawner))
    return str(path)


async def get(server: AsgiServer, path: str, query: bytes = b"") -> tuple[int, Any]:
    result = await BufferedAsgiEndpoint(server).serve({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": query, "headers": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }, b"")
    return result["status"], json.loads(result["body"])


@asynccontextmanager
async def running(config: str, *, wait_member: bool = True):
    server = AsgiServer(config=config)
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        if wait_member:
            deadline = time.monotonic() + 30
            while server.children_kbus.resolve("billing") is None:
                assert time.monotonic() < deadline, "the process never registered"
                await asyncio.sleep(0.05)
        yield server
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await lifespan


@pytest.fixture
def no_process(monkeypatch):
    async def ensure(self, role, *, token, environment):
        return None
    monkeypatch.setattr(SubprocessSpawner, "ensure", ensure)




def test_the_old_remote_modules_are_gone():
    for name in ("kajenn.remote_connection", "kajenn.remote_runner"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


async def test_an_external_application_answers_http_like_an_inprocess_one(tmp_path):
    # wf:contract: a configuration declaring application(app_class=<a RoutedApplication>,
    # wf:contract: code="billing", spawner="subprocess") boots a server whose GET
    # wf:contract: /billing/total?order=2 answers {"total": 4} once the process registered,
    # wf:contract: the same answer the same class gives when declared without spawner.
    async with running(write_recipe(tmp_path)) as server:
        external = await get(server, "/billing/total", b"order=2")
    async with running(write_recipe(tmp_path, 'code_unused=None'.replace("code_unused=None", "mount='billing'")), wait_member=False) as server:
        inprocess = await get(server, "/billing/total", b"order=2")
    assert external == inprocess == (200, {"total": 4})


async def test_the_server_reaches_the_external_route_through_kbus_call(tmp_path):
    # wf:contract: on that server, await server.kbus_call("/billing/total", {"order": 3})
    # wf:contract: returns {"total": 6}, the handler running in the spawned process.
    async with running(write_recipe(tmp_path)) as server:
        assert await server.kbus_call("/billing/total", {"order": 3}) == {"total": 6}
        _, state = await get(server, "/billing/prepared_state")
    assert state["pid"] != os.getpid()


async def test_the_external_process_reaches_a_route_of_the_server(tmp_path):
    # wf:contract: a handler of the external application calling
    # wf:contract: self.server.kbus_call("/<an in-process app>/<route>", data) gets that
    # wf:contract: route's answer: a path whose first segment is not the hosted application
    # wf:contract: goes to the parent hub, which serves it with serve_kbus_frame.
    async with running(write_recipe(tmp_path)) as server:
        assert await server.kbus_call("/billing/ask_local") == {"pong": 7}


async def test_the_external_process_sees_the_whole_configuration(tmp_path):
    # wf:contract: inside the spawned process, the application's self.server.config is the
    # wf:contract: same site configuration the main server loaded: a value written under
    # wf:contract: applications.billing and a section outside it (databases or storage) are
    # wf:contract: both readable there.
    async with running(write_recipe(tmp_path)) as server:
        status, seen = await get(server, "/billing/configuration")
    assert status == 200
    assert seen == {"codes": ["billing", "local"], "site": "billingsite"}


async def test_a_mount_without_its_member_answers_503(tmp_path, no_process):
    # wf:contract: while the member named after the application code is not in the hub's
    # wf:contract: rubric, an HTTP request to its mount answers 503 and kbus_call raises
    # wf:contract: KBusCallError with status 503; the server boots without waiting for it.
    async with running(write_recipe(tmp_path), wait_member=False) as server:
        status, _ = await get(server, "/billing/total", b"order=2")
        with pytest.raises(KBusCallError) as refused:
            await server.kbus_call("/billing/total", {"order": 2})
    assert status == 503
    assert refused.value.status == 503


async def test_register_comes_after_on_startup(tmp_path):
    # wf:contract: the REGISTER of the spawned process reaches the hub only after the
    # wf:contract: hosted application's on_startup returned: a request answered by the
    # wf:contract: member always finds what on_startup prepared.
    async with running(write_recipe(tmp_path)) as server:
        status, state = await get(server, "/billing/prepared_state")
    assert (status, state["prepared"]) == (200, True)


async def test_a_register_with_the_wrong_token_is_refused(tmp_path, no_process):
    # wf:contract: the spawner hands the process a fresh instance token; a REGISTER for
    # wf:contract: that application code carrying another token is refused, the process's
    # wf:contract: connect fails and the mount keeps answering 503.
    async with running(write_recipe(tmp_path), wait_member=False) as server:
        intruder = KBusClient(server.children_kbus.address, "billing",
                              presentation={"role": "application:billing", "token": "wrong"})
        with pytest.raises(ConnectionError):
            await intruder.connect()
        status, _ = await get(server, "/billing/total", b"order=2")
    assert status == 503


async def test_shutdown_stops_the_spawned_process(tmp_path):
    # wf:contract: at server shutdown the subprocess spawner stops the process it started
    # wf:contract: (terminate, then kill past the shutdown timeout) and no child outlives
    # wf:contract: the server.
    async with running(write_recipe(tmp_path)) as server:
        _, state = await get(server, "/billing/prepared_state")
    with pytest.raises(ProcessLookupError):
        os.kill(state["pid"], 0)
