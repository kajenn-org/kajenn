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

"""ChannelHub tests: the rubric, the CALL/REPLY/EVENT envelopes, EOF and isolation.

The member side is a ``MemberPeer`` over the package's own ``FrameStream``
(both ends of the codec are exercised): it REGISTERs, records what it
receives and answers CALLs with a REPLY **reusing the CALL id** — the
correlation the hub keys its futures on, which ``ChannelClient.send`` cannot
express since it mints a fresh id per frame. The protocol-violation and
no-REGISTER cases write raw bytes on a plain connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any

import pytest

from kajenn.channel import (
    CALL_METHOD,
    EVENT_METHOD,
    REGISTER_METHOD,
    REGISTER_PATH,
    REPLY_METHOD,
    ChannelHub,
    Frame,
    FrameStream,
)
from kajenn.channel.control import ControlPayload

CONTROL = ControlPayload()


def control_frame(*, data=None, **kwargs):
    return Frame(payload=CONTROL.encode(data), **kwargs)


def data_of(frame):
    return CONTROL.decode(frame.payload)


class MemberPeer:
    """A child on the channel: REPLYs to CALLs reusing their id."""

    def __init__(self, address: str, name: str) -> None:
        self.address = address
        self.name = name
        self.received: list[Frame] = []
        self.reply_result: Any = None
        self.reply_events: list[dict[str, Any]] = []
        self.reply_error: Any = None
        self.answer_calls = True
        self.stream: FrameStream | None = None
        self._task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        transport, _, rest = self.address.partition(":")
        if transport == "uds":
            reader, writer = await asyncio.open_unix_connection(rest)
        else:
            host, _, port = rest.rpartition(":")
            reader, writer = await asyncio.open_connection(host, int(port))
        self.stream = FrameStream(reader, writer)
        await self.stream.write(
            control_frame(
                method=REGISTER_METHOD,
                path=REGISTER_PATH,
                data={"name": self.name, "pid": os.getpid()},
            )
        )
        self._task = asyncio.create_task(self._receive_loop())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.stream.close()

    async def send(
        self, method: str, path: str, data: Any = None, *, id: str | None = None
    ) -> None:
        kwargs = {"id": id} if id is not None else {}
        await self.stream.write(control_frame(method=method, path=path, data=data, **kwargs))

    async def wait_frames(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.received) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"{self.name} got {len(self.received)}/{count} frames")
            await asyncio.sleep(0.01)

    async def _receive_loop(self) -> None:
        while True:
            frame = await self.stream.read()
            if frame is None:
                return
            self.received.append(frame)
            if frame.method == CALL_METHOD and self.answer_calls:
                await self._answer(frame)

    async def _answer(self, call: Frame) -> None:
        data: dict[str, Any] = {"events": list(self.reply_events)}
        if self.reply_error is not None:
            data["error"] = self.reply_error
        else:
            data["result"] = self.reply_result
        await self.stream.write(
            control_frame(id=call.id, method=REPLY_METHOD, path=call.path, data=data)
        )


class HubHarness:
    """A started hub plus the callback log its tests assert on."""

    def __init__(self, **kwargs: Any) -> None:
        self.joined: list[str] = []
        self.lost: list[str] = []
        self.events: list[tuple[str, Frame]] = []
        self.hub = ChannelHub(
            on_member_joined=lambda member: self.joined.append(member.name),
            on_channel_lost=lambda member: self.lost.append(member.name),
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


async def test_register_lands_in_the_rubric(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()
    await uds_harness.wait_members(1)
    member = uds_harness.hub.resolve("W:one")
    assert member is not None
    assert member.name == "W:one"
    assert member.pid == os.getpid()
    assert uds_harness.joined == ["W:one"]
    assert uds_harness.hub.resolve("W:missing") is None
    await peer.close()


async def test_register_over_tcp():
    harness = HubHarness(host="127.0.0.1", port=0)
    await harness.hub.start()
    assert harness.hub.address.startswith("tcp:127.0.0.1:")
    peer = MemberPeer(harness.hub.address, "W:tcp")
    await peer.connect()
    await harness.wait_members(1)
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
    await uds_harness.wait_members(1)

    payload = await uds_harness.hub.call("W:one", "/op/new_user", {"identity": "u1"}, timeout=5.0)

    assert payload == {"result": {"ok": 1}, "events": peer.reply_events}
    assert peer.received[0].method == CALL_METHOD
    assert peer.received[0].path == "/op/new_user"
    assert data_of(peer.received[0]) == {"identity": "u1"}
    await peer.close()


async def test_error_reply_is_delivered_not_raised(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.reply_error = "unsupported until phase B"
    await peer.connect()
    await uds_harness.wait_members(1)

    payload = await uds_harness.hub.call("W:one", "/op/http", {"http": {}}, timeout=5.0)

    assert payload == {"error": "unsupported until phase B", "events": []}
    await peer.close()


async def test_reply_without_a_parked_caller_is_dropped(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.answer_calls = False
    await peer.connect()
    await uds_harness.wait_members(1)

    with pytest.raises(TimeoutError):
        await uds_harness.hub.call("W:one", "/op/slow", None, timeout=0.1)
    await peer._answer(peer.received[0])
    await asyncio.sleep(0.05)

    assert uds_harness.hub._pending == {}
    assert uds_harness.hub.resolve("W:one") is not None
    await peer.close()


async def test_call_timeout_unparks_the_future(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.answer_calls = False
    await peer.connect()
    await uds_harness.wait_members(1)

    with pytest.raises(TimeoutError):
        await uds_harness.hub.call("W:one", "/op/silent", None, timeout=0.1)
    assert uds_harness.hub._pending == {}
    await peer.close()


async def test_a_call_without_timeout_waits_for_its_reply(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.answer_calls = False
    peer.reply_result = "late"
    await peer.connect()
    await uds_harness.wait_members(1)

    parked = asyncio.create_task(uds_harness.hub.call("W:one", "/op/slow", None))
    await peer.wait_frames(1)
    await asyncio.sleep(0.2)
    assert not parked.done()

    await peer._answer(peer.received[0])
    assert (await parked)["result"] == "late"
    assert uds_harness.hub._pending == {}
    await peer.close()


async def test_member_death_fails_its_parked_calls(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    other = MemberPeer(uds_harness.hub.address, "W:two")
    peer.answer_calls = False
    other.answer_calls = False
    await peer.connect()
    await other.connect()
    await uds_harness.wait_members(2)

    parked = asyncio.create_task(uds_harness.hub.call("W:one", "/op/slow", None))
    survivor = asyncio.create_task(uds_harness.hub.call("W:two", "/op/slow", None))
    await peer.wait_frames(1)
    await other.wait_frames(1)

    await peer.close()
    await uds_harness.wait_lost(1)

    with pytest.raises(ConnectionError, match="channel to W:one lost"):
        await parked
    assert not survivor.done()

    survivor.cancel()
    await other.close()


async def test_stop_fails_every_parked_call(socket_dir):
    harness = HubHarness(path=os.path.join(socket_dir, "hub.sock"))
    await harness.hub.start()
    peer = MemberPeer(harness.hub.address, "W:one")
    peer.answer_calls = False
    await peer.connect()
    await harness.wait_members(1)

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


async def test_call_frame_requires_call_and_rejects_duplicate_pending_id(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()
    await uds_harness.wait_members(1)
    peer.answer_calls = False
    with pytest.raises(ValueError, match="requires a CALL"):
        await uds_harness.hub.call_frame(
            "W:one", Frame(id="event", method=EVENT_METHOD, path="/event")
        )
    first = asyncio.create_task(
        uds_harness.hub.call_frame("W:one", Frame(id="same", method=CALL_METHOD, path="/one"))
    )
    await peer.wait_frames(1)
    with pytest.raises(RuntimeError, match="already pending"):
        await uds_harness.hub.call_frame("W:one", Frame(id="same", method=CALL_METHOD, path="/two"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_reply_from_another_member_cannot_complete_a_call(uds_harness):
    one = MemberPeer(uds_harness.hub.address, "W:one")
    two = MemberPeer(uds_harness.hub.address, "W:two")
    await one.connect()
    await two.connect()
    await uds_harness.wait_members(2)
    one.answer_calls = False
    pending = asyncio.create_task(
        uds_harness.hub.call_frame(
            "W:one", Frame(id="owned", method=CALL_METHOD, path="/ask"), timeout=1
        )
    )
    await one.wait_frames(1)
    await two.send(REPLY_METHOD, "/ask", {"result": "spoof"}, id="owned")
    await asyncio.wait_for(two._task, timeout=1)
    assert not pending.done()
    assert uds_harness.hub.resolve("W:two") is None
    await one.send(REPLY_METHOD, "/ask", {"result": "real"}, id="owned")
    assert data_of(await pending) == {"result": "real"}


async def test_late_reply_cannot_resolve_reused_id_or_wrong_path(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.answer_calls = False
    await peer.connect()
    await uds_harness.wait_members(1)
    expired = Frame(id="late", method=CALL_METHOD, path="/old")
    with pytest.raises(TimeoutError):
        await uds_harness.hub.call_frame("W:one", expired, timeout=0.01)
    with pytest.raises(RuntimeError, match="awaiting a late reply"):
        await uds_harness.hub.call_frame("W:one", Frame(id="late", method=CALL_METHOD, path="/new"))
    await peer.send(REPLY_METHOD, "/wrong", {"result": "wrong"}, id="late")
    await uds_harness.wait_lost(1)
    assert uds_harness.hub.resolve("W:one") is None

    replacement_peer = MemberPeer(uds_harness.hub.address, "W:one")
    replacement_peer.answer_calls = False
    await replacement_peer.connect()
    await uds_harness.wait_members(1)
    replacement = asyncio.create_task(uds_harness.hub.call_frame("W:one", expired))
    await replacement_peer.wait_frames(1)
    await replacement_peer.send(REPLY_METHOD, "/old", {"result": "fresh"}, id="late")
    assert data_of(await replacement) == {"result": "fresh"}


async def test_cancel_during_write_reserves_id_until_late_reply(uds_harness, monkeypatch):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.answer_calls = False
    await peer.connect()
    await uds_harness.wait_members(1)
    member = uds_harness.hub.resolve("W:one")
    original_write = type(member).write
    transmitted = asyncio.Event()
    release = asyncio.Event()

    async def blocked_after_write(self, frame):
        await original_write(self, frame)
        transmitted.set()
        await release.wait()

    monkeypatch.setattr(type(member), "write", blocked_after_write)
    frame = Frame(id="during-write", method=CALL_METHOD, path="/old")
    call = asyncio.create_task(uds_harness.hub.call_frame("W:one", frame))
    await transmitted.wait()
    await peer.wait_frames(1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    with pytest.raises(RuntimeError, match="awaiting a late reply"):
        await uds_harness.hub.call_frame("W:one", frame)
    await peer.send(REPLY_METHOD, "/old", {"result": "late"}, id="during-write")
    await asyncio.sleep(0.01)

    monkeypatch.setattr(type(member), "write", original_write)
    replacement = asyncio.create_task(uds_harness.hub.call_frame("W:one", frame))
    await peer.wait_frames(2)
    await peer.send(REPLY_METHOD, "/old", {"result": "fresh"}, id="during-write")
    assert data_of(await replacement) == {"result": "fresh"}
    release.set()


async def test_inbound_event_background_work_is_bounded(socket_dir, caplog):
    gate = asyncio.Event()

    async def held_event(member, frame):
        await gate.wait()

    harness = HubHarness(path=os.path.join(socket_dir, "bounded.sock"))
    harness.hub.on_event = held_event
    harness.hub.max_event_tasks = 1
    await harness.hub.start()
    try:
        peer = MemberPeer(harness.hub.address, "W:one")
        await peer.connect()
        await harness.wait_members(1)
        await peer.send(EVENT_METHOD, "/first")
        while len(harness.hub._event_tasks) != 1:
            await asyncio.sleep(0)
        await peer.send(EVENT_METHOD, "/dropped")
        await asyncio.sleep(0.02)
        assert len(harness.hub._event_tasks) == 1
        assert "event task limit 1 reached" in caplog.text
        gate.set()
    finally:
        await harness.hub.stop()


async def test_post_reaches_one_member_only(uds_harness):
    one = MemberPeer(uds_harness.hub.address, "W:one")
    two = MemberPeer(uds_harness.hub.address, "W:two")
    await one.connect()
    await two.connect()
    await uds_harness.wait_members(2)

    frame_id = await uds_harness.hub.post("W:one", "/occupancy", {"users": 3})
    await one.wait_frames(1)
    assert one.received[0].id == frame_id
    assert one.received[0].method == EVENT_METHOD
    assert data_of(one.received[0]) == {"users": 3}
    assert two.received == []
    await one.close()
    await two.close()


async def test_inbound_event_reaches_the_consumer(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()
    await uds_harness.wait_members(1)

    await peer.send(EVENT_METHOD, "/op/drop_user", {"seq": 7})
    deadline = asyncio.get_running_loop().time() + 5.0
    while not uds_harness.events:
        assert asyncio.get_running_loop().time() < deadline, "no event reached the hub"
        await asyncio.sleep(0.01)
    name, frame = uds_harness.events[0]
    assert (name, frame.path, data_of(frame)) == ("W:one", "/op/drop_user", {"seq": 7})
    await peer.close()


async def test_a_slow_event_consumer_does_not_delay_the_reply_behind_it(uds_harness):
    """Serving is a task: the member's receive loop stays free for the REPLY."""
    gate = asyncio.Event()
    served = []

    async def slow_on_event(member, frame):
        served.append(frame.path)
        await gate.wait()

    uds_harness.hub.on_event = slow_on_event
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    peer.reply_result = {"ok": 1}
    await peer.connect()
    await uds_harness.wait_members(1)

    await peer.send(EVENT_METHOD, "/op/slow", {"seq": 1})
    deadline = asyncio.get_running_loop().time() + 5.0
    while not served:
        assert asyncio.get_running_loop().time() < deadline, "the consumer never ran"
        await asyncio.sleep(0.01)
    payload = await asyncio.wait_for(uds_harness.hub.call("W:one", "/op/ping"), timeout=5.0)
    assert payload["result"] == {"ok": 1}

    gate.set()
    await peer.close()


