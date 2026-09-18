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

"""LocalChannel tests: the in-process wire must behave like the socket one.

Same rubric (the member joins through ``attach_local`` with a REGISTER
frame), same envelopes (a CALL is answered with a REPLY reusing its id, via
``send_frame``), same death semantics (hub stop → orphan, deliberate member
close → channel lost with no orphan) and — the point of the phase — the same
bytes: a payload mutated after ``send`` cannot reach the peer.
"""

from __future__ import annotations

import asyncio
import os
import struct
from typing import Any

import pytest

from kajenn.channel import (
    CALL_METHOD,
    EVENT_METHOD,
    REGISTER_METHOD,
    REPLY_METHOD,
    ChannelHub,
    Frame,
    LocalChannel,
    LocalFrameStream,
)
from kajenn.channel.control import ControlPayload

CONTROL = ControlPayload()


def control_frame(*, data=None, **kwargs):
    return Frame(payload=CONTROL.encode(data), **kwargs)


def data_of(frame):
    return CONTROL.decode(frame.payload)


class LocalPeer:
    """An in-process member: records frames, answers CALLs with a REPLY."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.received: list[Frame] = []
        self.orphaned = 0
        self.reply_result: Any = None
        self.reply_events: list[dict[str, Any]] = []
        self.reply_error: Any = None
        self.channel = LocalChannel(name, on_message=self._on_message, on_orphan=self._on_orphan)

    async def join(self, hub: ChannelHub) -> None:
        await self.channel.connect()
        await hub.attach_local(self.channel)

    async def wait_frames(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.received) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"{self.name} got {len(self.received)}/{count} frames")
            await asyncio.sleep(0.01)

    async def _on_message(self, frame: Frame) -> None:
        self.received.append(frame)
        if frame.method == CALL_METHOD:
            data: dict[str, Any] = {"events": list(self.reply_events)}
            if self.reply_error is not None:
                data["error"] = self.reply_error
            else:
                data["result"] = self.reply_result
            await self.channel.send_frame(
                control_frame(id=frame.id, method=REPLY_METHOD, path=frame.path, data=data)
            )

    def _on_orphan(self, channel: LocalChannel) -> None:
        self.orphaned += 1


class LocalHarness:
    """A started hub plus the callback log its tests assert on."""

    def __init__(self) -> None:
        self.joined: list[str] = []
        self.lost: list[str] = []
        self.events: list[tuple[str, Frame]] = []
        self.hub = ChannelHub(
            on_member_joined=lambda member: self.joined.append(member.name),
            on_channel_lost=lambda member: self.lost.append(member.name),
            on_event=lambda member, frame: self.events.append((member.name, frame)),
        )

    async def wait_lost(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.lost) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"hub saw {len(self.lost)}/{count} losses")
            await asyncio.sleep(0.01)


@pytest.fixture
async def harness():
    harness = LocalHarness()
    await harness.hub.start()
    yield harness
    await harness.hub.stop()


async def test_register_handshake_lands_in_the_rubric(harness):
    peer = LocalPeer("W:local-1")
    await peer.join(harness.hub)
    member = harness.hub.resolve("W:local-1")
    assert member is not None
    assert member.pid == os.getpid()
    assert harness.joined == ["W:local-1"]
    assert peer.channel.connected is True
    await peer.channel.close()


async def test_event_from_the_hub_reaches_the_member(harness):
    peer = LocalPeer("W:local-1")
    await peer.join(harness.hub)
    frame_id = await harness.hub.post("W:local-1", "/occupancy", {"users": 3})
    await peer.wait_frames(1)
    frame = peer.received[0]
    assert (frame.method, frame.path, frame.id) == (EVENT_METHOD, "/occupancy", frame_id)
    assert data_of(frame) == {"users": 3}
    await peer.channel.close()


async def test_event_from_the_member_reaches_the_hub(harness):
    peer = LocalPeer("W:local-1")
    await peer.join(harness.hub)
    await peer.channel.send(method=EVENT_METHOD, path="/op/new_user", data={"seq": 1})
    deadline = asyncio.get_running_loop().time() + 5.0
    while not harness.events:
        assert asyncio.get_running_loop().time() < deadline, "hub saw no event"
        await asyncio.sleep(0.01)
    name, frame = harness.events[0]
    assert (name, frame.path, data_of(frame)) == ("W:local-1", "/op/new_user", {"seq": 1})
    await peer.channel.close()


async def test_payload_mutated_after_send_does_not_reach_the_peer(harness):
    peer = LocalPeer("W:local-1")
    await peer.join(harness.hub)
    payload = {"users": 1}
    frame = control_frame(method=EVENT_METHOD, path="/occupancy", data=payload)
    await harness.hub.resolve("W:local-1").write(frame)
    payload["users"] = 999
    await peer.wait_frames(1)
    assert data_of(peer.received[0]) == {"users": 1}

    outbound = {"seq": 1}
    await peer.channel.send(method=EVENT_METHOD, path="/op/new_user", data=outbound)
    outbound["seq"] = 999
    deadline = asyncio.get_running_loop().time() + 5.0
    while not harness.events:
        assert asyncio.get_running_loop().time() < deadline, "hub saw no event"
        await asyncio.sleep(0.01)
    assert data_of(harness.events[0][1]) == {"seq": 1}
    await peer.channel.close()


async def test_call_reply_delivers_the_payload_verbatim(harness):
    peer = LocalPeer("W:local-1")
    peer.reply_result = {"ok": True}
    peer.reply_events = [{"op": "new_user", "seq": 1}]
    await peer.join(harness.hub)

    payload = await asyncio.wait_for(
        harness.hub.call("W:local-1", "/op/new_user", {"identity": "u1"}), timeout=5.0
    )

    assert payload == {"result": {"ok": True}, "events": [{"op": "new_user", "seq": 1}]}
    assert peer.received[0].method == CALL_METHOD
    assert data_of(peer.received[0]) == {"identity": "u1"}
    await peer.channel.close()


async def test_error_reply_rides_the_payload(harness):
    peer = LocalPeer("W:local-1")
    peer.reply_error = "unsupported until phase B"
    await peer.join(harness.hub)
    payload = await harness.hub.call("W:local-1", "/op/new_user", {"identity": "u1"}, timeout=5.0)
    assert payload == {"error": "unsupported until phase B", "events": []}
    await peer.channel.close()


async def test_member_close_is_a_channel_loss_without_orphan(harness):
    peer = LocalPeer("W:local-1")
    await peer.join(harness.hub)
    await peer.channel.close()
    await harness.wait_lost(1)
    assert harness.lost == ["W:local-1"]
    assert peer.orphaned == 0
    assert peer.channel.connected is False
    assert peer.channel.closed is True
    assert harness.hub.resolve("W:local-1") is None


async def test_hub_stop_orphans_the_member(harness):
    peer = LocalPeer("W:local-1")
    await peer.join(harness.hub)
    await harness.hub.stop()
    await asyncio.wait_for(peer.channel.wait_closed(), timeout=5.0)
    assert peer.orphaned == 1
    assert harness.lost == []


async def test_send_before_connect_is_refused():
    channel = LocalChannel("W:local-1")
    with pytest.raises(ConnectionError):
        await channel.send(path="/op/new_user")


async def test_connect_twice_is_refused():
    channel = LocalChannel("W:local-1")
    await channel.connect()
    with pytest.raises(RuntimeError, match="already connected"):
        await channel.connect()
    await channel.close()


async def test_decode_error_is_a_protocol_violation():
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    outbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    stream = LocalFrameStream(inbound, outbound)
    await inbound.put(struct.pack("!4sBII", b"NOPE", 1, 2, 0) + b"{}")
    with pytest.raises(ValueError, match="invalid frame magic"):
        await stream.read()


async def test_oversized_frame_is_refused_both_ways():
    inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    outbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    stream = LocalFrameStream(inbound, outbound, max_size=64)
    with pytest.raises(ValueError, match="exceeds max_size"):
        await stream.write(
            control_frame(method=EVENT_METHOD, path="/big", data={"blob": "x" * 200})
        )
    await inbound.put(struct.pack("!4sBII", b"KJNF", 1, 10, 190) + b"x" * 200)
    with pytest.raises(ValueError, match="exceeds max_size"):
        await stream.read()


async def test_closing_one_end_ends_both_reads():
    channel = LocalChannel("W:local-1")
    hub_stream = channel.hub_stream
    await channel.connect()
    register = await hub_stream.read()
    assert register.method == REGISTER_METHOD
    await hub_stream.close()
    assert await hub_stream.read() is None
    await asyncio.wait_for(channel.wait_closed(), timeout=5.0)
    with pytest.raises(ConnectionError):
        await channel.send(path="/late")


async def test_local_queue_admission_is_bounded_and_close_never_waits():
    inbound, outbound = asyncio.Queue(), asyncio.Queue()
    stream = LocalFrameStream(inbound, outbound)
    for _ in range(16):
        await stream.write(Frame(payload=b"opaque"))
    with pytest.raises(ConnectionError, match="not sent"):
        await stream.write(Frame(payload=b"overflow"))
    assert outbound.qsize() == 16
    await asyncio.wait_for(stream.close(), 1)


async def test_local_queue_limit_is_configurable():
    inbound, outbound = asyncio.Queue(), asyncio.Queue()
    stream = LocalFrameStream(inbound, outbound, max_queue_size=1)
    await stream.write(Frame(payload=b"first"))
    with pytest.raises(ConnectionError, match="not sent"):
        await stream.write(Frame(payload=b"second"))
