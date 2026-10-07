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

"""KajennBus behaviours across the connector, the hub, the spawner and the mixin.

Timeouts that wrap a call, backpressure on the in-process wire, the event lane
from a role process to the server, the decoding of a bus answer, the end of a
role process when its parent goes away, the relaunch of a process that exits
on its own, and the REGISTER ceiling of a network hub.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.http_record import HttpRecord
from kajenn.kbus import (
    CALL_METHOD,
    EVENT_METHOD,
    REGISTER_METHOD,
    REGISTER_PATH,
    REPLY_METHOD,
    Frame,
    KBusCallError,
    KBusClient,
    KBusHub,
    LocalKBus,
)
from kajenn.kbus.address import KBusAddress
from kajenn.kbus.connector import KBusConnector
from kajenn.kbus.control import ControlPayload
from kajenn.kbus.frame import FrameStream
from kajenn.kbus.local import LocalFrameStream

from .test_kbus_external_application import RECIPE, get, no_process, running  # noqa: F401


def write_recipe(
    tmp_path, spawner: str = 'spawner="subprocess"', classes: str = "", **app_classes: str
) -> str:
    """The shared recipe; ``classes`` go before it, ``app_classes`` replace a mount's class."""
    text = RECIPE.replace("SPAWNER", spawner).replace("\nclass Recipe(", classes + "\nclass Recipe(")
    for code, app_class in app_classes.items():
        text = text.replace(f'code="{code}", app_class={code.title()}',
                            f'code="{code}", app_class={app_class}')
    path = tmp_path / "config.py"
    path.write_text(text)
    return str(path)