async def test_inbound_call_is_an_unexpected_envelope(uds_harness, caplog):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()
    await uds_harness.wait_members(1)

    with caplog.at_level(logging.WARNING, logger="kajenn.channel.hub"):
        await peer.send(CALL_METHOD, "/ask", {"q": 1})
        await asyncio.sleep(0.1)

    assert "Unknown envelope CALL from W:one" in caplog.text
    assert peer.received == []
    assert uds_harness.hub.resolve("W:one") is not None
    await peer.close()


async def test_member_eof_sweeps_the_rubric(uds_harness):
    peer = MemberPeer(uds_harness.hub.address, "W:one")
    await peer.connect()
    await uds_harness.wait_members(1)

    await peer.close()
    await uds_harness.wait_lost(1)
    assert uds_harness.lost == ["W:one"]
    assert uds_harness.hub.resolve("W:one") is None


async def test_deliberate_hub_stop_fires_no_channel_lost(socket_dir):
    harness = HubHarness(path=os.path.join(socket_dir, "hub.sock"))
    await harness.hub.start()
    peer = MemberPeer(harness.hub.address, "W:one")
    await peer.connect()
    await harness.wait_members(1)

    await harness.hub.stop()
    await asyncio.sleep(0.1)
    assert harness.lost == []
    await peer.close()


