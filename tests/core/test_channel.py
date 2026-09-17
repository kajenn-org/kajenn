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

"""Channel tests (SPECIFICATION.md §4): the child connects to a fake hub over
UDS, REGISTERs, and EOF is the death signal; the CommunicationMixin arms the
parent side from ``parent=`` and hooks the lifespan cooperatively (D16/D17).

The fake hub is a plain asyncio UDS server built in the test: it decodes
frames with the package's own ``FrameStream`` (so both ends of the protocol
are exercised) and records what it receives.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import struct
import tempfile

import pytest

from kajenn import BaseApplication, BaseServer
from kajenn.channel import (
    MAX_FRAME_SIZE,
    REGISTER_METHOD,
    REGISTER_PATH,
    ChannelClient,
    Frame,
    FrameStream,
)
from kajenn.communication import CommunicationMixin
from kajenn.channel.control import ControlPayload
from kajenn.channel.frame import FrameCodec

CONTROL = ControlPayload()


def control_frame(*, data=None, **kwargs):
    return Frame(payload=CONTROL.encode(data), **kwargs)


def data_of(frame):
    return CONTROL.decode(frame.payload)


class FakeHub:
    """A plain asyncio UDS server standing in for the orchestration hub."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.frames: list[Frame] = []
        self.streams: list[FrameStream] = []
        self.eofs: list[FrameStream] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._serve, path=self.path)

    async def stop(self) -> None:
        for stream in self.streams:
            await stream.close()
        self._server.close()
        await self._server.wait_closed()

    async def wait_frames(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.frames) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"hub received {len(self.frames)}/{count} frames")
            await asyncio.sleep(0.01)

    async def wait_eofs(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.eofs) < count:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"hub saw {len(self.eofs)}/{count} EOFs")
            await asyncio.sleep(0.01)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stream = FrameStream(reader, writer)
        self.streams.append(stream)
        while True:
            frame = await stream.read()
            if frame is None:
                break
            self.frames.append(frame)
        self.eofs.append(stream)


class ChannelServer(CommunicationMixin, BaseServer):
    """The composition under test: communication capability over the base."""


class RecordingApp(BaseApplication):
    """Minimal app recording lifecycle hook calls to a shared list.

    Constructor kwargs peeled here (cooperative chain): ``events`` — the
    shared list the hooks append to.
    """

    def __init__(self, **kwargs: object) -> None:
        self.events: list[str] = kwargs.pop("events")
        super().__init__(**kwargs)

    def on_startup(self) -> None:
        self.events.append("on_startup")

    def on_shutdown(self) -> None:
        self.events.append("on_shutdown")


@pytest.fixture
def hub_path():
    tmpdir = tempfile.mkdtemp(prefix="gnrchan_")
    yield os.path.join(tmpdir, "hub.sock")
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
async def hub(hub_path):
    fake = FakeHub(hub_path)
    await fake.start()
    yield fake
    await fake.stop()


async def stream_pair(max_size: int = MAX_FRAME_SIZE) -> tuple[FrameStream, FrameStream]:
    """Two FrameStreams over a connected socketpair (an in-process wire)."""
    left, right = socket.socketpair()
    reader_l, writer_l = await asyncio.open_connection(sock=left)
    reader_r, writer_r = await asyncio.open_connection(sock=right)
    one = FrameStream(reader_l, writer_l, max_size=max_size)
    two = FrameStream(reader_r, writer_r, max_size=max_size)
    return one, two