async def wait_until(predicate, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never reached"
        await asyncio.sleep(0.05)


async def slow_member(server: AsgiServer, release: asyncio.Event) -> KBusClient:
    """A member registered as ``billing`` whose every CALL waits for ``release``."""

    async def on_call(frame: Frame) -> Frame:
        await release.wait()
        return Frame(id=frame.id, method=REPLY_METHOD, path=frame.path, info={"format": "http"},
                     payload=HttpRecord().encode_response(
                         {"status": 200, "headers": [], "body": b"{}"}))

    member = KBusClient(server.children_kbus.address, "billing", on_call=on_call,
                        presentation={"role": "application:billing",
                                      "token": server._kbus_tokens["billing"]})
    await member.connect()
    return member


# -- timeouts around a call --------------------------------------------------------


async def test_an_outer_timeout_around_a_call_raises_timeout_error():
    to_a, to_b = asyncio.Queue(), asyncio.Queue()
    release = asyncio.Event()

    async def on_call(frame: Frame) -> Frame:
        await release.wait()
        return Frame(id=frame.id, method=REPLY_METHOD, path=frame.path)

    a = KBusConnector(LocalFrameStream(to_a, to_b), name="a")
    b = KBusConnector(LocalFrameStream(to_b, to_a), name="b", on_call=on_call)
    a.start()
    b.start()
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                await a.call(Frame(method=CALL_METHOD, path="/slow"))
    finally:
        release.set()
        await a.close()
        await b.close()


@pytest.mark.usefixtures("no_process")
async def test_a_remote_application_past_its_request_timeout_answers_503(tmp_path):
    config = write_recipe(tmp_path, 'spawner="subprocess", request_timeout=0.2')
    release = asyncio.Event()
    async with running(config, wait_member=False) as server:
        member = await slow_member(server, release)
        status, body = await get(server, "/billing/total", b"order=2")
        release.set()
        await member.close()
    assert (status, body) == (503, "Remote application unavailable")


@pytest.mark.usefixtures("no_process")
async def test_kbus_call_across_the_hub_raises_timeout_error(tmp_path):
    release = asyncio.Event()
    async with running(write_recipe(tmp_path), wait_member=False) as server:
        member = await slow_member(server, release)
        with pytest.raises(TimeoutError):
            await server.kbus_call("/billing/total", {"order": 2}, timeout=0.2)
        release.set()
        await member.close()


# -- backpressure on the in-process wire ----------------------------------------------


async def test_forty_concurrent_calls_over_a_local_kbus_all_resolve():
    def on_call(member: Any, frame: Frame) -> Frame:
        return Frame(id=frame.id, method=REPLY_METHOD, path=frame.path,
                     info={"format": "control-json"}, payload=frame.payload)

    hub = KBusHub(on_call=on_call)
    await hub.start()
    try:
        local = LocalKBus("W:busy")
        await hub.attach_local(local)
        answers = await asyncio.wait_for(
            asyncio.gather(*(local.call("/echo", {"n": n}) for n in range(40))), 10)
        assert answers == [{"n": n} for n in range(40)]
        await local.close()
    finally:
        await hub.stop()


# -- the server and its role processes ------------------------------------------------

POSTING = '''

class Posting(Billing):
    @route()
    async def post_local(self):
        await self.server.kbus_post("/local/remember", {"x": 5})
        return {"posted": True}


class Remembering(Local):
    seen = []

    @route()
    def remember(self, x):
        self.seen.append(x)

'''


async def test_a_role_process_post_reaches_an_application_of_the_server(tmp_path):
    async with running(write_recipe(
            tmp_path, classes=POSTING, billing="Posting", local="Remembering")) as server:
        assert await server.kbus_call("/billing/post_local") == {"posted": True}
        local = server.application_at("local")
        await wait_until(lambda: local.seen == [5], timeout=10)


def test_a_parent_and_an_external_application_are_refused_together(tmp_path):
    with pytest.raises(ValueError, match=r"parent=.*external application \(billing\)"):
        AsgiServer(config=write_recipe(tmp_path), parent="uds:/nonexistent/hub.sock")


async def test_a_role_process_ends_when_its_parent_link_is_lost(tmp_path):
    async with running(write_recipe(tmp_path)) as server:
        spawner = server._kbus_spawners["subprocess"]
        process = spawner.processes["application:billing"]
        await server.children_kbus.stop()
        assert await asyncio.wait_for(process.wait(), 20) == 0


FAILING_FIRST = '''

import pathlib


class FailingFirst(Billing):
    async def on_startup(self):
        marker = pathlib.Path(MARKER)
        if not marker.exists():
            marker.write_text(str(os.getpid()))
            os._exit(3)
        await super().on_startup()
'''


async def test_a_process_that_exits_before_register_is_relaunched(tmp_path):
    marker = tmp_path / "marker"
    config = write_recipe(tmp_path, classes=FAILING_FIRST.replace("MARKER", repr(str(marker))),
                          billing="FailingFirst")
    async with running(config) as server:
        _, state = await get(server, "/billing/prepared_state")
    assert state["prepared"] is True
    assert state["pid"] != int(marker.read_text())


async def test_a_killed_process_is_relaunched_once(tmp_path):
    async with running(write_recipe(tmp_path)) as server:
        spawner = server._kbus_spawners["subprocess"]
        ensure = spawner.ensure
        ensured: list[str] = []

        async def counting(role: str, **kwargs: Any) -> None:
            ensured.append(role)
            await ensure(role, **kwargs)

        spawner.ensure = counting
        first = server.children_kbus.resolve("billing")
        os.kill(first.pid, signal.SIGKILL)
        await wait_until(lambda: server.children_kbus.resolve("billing") not in (None, first))
        await asyncio.sleep(2.5)
        second = server.children_kbus.resolve("billing")
        running_pid = spawner.processes["application:billing"].pid
    assert ensured == ["application:billing"]
    assert second.pid == running_pid


# -- decoding a bus answer --------------------------------------------------------------


async def test_a_call_to_no_application_is_a_404():
    with pytest.raises(KBusCallError) as missing:
        await AsgiServer().kbus_call("/nowhere/x")
    assert missing.value.status == 404


class Failing(RoutedApplication):
    @route()
    def broken(self):
        raise LookupError("gone")


async def test_an_http_frame_answers_500_for_an_application_error():
    server = AsgiServer(applications=[(Failing, {"code": "failing"})])
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": "/failing/broken",
             "raw_path": b"/failing/broken", "root_path": "", "query_string": b"",
             "headers": [], "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}
    reply = await server.serve_kbus_frame(Frame(
        method=CALL_METHOD, path="/failing/broken", info={"format": "http"},
        payload=HttpRecord().encode_request(scope, b"")))
    result = HttpRecord().decode_response(reply.payload)
    assert result["status"] == 500
    assert result["body"] == b"Internal Server Error"


# -- the REGISTER ceiling of a network hub ---------------------------------------------


async def test_an_oversized_register_on_a_network_hub_is_refused_before_admission():
    admitted: list[Any] = []
    hub = KBusHub(host="0.0.0.0", secret="s3cret", on_member_joined=admitted.append)
    await hub.start()
    try:
        port = hub.address.rpartition(":")[2]
        reader, writer = await KBusAddress(f"tcp:127.0.0.1:{port}").connect()
        stream = FrameStream(reader, writer, max_size=1 << 20)
        register = Frame(method=REGISTER_METHOD, path=REGISTER_PATH,
                         info={"format": "control-json"},
                         payload=ControlPayload().encode(
                             {"name": "big", "pid": 1, "secret": "s3cret",
                              "padding": "x" * hub.REGISTER_MAX_SIZE}))
        with contextlib.suppress(OSError):
            await stream.write(register)
        assert await asyncio.wait_for(stream.read(), 5) is None
        await stream.close()
        assert admitted == []
        assert hub.members == {}
    finally:
        await hub.stop()


def parked_pair() -> tuple[LocalFrameStream, LocalFrameStream]:
    to_a: asyncio.Queue = asyncio.Queue(maxsize=1)
    to_b: asyncio.Queue = asyncio.Queue(maxsize=1)
    ended = asyncio.Event()
    return (LocalFrameStream(to_a, to_b, ended=ended),
            LocalFrameStream(to_b, to_a, ended=ended))


@pytest.mark.parametrize("closing", ["peer", "own"])
async def test_a_write_parked_on_a_full_queue_is_released_by_a_close(closing):
    a, b = parked_pair()
    await a.write(Frame(method=EVENT_METHOD, path="/fill"))
    parked = [asyncio.create_task(a.write(Frame(method=EVENT_METHOD, path="/wait")))
              for _ in range(3)]
    await asyncio.sleep(0.05)
    assert not any(task.done() for task in parked)
    await (b if closing == "peer" else a).close()
    results = await asyncio.wait_for(asyncio.gather(*parked, return_exceptions=True), 1)
    assert all(isinstance(result, BrokenPipeError) for result in results)