async def test_protocol_violation_isolates_that_member(uds_harness):
    survivor = MemberPeer(uds_harness.hub.address, "W:good")
    await survivor.connect()
    await uds_harness.wait_members(1)

    reader, writer = await asyncio.open_unix_connection(uds_harness.hub.path)
    writer.write(
        control_frame(
            method=REGISTER_METHOD, path=REGISTER_PATH, data={"name": "W:bad", "pid": 1}
        ).encode()
    )
    await writer.drain()
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
    await uds_harness.wait_members(1)
    registered = uds_harness.hub.resolve("W:one")

    second = MemberPeer(uds_harness.hub.address, "W:one")
    await second.connect()
    # The refusal is the closed stream: the newcomer's receive loop reads EOF.
    await asyncio.wait_for(second._task, timeout=5.0)

    assert uds_harness.hub.resolve("W:one") is registered
    assert uds_harness.joined == ["W:one"]
    assert uds_harness.lost == []
    first.reply_result = "still here"
    payload = await uds_harness.hub.call("W:one", "/ping", None, timeout=5.0)
    assert payload["result"] == "still here"
    await second.close()
    await first.close()


async def test_connection_without_register_is_rejected(uds_harness):
    reader, writer = await asyncio.open_unix_connection(uds_harness.hub.path)
    writer.write(control_frame(method=EVENT_METHOD, path="/hello", data=None).encode())
    await writer.drain()
    assert await reader.read() == b""
    assert uds_harness.hub.members == {}
    writer.close()


async def test_address_before_start_raises(socket_dir):
    hub = ChannelHub(path=os.path.join(socket_dir, "hub.sock"))
    assert not hub.started
    with pytest.raises(RuntimeError):
        hub.address
    await hub.stop()


async def test_path_and_host_together_are_rejected():
    with pytest.raises(ValueError):
        ChannelHub(path="/tmp/x.sock", host="127.0.0.1")


@pytest.mark.parametrize("pid", [{}, "invalid"])
async def test_malformed_register_pid_closes_only_offending_socket(uds_harness, pid):
    survivor = MemberPeer(uds_harness.hub.address, "W:good")
    await survivor.connect()
    await uds_harness.wait_members(1)
    reader, writer = await asyncio.open_unix_connection(uds_harness.hub.path)
    try:
        writer.write(control_frame(method=REGISTER_METHOD, path=REGISTER_PATH,
                                   data={"name": "W:bad", "pid": pid}).encode())
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        assert uds_harness.hub.resolve("W:bad") is None
        survivor.reply_result = "alive"
        reply = await uds_harness.hub.call("W:good", "/ping", timeout=1)
        assert reply["result"] == "alive"
    finally:
        writer.close()
        await writer.wait_closed()
        await survivor.close()