class TestFrameProtocol:
    def test_frame_id_generated_when_not_given(self) -> None:
        one, two = control_frame(), control_frame()
        assert one.id and two.id and one.id != two.id
        assert control_frame(id="fixed").id == "fixed"

    async def test_round_trip(self) -> None:
        one, two = await stream_pair()
        sent = control_frame(method="POST", path="/events/ready", data={"n": 1})
        await one.write(sent)
        received = await two.read()
        assert received is not None
        assert received.id == sent.id
        assert received.method == "POST"
        assert received.path == "/events/ready"
        assert data_of(received) == {"n": 1}
        await one.close()
        await two.close()

    async def test_eof_reads_none(self) -> None:
        one, two = await stream_pair()
        await one.close()
        assert await two.read() is None
        await two.close()

    async def test_oversized_write_raises(self) -> None:
        one, two = await stream_pair(max_size=32)
        with pytest.raises(ValueError, match="exceeds max_size"):
            await one.write(control_frame(data={"blob": "x" * 100}))
        await one.close()
        await two.close()

    async def test_envelope_missing_method_raises(self) -> None:
        one, two = await stream_pair()
        info = json.dumps({"id": "x", "path": "/foo"}).encode("utf-8")
        one.writer.write(struct.pack("!4sBII", b"GNRF", 1, len(info), 0) + info)
        await one.writer.drain()
        with pytest.raises(ValueError, match="missing 'method'"):
            await two.read()
        await one.close()
        await two.close()

    async def test_envelope_non_dict_payload_raises(self) -> None:
        one, two = await stream_pair()
        info = json.dumps(["a", "b"]).encode("utf-8")
        one.writer.write(struct.pack("!4sBII", b"GNRF", 1, len(info), 0) + info)
        await one.writer.drain()
        with pytest.raises(ValueError, match="must be a JSON object"):
            await two.read()
        await one.close()
        await two.close()

    def test_payload_is_opaque_and_info_is_separate(self) -> None:
        frame = Frame(info={"format": "application/x-test"}, payload=b"\x00\xffnot-json")
        received = FrameCodec().get_frame(frame.encode())
        assert received.info == {"format": "application/x-test"}
        assert received.payload == b"\x00\xffnot-json"

    def test_reserved_info_keys_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="reserved keys"):
            Frame(info={"method": "EVENT"})

    def test_explicit_empty_id_and_unknown_method_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="frame id"):
            Frame(id="")
        with pytest.raises(ValueError, match="unsupported frame method"):
            Frame(method="UNKNOWN")

    def test_nested_info_cannot_be_mutated_after_validation(self) -> None:
        source = {"route": {"parts": ["one"]}}
        frame = Frame(info=source)
        source["route"]["parts"].append(float("nan"))
        exposed = frame.info
        exposed["route"]["parts"].append("two")
        assert FrameCodec().get_frame(frame.encode()).info == {"route": {"parts": ["one"]}}

    def test_duplicate_info_keys_are_rejected(self) -> None:
        info = b'{"id":"x","method":"POST","path":"/","path":"/again"}'
        wire = struct.pack("!4sBII", b"GNRF", 1, len(info), 0) + info
        with pytest.raises(ValueError, match="duplicate JSON key"):
            FrameCodec().get_frame(wire)

    def test_unknown_version_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported frame version"):
            FrameCodec().get_header_lengths(struct.pack("!4sBII", b"GNRF", 2, 0, 0))

    def test_control_payload_is_explicit_and_strict(self) -> None:
        codec = ControlPayload()
        assert codec.decode(codec.encode({"value": None})) == {"value": None}
        with pytest.raises(ValueError, match="non-finite"):
            codec.decode(b'{"value":NaN}')

    async def test_concurrent_writes_remain_complete_frames(self) -> None:
        one, two = await stream_pair()
        frames = [Frame(path=f"/{index}", payload=bytes([index])) for index in range(100)]
        writes = asyncio.gather(*(one.write(frame) for frame in frames))
        received = [await two.read() for _ in frames]
        await writes
        assert {(frame.path, frame.payload) for frame in received} == {
            (frame.path, frame.payload) for frame in frames
        }
        await one.close()
        await two.close()


