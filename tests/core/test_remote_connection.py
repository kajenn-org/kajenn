"""Loopback contract tests for bounded, generation-owned remote calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from kajenn.channel.frame import Frame, FrameStream
from kajenn.remote_connection import (
    RemoteCallCancelled,
    RemoteCallFailed,
    RemoteConnection,
)


@dataclass
class Received:
    generation: int
    frame: Frame
    stream: FrameStream


class LoopbackPeer:
    def __init__(self) -> None:
        self.received: asyncio.Queue[Received] = asyncio.Queue()
        self.connections = 0
        self.streams: list[FrameStream] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)

    @property
    def address(self) -> str:
        port = self.server.sockets[0].getsockname()[1]
        return f"tcp:127.0.0.1:{port}"

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        generation = self.connections
        stream = FrameStream(reader, writer)
        self.streams.append(stream)
        while (frame := await stream.read()) is not None:
            await self.received.put(Received(generation, frame, stream))

    async def reply(self, received: Received, payload: bytes = b"ok") -> None:
        await received.stream.write(
            Frame(
                id=received.frame.id,
                method="REPLY",
                path=received.frame.path,
                payload=payload,
            )
        )

    async def close(self) -> None:
        for stream in self.streams:
            await stream.close()
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def peer():
    peer = LoopbackPeer()
    await peer.start()
    try:
        yield peer
    finally:
        await peer.close()


def call_frame(id: str, path: str = "/work", payload: bytes = b"request") -> Frame:
    return Frame(id=id, method="CALL", path=path, payload=payload)


async def test_call_requires_call_method_without_connecting() -> None:
    connection = RemoteConnection("tcp:127.0.0.1:1", timeout=0.1)
    with pytest.raises(ValueError, match="require method CALL"):
        await connection.call(Frame(method="EVENT"))


async def test_duplicate_inflight_id_is_rejected_and_never_replayed(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=5)
    first = asyncio.create_task(connection.call(call_frame("same")))
    received = await asyncio.wait_for(peer.received.get(), 2)
    with pytest.raises(ValueError, match="duplicate in-flight correlation id"):
        await connection.call(call_frame("same", path="/other"))
    first.cancel()
    with pytest.raises(RemoteCallCancelled) as cancelled:
        await first
    assert cancelled.value.outcome == "unknown"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(peer.received.get(), 0.05)
    assert received.frame.path == "/work"
    await connection.close()


async def test_capacity_wait_is_bounded_and_cancelled_call_is_not_sent(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=5, max_calls=1)
    first = asyncio.create_task(connection.call(call_frame("first")))
    received_first = await asyncio.wait_for(peer.received.get(), 2)
    second = asyncio.create_task(connection.call(call_frame("second")))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(peer.received.get(), 0.05)
    second.cancel()
    with pytest.raises(RemoteCallCancelled) as cancelled:
        await second
    assert cancelled.value.outcome == "not_sent"
    await peer.reply(received_first)
    assert (await first).payload == b"ok"
    await connection.close()


async def test_capacity_releases_only_after_the_first_call_finishes(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=5, max_calls=1)
    first = asyncio.create_task(connection.call(call_frame("first")))
    received_first = await asyncio.wait_for(peer.received.get(), 2)
    second = asyncio.create_task(connection.call(call_frame("second")))
    await peer.reply(received_first, b"one")
    assert (await first).payload == b"one"
    received_second = await asyncio.wait_for(peer.received.get(), 2)
    assert received_second.frame.id == "second"
    await peer.reply(received_second, b"two")
    assert (await second).payload == b"two"
    await connection.close()


async def test_link_loss_is_unknown_not_replayed_and_next_call_uses_new_generation(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=5)
    first = asyncio.create_task(connection.call(call_frame("reused")))
    old = await asyncio.wait_for(peer.received.get(), 2)
    await old.stream.close()
    with pytest.raises(RemoteCallFailed) as failed:
        await first
    assert failed.value.outcome == "unknown"

    second = asyncio.create_task(connection.call(call_frame("reused", payload=b"new")))
    new = await asyncio.wait_for(peer.received.get(), 2)
    assert new.generation == old.generation + 1
    assert new.frame.payload == b"new"
    await peer.reply(new, b"new-reply")
    assert (await second).payload == b"new-reply"
    assert peer.connections == 2
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(peer.received.get(), 0.05)
    await connection.close()


async def test_timeout_after_send_reports_unknown_and_does_not_replay(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=0.05)
    call = asyncio.create_task(connection.call(call_frame("slow")))
    received = await asyncio.wait_for(peer.received.get(), 2)
    with pytest.raises(RemoteCallFailed) as failed:
        await call
    assert failed.value.outcome == "unknown"
    assert received.frame.id == "slow"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(peer.received.get(), 0.05)
    await connection.close()


async def test_timed_out_id_is_reserved_until_its_late_reply_is_consumed(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=0.05)
    first = asyncio.create_task(connection.call(call_frame("reserved")))
    received = await asyncio.wait_for(peer.received.get(), 2)
    with pytest.raises(RemoteCallFailed):
        await first

    with pytest.raises(ValueError, match="duplicate in-flight correlation id"):
        await connection.call(call_frame("reserved", payload=b"must-not-send"))
    await peer.reply(received, b"late")
    for _ in range(100):
        if "reserved" not in connection._abandoned:
            break
        await asyncio.sleep(0.01)
    assert "reserved" not in connection._abandoned

    reused = asyncio.create_task(connection.call(call_frame("reserved", payload=b"fresh")))
    fresh = await asyncio.wait_for(peer.received.get(), 2)
    assert fresh.frame.payload == b"fresh"
    await peer.reply(fresh, b"fresh-reply")
    assert (await reused).payload == b"fresh-reply"
    await connection.close()


async def test_wrong_route_reply_closes_generation_and_fails_parked_call(peer) -> None:
    connection = RemoteConnection(peer.address, timeout=5)
    call = asyncio.create_task(connection.call(call_frame("owned", path="/right")))
    received = await asyncio.wait_for(peer.received.get(), 2)
    await received.stream.write(
        Frame(id="owned", method="REPLY", path="/wrong", payload=b"misrouted")
    )
    with pytest.raises(RemoteCallFailed, match="does not belong") as failed:
        await call
    assert failed.value.outcome == "unknown"

    replacement = asyncio.create_task(connection.call(call_frame("owned", path="/right")))
    fresh = await asyncio.wait_for(peer.received.get(), 2)
    assert fresh.generation == received.generation + 1
    await peer.reply(fresh)
    await replacement
    await connection.close()


async def test_oversized_send_preserves_other_calls_and_connection(peer, monkeypatch):
    from kajenn.transport_limits import FrameTooLarge

    monkeypatch.setenv("KAJENN_FRAME_MAX_BYTES", "1024")
    connection = RemoteConnection(peer.address, timeout=3)
    try:
        first = asyncio.create_task(connection.call(call_frame("first")))
        received = await asyncio.wait_for(peer.received.get(), 1)
        with pytest.raises(FrameTooLarge):
            await connection.call(call_frame("large", payload=b"x" * 2048))
        assert "large" not in connection._pending
        assert "large" not in connection._abandoned
        await peer.reply(received)
        assert (await first).payload == b"ok"
        after = asyncio.create_task(connection.call(call_frame("after")))
        received_after = await asyncio.wait_for(peer.received.get(), 1)
        assert received_after.generation == received.generation
        await peer.reply(received_after)
        assert (await after).payload == b"ok"
        assert peer.received.empty()
    finally:
        await connection.close()
