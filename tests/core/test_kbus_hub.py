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

"""KBusHub tests: the rubric, presentation, CALL/REPLY/EVENT through the hub, loss and isolation.

The member side is a ``MemberPeer`` over ``KBusClient``: it records the CALLs
and EVENTs it receives and answers CALLs with a REPLY. Correlation itself
(timeouts, abandoned ids, mismatched replies) belongs to ``KBusConnector`` and
is tested in ``test_kbus_connector.py``. The protocol-violation and
no-REGISTER cases write raw bytes on a plain connection.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any

import pytest

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
)
from kajenn.kbus.control import ControlPayload

CONTROL = ControlPayload()


def control_frame(*, data=None, **kwargs):
    return Frame(payload=CONTROL.encode(data), **kwargs)


def data_of(frame):
    return CONTROL.decode(frame.payload)


class MemberPeer:
    """A child on the KajennBus: records what it receives, REPLYs to CALLs."""

    def __init__(self, address: str, name: str) -> None:
        self.received: list[Frame] = []
        self.reply_result: Any = None
        self.reply_events: list[dict[str, Any]] = []
        self.reply_error: Any = None
        self.gate = asyncio.Event()
        self.gate.set()
        self.client = KBusClient(address, name, on_call=self._answer, on_event=self.received.append)

    async def connect(self) -> None:
        await self.client.connect()

    async def close(self) -> None:
        self.gate.set()
        await self.client.close()

    async def wait_frames(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.received) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"{self.client.name} got {len(self.received)}/{count} frames")
            await asyncio.sleep(0.01)

    async def _answer(self, call: Frame) -> Frame:
        self.received.append(call)
        await self.gate.wait()
        data: dict[str, Any] = {"events": list(self.reply_events)}
        if self.reply_error is not None:
            data["error"] = self.reply_error
        else:
            data["result"] = self.reply_result
        return control_frame(id=call.id, method=REPLY_METHOD, path=call.path, data=data)


class HubHarness:
    """A started hub plus the callback log its tests assert on."""

    def __init__(self, **kwargs: Any) -> None:
        self.joined: list[str] = []
        self.lost: list[str] = []
        self.events: list[tuple[str, Frame]] = []
        self.hub = KBusHub(
            on_member_joined=lambda member: self.joined.append(member.name),
            on_member_lost=lambda member: self.lost.append(member.name),
            on_event=lambda member, frame: self.events.append((member.name, frame)),
            **kwargs,
        )

    async def wait_members(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.hub.members) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"hub has {len(self.hub.members)}/{count} members")
            await asyncio.sleep(0.01)

    async def wait_lost(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.lost) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"hub saw {len(self.lost)}/{count} losses")
            await asyncio.sleep(0.01)


@pytest.fixture
def socket_dir():
    path = tempfile.mkdtemp(prefix="gnrhubtest_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
async def uds_harness(socket_dir):
    harness = HubHarness(path=os.path.join(socket_dir, "hub.sock"))
    await harness.hub.start()
    yield harness
    await harness.hub.stop()


async def register_raw(path: str, data: Any) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(control_frame(method=REGISTER_METHOD, path=REGISTER_PATH, data=data).encode())
    await writer.drain()
    return reader, writer


async def test_register_lands_in_the_rubric(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()
    member = uds_harness.hub.resolve("W:one")
    assert member is not None
    assert member.name == "W:one"
    assert member.pid == os.getpid()
    assert uds_harness.joined == ["W:one"]
    assert peer.client.welcome == {}
    assert uds_harness.hub.resolve("W:missing") is None
    await peer.close()


async def test_register_over_tcp():
    harness = HubHarness(host="127.0.0.1", port=0)
    await harness.hub.start()
    assert harness.hub.address.startswith("tcp:127.0.0.1:")
    peer = MemberPeer(harness.hub.address, "W:tcp")
    await peer.connect()
    assert harness.hub.resolve("W:tcp") is not None
    await peer.close()
    await harness.hub.stop()


async def test_owned_socket_directory_is_private_and_removed():
    harness = HubHarness()
    await harness.hub.start()
    path = str(harness.hub.path)
    owned_dir = os.path.dirname(path)
    assert os.stat(owned_dir).st_mode & 0o777 == 0o700
    await harness.hub.stop()
    assert not os.path.exists(path)
    assert not os.path.exists(owned_dir)


async def test_call_returns_the_reply_payload_verbatim(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.reply_result = {"ok": 1}
    peer.reply_events = [{"op": "new_user", "seq": 1}, {"op": "drop_user", "seq": 2}]
    await peer.connect()

    payload = await uds_harness.hub.call("W:one", "/op/new_user", {"identity": "u1"}, timeout=5.0)

    assert payload == {"result": {"ok": 1}, "events": peer.reply_events}
    assert peer.received[0].method == CALL_METHOD
    assert peer.received[0].path == "/op/new_user"
    assert peer.received[0].info == {"format": "control-json"}
    assert data_of(peer.received[0]) == {"identity": "u1"}
    await peer.close()


async def test_payload_level_error_is_delivered_not_raised(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.reply_error = "unsupported until phase B"
    await peer.connect()

    payload = await uds_harness.hub.call("W:one", "/op/http", {"http": {}}, timeout=5.0)

    assert payload == {"error": "unsupported until phase B", "events": []}
    await peer.close()


async def test_error_reply_raises_the_call_error(uds_harness):
    client = KBusClient(uds_harness.hub.address, "W:none")
    await client.connect()
    with pytest.raises(KBusCallError) as raised:
        await uds_harness.hub.call("W:none", "/op/x", timeout=5.0)
    assert raised.value.path == "/op/x"
    assert raised.value.error.startswith("LookupError:")
    await client.close()


async def test_call_timeout_is_an_unknown_outcome(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.gate.clear()
    await peer.connect()

    with pytest.raises(ConnectionError) as raised:
        await uds_harness.hub.call("W:one", "/op/silent", None, timeout=0.1)
    assert raised.value.outcome == "unknown"
    await peer.close()


async def test_a_call_without_timeout_waits_for_its_reply(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.gate.clear()
    peer.reply_result = "late"
    await peer.connect()

    parked = asyncio.create_task(uds_harness.hub.call("W:one", "/op/slow", None))
    await peer.wait_frames(1)
    await asyncio.sleep(0.2)
    assert not parked.done()

    peer.gate.set()
    assert (await parked)["result"] == "late"
    await peer.close()


async def test_member_death_fails_its_parked_calls(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    other = MemberPeer(uds_harness.hub.address, "W:two")
    peer.gate.clear()
    other.gate.clear()
    await peer.connect()
    await other.connect()

    parked = asyncio.create_task(uds_harness.hub.call("W:one", "/op/slow", None))
    survivor = asyncio.create_task(uds_harness.hub.call("W:two", "/op/slow", None))
    await peer.wait_frames(1)
    await other.wait_frames(1)

    await peer.client.close()
    await uds_harness.wait_lost(1)

    with pytest.raises(ConnectionError):
        await parked
    assert not survivor.done()

    survivor.cancel()
    await other.close()


async def test_stop_fails_every_parked_call(socket_dir):
    harness = HubHarness(path=os.path.join(socket_dir, "hub.sock"))
    await harness.hub.start()
    peer = MemberPeer(harness.hub.address, "W:one")
    peer.gate.clear()
    await peer.connect()

    parked = asyncio.create_task(harness.hub.call("W:one", "/op/slow", None))
    await peer.wait_frames(1)

    await harness.hub.stop()

    with pytest.raises(ConnectionError):
        await parked
    assert harness.lost == []
    await peer.close()


async def test_call_on_unknown_member_raises_lookup(uds_harness):
    with pytest.raises(LookupError):
        await uds_harness.hub.call("W:ghost", "/op/new_user", None, timeout=0.5)


async def test_post_reaches_one_member_only(uds_harness):
    one = MemberPeer(uds_harness.hub.address, "W:one")
    two = MemberPeer(uds_harness.hub.address, "W:two")
    await one.connect()
    await two.connect()

    await uds_harness.hub.post("W:one", "/occupancy", {"users": 3})
    await one.wait_frames(1)
    assert one.received[0].method == EVENT_METHOD
    assert data_of(one.received[0]) == {"users": 3}
    assert two.received == []
    await one.close()
    await two.close()


async def test_inbound_event_reaches_the_consumer(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()

    await peer.client.post("/op/drop_user", {"seq": 7})
    deadline = asyncio.get_running_loop().time() + 5.0
    while not uds_harness.events:
        assert asyncio.get_running_loop().time() < deadline, "no event reached the hub"
        await asyncio.sleep(0.01)
    name, frame = uds_harness.events[0]
    assert (name, frame.path, data_of(frame)) == ("W:one", "/op/drop_user", {"seq": 7})
    await peer.close()


async def test_a_slow_event_consumer_does_not_delay_the_reply_behind_it(uds_harness):
    """Serving is a task: the member's link stays free for the REPLY."""
    gate = asyncio.Event()
    served = []

    async def slow_on_event(member, frame):
        served.append(frame.path)
        await gate.wait()

    uds_harness.hub.on_event = slow_on_event
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.reply_result = {"ok": 1}
    await peer.connect()

    await peer.client.post("/op/slow", {"seq": 1})
    deadline = asyncio.get_running_loop().time() + 5.0
    while not served:
        assert asyncio.get_running_loop().time() < deadline, "the consumer never ran"
        await asyncio.sleep(0.01)
    payload = await asyncio.wait_for(uds_harness.hub.call("W:one", "/op/ping"), timeout=5.0)
    assert payload["result"] == {"ok": 1}

    gate.set()
    await peer.close()