class TestChannelClient:
    async def test_connect_presents_register_frame(self, hub, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "child_01")
        await client.connect()
        assert client.connected is True
        await hub.wait_frames(1)
        register = hub.frames[0]
        assert register.method == REGISTER_METHOD
        assert register.path == REGISTER_PATH
        assert data_of(register) == {"name": "child_01", "pid": os.getpid()}
        await client.close()
        assert client.connected is False
        assert client.closed is True

    async def test_connect_twice_is_refused(self, hub, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "child_01")
        await client.connect()
        with pytest.raises(RuntimeError, match="already connected"):
            await client.connect()
        await client.close()

    async def test_reconnect_after_link_loss_keeps_the_new_generation(self, hub, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "child_01")
        await client.connect()
        await hub.wait_frames(1)
        await hub.streams[0].close()
        await asyncio.wait_for(client.wait_closed(), timeout=5)

        await client.connect()
        await hub.wait_frames(2)
        await asyncio.sleep(0)
        assert client.connected is True
        await client.send(path="/new-generation", data={"ok": True})
        await hub.wait_frames(3)
        assert hub.frames[-1].path == "/new-generation"
        await client.close()

    async def test_send_relays_frames_to_the_hub(self, hub, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "child_01")
        await client.connect()
        frame_id = await client.send(path="/events/ready", data={"n": 1})
        await hub.wait_frames(2)
        event = hub.frames[1]
        assert event.id == frame_id
        assert event.method == "POST"
        assert event.path == "/events/ready"
        assert data_of(event) == {"n": 1}
        await client.close()

    async def test_send_before_connect_raises(self, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "child_01")
        with pytest.raises(ConnectionError, match="not connected"):
            await client.send(path="/events/ready")

    async def test_hub_eof_orphans_the_client(self, hub, hub_path) -> None:
        orphaned: list[ChannelClient] = []
        client = ChannelClient(f"uds:{hub_path}", "child_01", on_orphan=orphaned.append)
        await client.connect()
        await hub.wait_frames(1)
        await hub.stop()  # the hub side goes away: EOF is the death signal
        await asyncio.wait_for(client.wait_closed(), timeout=5)
        assert client.connected is False
        assert client.closed is True
        assert orphaned == [client]
        await client.close()  # a deliberate close after orphan stays safe

    async def test_protocol_violation_is_a_clean_death(self, hub, hub_path, caplog) -> None:
        orphaned: list[ChannelClient] = []
        client = ChannelClient(f"uds:{hub_path}", "child_01", on_orphan=orphaned.append)
        await client.connect()
        await hub.wait_frames(1)
        bogus = b"BOGUS: not a wsx envelope"  # valid length prefix, invalid payload
        hub.streams[0].writer.write(len(bogus).to_bytes(4, "big") + bogus)
        await hub.streams[0].writer.drain()
        await asyncio.wait_for(client.wait_closed(), timeout=5)
        assert client.connected is False
        assert client.closed is True
        assert orphaned == [client]
        # the ValueError was caught and logged: the loop task ends clean,
        # nothing stays unretrieved
        assert any("Protocol violation" in record.getMessage() for record in caplog.records)
        # the client closed its writer on the way out: the hub reads EOF
        await hub.wait_eofs(1)

    async def test_malformed_envelope_is_a_clean_death(self, hub, hub_path, caplog) -> None:
        orphaned: list[ChannelClient] = []
        client = ChannelClient(f"uds:{hub_path}", "child_01", on_orphan=orphaned.append)
        await client.connect()
        await hub.wait_frames(1)
        payload = b"WSX://" + json.dumps({"id": "x", "path": "/foo"}).encode("utf-8")
        hub.streams[0].writer.write(len(payload).to_bytes(4, "big") + payload)
        await hub.streams[0].writer.drain()
        await asyncio.wait_for(client.wait_closed(), timeout=5)
        assert client.connected is False
        assert client.closed is True
        assert orphaned == [client]
        # the ValueError was caught and logged: the loop task ends clean,
        # nothing stays unretrieved
        assert any("Protocol violation" in record.getMessage() for record in caplog.records)
        # the client closed its writer on the way out: the hub reads EOF
        await hub.wait_eofs(1)

    async def test_deliberate_close_fires_no_orphan(self, hub, hub_path) -> None:
        orphaned: list[ChannelClient] = []
        client = ChannelClient(f"uds:{hub_path}", "child_01", on_orphan=orphaned.append)
        await client.connect()
        await client.close()
        assert orphaned == []
        assert client.closed is True

    async def test_connect_retries_until_the_hub_binds(self, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "late_child")
        task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.15)  # a few retry rounds before the hub exists
        late = FakeHub(hub_path)
        await late.start()
        await asyncio.wait_for(task, timeout=5)
        assert client.connected is True
        await late.wait_frames(1)
        assert late.frames[0].method == REGISTER_METHOD
        await client.close()
        await late.stop()

    async def test_connect_timeout_raises_connection_error(self, hub_path) -> None:
        client = ChannelClient(f"uds:{hub_path}", "child_01", connect_timeout=0.2)
        with pytest.raises(ConnectionError, match="not reachable"):
            await client.connect()

    def test_invalid_addresses_raise(self) -> None:
        with pytest.raises(ValueError, match="invalid channel address"):
            ChannelClient("bogus:/x", "child_01")
        with pytest.raises(ValueError, match="invalid channel address"):
            ChannelClient("uds:", "child_01")
        with pytest.raises(ValueError, match="invalid tcp address"):
            ChannelClient("tcp:127.0.0.1", "child_01")


