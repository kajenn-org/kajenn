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

"""An external application behaves like the same class declared in-process.

Each test boots a real server and a real spawned process: the application's
own kwargs reach the process and the proxy options stay with the mount, the
class mount holds in both processes, the channel of a call reaches the
process, a failed request answers the same error body, and the shutdown stops
the process and removes the hub socket before it completes.
"""

from __future__ import annotations

import asyncio
import os
import time

from kajenn import AsgiServer
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.kbus.address import KBusAddress

from .test_kbus_external_application import RECIPE, get, running

REVIEWED = '''

class Reviewed(Billing):
    def __init__(self, *, greeting="none", **kwargs):
        self.greeting = greeting
        super().__init__(**kwargs)

    @route()
    def greet(self):
        return {"greeting": self.greeting, "db_name": self.db_name, "pid": os.getpid()}

    @route()
    def seen_channel(self, _request=None):
        return {"channel": _request.scope.get("kajenn.channel"), "pid": os.getpid()}

    @route()
    def broken(self):
        raise LookupError("gone")


class Rooted(Reviewed):
    mount = ""

'''


def write_recipe(tmp_path, declaration: str) -> str:
    """The shared recipe with ``billing`` declared by ``declaration``."""
    text = RECIPE.replace("\nclass Recipe(", REVIEWED + "\nclass Recipe(").replace(
        'code="billing", app_class=Billing, SPAWNER', declaration)
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


async def wait_member(server: AsgiServer, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while server.children_kbus.resolve("billing") is None:
        assert time.monotonic() < deadline, "the process never registered"
        await asyncio.sleep(0.05)


async def request(server: AsgiServer, path: str, accept: str) -> tuple[int, str, bytes]:
    """GET ``path`` with ``Accept: accept``: status, content type and raw body."""
    result = await BufferedAsgiEndpoint(server).serve({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": b"", "headers": [(b"accept", accept.encode())],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }, b"")
    headers = {name.lower(): value for name, value in result["headers"]}
    return result["status"], headers.get("content-type"), result["body"]


async def test_the_application_kwargs_reach_the_spawned_process(tmp_path):
    config = write_recipe(tmp_path, 'code="billing", app_class=Reviewed, '
                                    'db_name="main", greeting="hello", spawner="subprocess"')
    async with running(config, wait_member=False) as server:
        await wait_member(server)
        status, seen = await get(server, "/billing/greet")
    assert status == 200
    assert (seen["greeting"], seen["db_name"]) == ("hello", "main")
    assert seen["pid"] != os.getpid()


async def test_the_proxy_options_stay_with_the_mount(tmp_path):
    config = write_recipe(tmp_path, 'code="billing", app_class=Reviewed, '
                                    'spawner="subprocess", request_timeout=5, max_calls=4')
    async with running(config, wait_member=False) as server:
        mount = server.application_at("billing")
        assert (mount.request_timeout, mount.max_calls) == (5, 4)
        await wait_member(server)
        status, seen = await get(server, "/billing/greet")
    assert status == 200
    assert seen["pid"] != os.getpid()


async def test_the_class_mount_holds_in_both_processes(tmp_path):
    config = write_recipe(tmp_path, 'code="billing", app_class=Rooted, spawner="subprocess"')
    async with running(config, wait_member=False) as server:
        assert server.root_application is not None
        assert server.root_application.mount == ""
        assert server.application_at("billing") is None
        await wait_member(server)
        status, seen = await get(server, "/greet")
    assert status == 200
    assert seen["pid"] != os.getpid()


async def test_the_channel_of_a_call_reaches_the_spawned_process(tmp_path):
    config = write_recipe(tmp_path, 'code="billing", app_class=Reviewed, spawner="subprocess"')
    async with running(config, wait_member=False) as server:
        await wait_member(server)
        seen = await server.kbus_call("/billing/seen_channel", {}, channel="mcp")
    assert seen["channel"] == "mcp"
    assert seen["pid"] != os.getpid()


async def test_a_failed_request_answers_the_same_error_in_both_processes(tmp_path):
    requests = [(path, accept)
                for path in ("/billing/no_such_route", "/billing/broken")
                for accept in ("application/json", "*/*")]
    config = write_recipe(tmp_path, 'code="billing", app_class=Reviewed, spawner="subprocess"')
    async with running(config, wait_member=False) as server:
        await wait_member(server)
        spawned = [await request(server, path, accept) for path, accept in requests]
    config = write_recipe(tmp_path, 'code="billing", app_class=Reviewed, mount="billing"')
    async with running(config, wait_member=False) as server:
        inprocess = [await request(server, path, accept) for path, accept in requests]
    assert [status for status, _, _ in inprocess] == [404, 404, 500, 500]
    assert spawned == inprocess


async def test_shutdown_stops_the_process_and_the_hub_before_it_completes(tmp_path):
    config = write_recipe(tmp_path, 'code="billing", app_class=Reviewed, spawner="subprocess"')
    server = AsgiServer(config=config)
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    shutdown_sent = False
    try:
        await wait_member(server)
        socket_path = KBusAddress(server.children_kbus.address).path
        spawner = server._kbus_spawners["subprocess"]
        process = spawner.processes["application:billing"]
        assert os.path.exists(socket_path)
        await inbox.put({"type": "lifespan.shutdown"})
        shutdown_sent = True
        assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
        assert not os.path.exists(socket_path)
        assert not os.path.exists(os.path.dirname(socket_path))
        assert process.returncode is not None
        assert "application:billing" not in spawner.processes
    finally:
        if not shutdown_sent:
            await inbox.put({"type": "lifespan.shutdown"})
        await lifespan