async def test_inbound_call_is_served_by_the_hub(socket_dir):
    def on_call(member, frame):
        return control_frame(id=frame.id, method=REPLY_METHOD, path=frame.path,
                             data={"member": member.name, "asked": data_of(frame)})

    harness = HubHarness(path=os.path.join(socket_dir, "hub.sock"), on_call=on_call)
    await harness.hub.start()
    peer = MemberPeer(harness.hub.address, "W:one")
    await peer.connect()
    assert await peer.client.call("/ask", {"q": 1}) == {"member": "W:one", "asked": {"q": 1}}
    await peer.close()
    await harness.hub.stop()


async def test_member_eof_sweeps_the_rubric(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()

    await peer.close()
    await uds_harness.wait_lost(1)
    assert uds_harness.lost == ["W:one"]
    assert uds_harness.hub.resolve("W:one") is None


async def test_deliberate_hub_stop_fires_no_member_lost(socket_dir):
    harness = HubHarness(path=os.path.join(socket_dir, "hub.sock"))
    await harness.hub.start()
    peer = MemberPeer(harness.hub.address, "W:one")
    await peer.connect()

    await harness.hub.stop()
    await asyncio.sleep(0.1)
    assert harness.lost == []
    await peer.close()


async def test_protocol_violation_isolates_that_member(uds_harness):
    survivor = MemberPeer(uds_harness.hub.address, "W:good")
    await survivor.connect()

    reader, writer = await register_raw(uds_harness.hub.path, {"name": "W:bad", "pid": 1})
    await uds_harness.wait_members(2)
    payload = b"NOTWSX-garbage"
    writer.write(len(payload).to_bytes(4, "big") + payload)
    await writer.drain()

    await uds_harness.wait_lost(1)
    assert uds_harness.lost == ["W:bad"]
    assert uds_harness.hub.resolve("W:bad") is None
    writer.close()

    survivor.reply_result = "alive"
    payload = await uds_harness.hub.call("W:good", "/ping", None, timeout=5.0)
    assert payload["result"] == "alive"
    await survivor.close()


async def test_duplicate_name_refuses_the_new_connection(uds_harness):
    first = MemberPeer(uds_harness.hub.address, "W:one")
    await first.connect()
    registered = uds_harness.hub.resolve("W:one")

    second = MemberPeer(uds_harness.hub.address, "W:one")
    with pytest.raises(ConnectionError):
        await second.connect()

    assert uds_harness.hub.resolve("W:one") is registered
    assert uds_harness.joined == ["W:one"]
    assert uds_harness.lost == []
    first.reply_result = "still here"
    payload = await uds_harness.hub.call("W:one", "/ping", None, timeout=5.0)
    assert payload["result"] == "still here"
    await first.close()


async def test_connection_without_register_is_rejected(uds_harness):
    reader, writer = await asyncio.open_unix_connection(uds_harness.hub.path)
    writer.write(control_frame(method=EVENT_METHOD, path="/hello", data=None).encode())
    await writer.drain()
    assert await reader.read() == b""
    assert uds_harness.hub.members == {}
    writer.close()


async def test_address_before_start_raises(socket_dir):
    hub = KBusHub(path=os.path.join(socket_dir, "hub.sock"))
    assert not hub.started
    with pytest.raises(RuntimeError):
        hub.address
    await hub.stop()


async def test_path_and_host_together_are_rejected():
    with pytest.raises(ValueError):
        KBusHub(path="/tmp/x.sock", host="127.0.0.1")


@pytest.mark.parametrize("pid", [{}, "invalid"])
async def test_malformed_register_pid_closes_only_offending_socket(uds_harness, pid):
    survivor = MemberPeer(uds_harness.hub.address, "W:good")
    await survivor.connect()
    reader, writer = await register_raw(uds_harness.hub.path, {"name": "W:bad", "pid": pid})
    try:
        assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        assert uds_harness.hub.resolve("W:bad") is None
        survivor.reply_result = "alive"
        reply = await uds_harness.hub.call("W:good", "/ping", timeout=1)
        assert reply["result"] == "alive"
    finally:
        writer.close()
        await writer.wait_closed()
        await survivor.close()