class TestCommunicationMixin:
    def test_plain_base_server_lacks_the_attributes(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        assert hasattr(server, "parent_channel") is False
        assert hasattr(server, "children_channel") is False

    def test_unarmed_parent_channel_raises(self) -> None:
        server = ChannelServer(applications=[BaseApplication(mount="")])
        assert server.parent_armed is False
        with pytest.raises(RuntimeError, match="not armed"):
            server.parent_channel

    def test_children_channel_is_unarmed_in_the_minimal_package(self, hub_path) -> None:
        server = ChannelServer(applications=[BaseApplication(mount="")], parent=f"uds:{hub_path}")
        with pytest.raises(RuntimeError, match="not armed"):
            server.children_channel

    def test_armed_parent_channel_is_a_channel_client(self, hub_path) -> None:
        server = ChannelServer(applications=[BaseApplication(mount="")], parent=f"uds:{hub_path}")
        assert server.parent_armed is True
        assert isinstance(server.parent_channel, ChannelClient)
        assert server.parent_channel.address == f"uds:{hub_path}"

    def test_cooperative_chain_names_leftover_kwargs(self, hub_path) -> None:
        with pytest.raises(TypeError, match="bogus"):
            ChannelServer(
                applications=[BaseApplication(mount="")], parent=f"uds:{hub_path}", bogus=1
            )

    async def test_armed_parent_connects_at_startup_disconnects_at_shutdown(
        self, hub, hub_path
    ) -> None:
        events: list[str] = []
        server = ChannelServer(
            applications=[RecordingApp(mount="", events=events)], parent=f"uds:{hub_path}"
        )
        gate = asyncio.Event()
        queue = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            message = queue.pop(0)
            if message["type"] == "lifespan.shutdown":
                await gate.wait()  # hold the running server between startup and shutdown
            return message

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        task = asyncio.create_task(server({"type": "lifespan"}, receive, send))
        await hub.wait_frames(1)  # REGISTER reached the hub while the server runs
        register = hub.frames[0]
        assert register.method == REGISTER_METHOD
        assert data_of(register)["name"] == server.parent_channel.name
        assert data_of(register)["pid"] == os.getpid()
        assert server.parent_channel.connected is True
        gate.set()
        await asyncio.wait_for(task, timeout=5)
        assert server.parent_channel.connected is False
        assert server.parent_channel.closed is True
        assert {"type": "lifespan.startup.complete"} in sent
        assert {"type": "lifespan.shutdown.complete"} in sent
        assert events == ["on_startup", "on_shutdown"]  # app hooks ran normally

    async def test_unreachable_hub_fails_startup_and_no_hook_runs(self, hub_path) -> None:
        # hub_path exists but nothing is bound there: connect retries then fails
        events: list[str] = []
        server = ChannelServer(
            applications=[RecordingApp(mount="", events=events)], parent=f"uds:{hub_path}"
        )
        server.parent_channel.connect_timeout = 0.2
        queue = [{"type": "lifespan.startup"}]
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return queue.pop(0)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        with pytest.raises(ConnectionError, match="not reachable"):
            await server({"type": "lifespan"}, receive, send)

        assert len(sent) == 1
        assert sent[0]["type"] == "lifespan.startup.failed"
        assert "not reachable" in str(sent[0]["message"])
        assert events == []  # the child died before any app hook ran

    async def test_unarmed_composition_passes_lifespan_straight_through(self) -> None:
        server = ChannelServer(applications=[BaseApplication(mount="")])
        queue = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return queue.pop(0)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await server({"type": "lifespan"}, receive, send)
        assert {"type": "lifespan.startup.complete"} in sent
        assert {"type": "lifespan.shutdown.complete"} in sent
