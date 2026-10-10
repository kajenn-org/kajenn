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

"""KajennBus behaviours across the connector, the hub and the mixin.

Timeouts that wrap a call, backpressure on the in-process wire, the decoding
of a bus answer, and the REGISTER ceiling of a network hub.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.kbus import (
    CALL_METHOD,
    EVENT_METHOD,
    REGISTER_METHOD,
    REGISTER_PATH,
    REPLY_METHOD,
    Frame,
    KBusCallError,
    KBusHub,
    LocalKBus,
)
from kajenn.kbus.address import KBusAddress
from kajenn.kbus.connector import KBusConnector
from kajenn.kbus.control import ControlPayload
from kajenn.kbus.frame import FrameStream
from kajenn.kbus.local import LocalFrameStream



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

# -- decoding a bus answer --------------------------------------------------------------


async def test_a_call_to_no_application_is_a_404():
    with pytest.raises(KBusCallError) as missing:
        await AsgiServer().kbus_call("/nowhere/x")
    assert missing.value.status == 404


class Failing(RoutedApplication):
    @route()
    def broken(self):
        raise LookupError("gone")


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
