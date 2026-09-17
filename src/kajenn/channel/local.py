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

"""Local channel — the in-process wire, byte-identical to the socket one.

The single role (design §3.5a) runs commander and worker in ONE process, and
it must speak the very same protocol as a spawned child: not "the same API",
the same *bytes*. ``LocalChannel`` is therefore two ``asyncio.Queue``s of
encoded frames — every envelope crosses through ``Frame.encode()`` and is
re-parsed on the other side with the same versioned info/bytes
rules ``FrameStream.read`` applies. A payload dict mutated after ``send()``
cannot reach the peer, exactly as over a socket.

``LocalFrameStream`` is the codec twin of ``FrameStream`` (the only module
above the frame protocol allowed to touch bytes): ``read()`` returns ``None``
at EOF, an oversized or malformed frame raises ``ValueError``. A queue sentinel
models EOF in both directions, so closing either end has the socket meaning —
the peer's read ends and the link-loss callback runs.

``LocalChannel`` itself IS the member face, with the ``ChannelClient`` API
(``connect``/``send``/``close``/``wait_closed``, ``on_message``/``on_orphan``,
``connected``), plus ``send_frame(frame)`` for the frames whose id is not the
sender's to mint — a REPLY reuses the CALL's id. The hub side is consumed by
``ChannelHub.attach_local()``, which registers it through the same REGISTER
path as any socket member: one rubric, no parallel bookkeeping.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Callable

from .frame import (
    REGISTER_METHOD,
    REGISTER_PATH,
    Frame,
    FrameCodec,
)
from .control import ControlPayload

__all__ = ["LocalChannel", "LocalFrameStream"]


class LocalFrameStream:
    """Frame codec over a pair of byte queues — the in-process ``FrameStream``.

    Reads from ``inbound``, writes to ``outbound``; a ``None`` in a queue is
    the EOF sentinel. ``close()`` sends it to the peer and unparks its own
    reader, so both sides observe the end of the channel.
    """

    def __init__(
        self,
        inbound: asyncio.Queue[bytes | None],
        outbound: asyncio.Queue[bytes | None],
        *,
        max_size: int | None = None,
        max_queue_size: int = 16,
    ) -> None:
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        self.inbound = inbound
        self.outbound = outbound
        self.max_queue_size = max_queue_size
        self.codec = FrameCodec(max_size=max_size)
        self.max_size = self.codec.max_size
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this end has been closed."""
        return self._closed

    async def read(self) -> Frame | None:
        """The next frame, or ``None`` when the channel ended."""
        wire = await self.inbound.get()
        if wire is None:
            return None
        return self.codec.get_frame(wire)

    async def write(self, frame: Frame) -> None:
        """Encode and enqueue one frame; a full or closed end raises."""
        if self.outbound.qsize() >= self.max_queue_size:
            raise ConnectionError("local channel queue full; frame not sent")
        wire = self.codec.encode(frame)
        if self._closed:
            raise BrokenPipeError("local channel end is closed")
        await self.outbound.put(wire)

    async def close(self) -> None:
        """Close this end: EOF to the peer, EOF to our own parked reader."""
        if self._closed:
            return
        self._closed = True
        await self.outbound.put(None)
        await self.inbound.put(None)


class LocalChannel:
    """In-process channel endpoint: the member face of a queue-backed wire.

    Built by whoever owns the in-process worker, then handed to
    ``ChannelHub.attach_local()``; ``connect()`` presents the REGISTER frame
    just like ``ChannelClient`` does, and the queues buffer it whichever side
    goes first.
    """

    def __init__(
        self,
        name: str,
        *,
        on_message: Callable[..., Any] | None = None,
        on_orphan: Callable[..., Any] | None = None,
        max_size: int | None = None,
        max_queue_size: int = 16,
    ) -> None:
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        self.name = name
        self.on_message = on_message
        self.on_orphan = on_orphan
        self.max_size = max_size
        self.address = "local:"
        to_hub: asyncio.Queue[bytes | None] = asyncio.Queue()
        to_member: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._member_stream = LocalFrameStream(
            to_member,
            to_hub,
            max_size=max_size,
            max_queue_size=max_queue_size,
        )
        self._hub_stream = LocalFrameStream(
            to_hub,
            to_member,
            max_size=max_size,
            max_queue_size=max_queue_size,
        )
        self._logger = logging.getLogger(__name__)
        self._receive_task: asyncio.Task[None] | None = None
        self._connected = False
        self._closing = False
        self._closed_event = asyncio.Event()

    @property
    def hub_stream(self) -> LocalFrameStream:
        """The hub-side end, consumed by ``ChannelHub.attach_local()``."""
        return self._hub_stream

    @property
    def connected(self) -> bool:
        """Whether the channel is up (REGISTER sent, receive loop running)."""
        return self._connected

    @property
    def closed(self) -> bool:
        """Whether the channel ended (either side; ``False`` before connect)."""
        return self._closed_event.is_set()

    async def connect(self) -> None:
        """Present the REGISTER frame and start the receive loop."""
        if self.connected:
            raise RuntimeError("local channel is already connected")
        register = Frame(
            method=REGISTER_METHOD,
            path=REGISTER_PATH,
            payload=ControlPayload().encode({"name": self.name, "pid": os.getpid()}),
        )
        await self._member_stream.write(register)
        self._connected = True
        self._closed_event.clear()
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._logger.info("Local channel connected as %s", self.name)

    async def close(self) -> None:
        """Deliberate local close: no orphan signal."""
        self._closing = True
        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        await self._member_stream.close()
        self._connected = False
        self._closed_event.set()

    async def wait_closed(self) -> None:
        """Block until the channel ends (either side); the member's main wait."""
        await self._closed_event.wait()

    async def send(self, *, method: str = "POST", path: str = "/", data: Any = None) -> str:
        """Send one frame to the hub (fire-and-forget); returns the frame id."""
        return await self.send_frame(
            Frame(method=method, path=path, payload=ControlPayload().encode(data))
        )

    async def send_frame(self, frame: Frame) -> str:
        """Send an already-built frame — a REPLY reuses the CALL's id."""
        if not self.connected:
            raise ConnectionError("not connected")
        await self._member_stream.write(frame)
        return frame.id

    async def _receive_loop(self) -> None:
        """Read frames until the channel ends; hub gone → orphan."""
        try:
            while True:
                try:
                    frame = await self._member_stream.read()
                except ValueError:
                    self._logger.exception("Protocol violation from the hub; closing the channel")
                    break
                if frame is None:
                    break
                await self._fire(self.on_message, frame)
        except asyncio.CancelledError:
            return
        finally:
            self._connected = False
            await self._member_stream.close()
            self._closed_event.set()
            if not self._closing:
                self._logger.info("Hub side gone: %s is orphan", self.name)
                await self._fire(self.on_orphan, self)

    async def _fire(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        """Run a sync-or-async callback; a consumer bug must not sever the channel."""
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            self._logger.exception("Channel callback %r failed", callback)
