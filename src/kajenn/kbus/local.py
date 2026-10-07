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

"""LocalKBus — the in-process wire, byte-identical to the socket one.

Both ends live in ONE process and speak the very same protocol as a child in
another process: not "the same API", the same *bytes*. ``LocalKBus`` is
therefore two ``asyncio.Queue``s of encoded frames — every envelope crosses
through ``Frame.encode()`` and is re-parsed on the other side with the same
versioned info/bytes rules ``FrameStream.read`` applies. A payload dict
mutated after ``post()`` cannot reach the peer, exactly as over a socket.

``LocalFrameStream`` is the codec twin of ``FrameStream``: ``read()`` returns
``None`` at EOF, an oversized or malformed frame raises ``ValueError``. A queue sentinel
models EOF in both directions, so closing either end has the socket meaning —
the peer's read ends and the link-loss callback runs. The queues are bounded:
a full queue makes ``write`` wait for the peer to read (backpressure), the way
a socket buffer does, and no frame is dropped.

``LocalKBus`` itself IS the member face, with the ``KBusClient`` API. The hub
side is consumed by ``KBusHub.attach_local()``, which registers it through the
same REGISTER path as any socket member: one rubric, no parallel bookkeeping.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .client import KBusEnd
from .frame import Frame, FrameCodec

__all__ = ["LocalFrameStream", "LocalKBus"]


class LocalFrameStream:
    """Frame codec over a pair of byte queues — the in-process ``FrameStream``.

    Reads from ``inbound``, writes to ``outbound``; a ``None`` in a queue is
    the EOF sentinel. ``write`` waits while ``outbound`` is full;
    ``max_queue_size`` is the bound ``LocalKBus`` gives both queues.
    ``close()`` never waits: it drops the frames nobody will read from
    ``inbound``, unparks its own reader, and sends the sentinel to the peer
    behind the frames already queued, so both sides observe the end of the
    KajennBus. Two ends built over the same queues share ``ended``: closing
    either one releases a ``write`` parked on a full queue on both sides.
    """

    def __init__(
        self,
        inbound: asyncio.Queue[bytes | None],
        outbound: asyncio.Queue[bytes | None],
        *,
        max_size: int | None = None,
        max_queue_size: int = 16,
        ended: asyncio.Event | None = None,
    ) -> None:
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        self.inbound = inbound
        self.outbound = outbound
        self.max_queue_size = max_queue_size
        self.codec = FrameCodec(max_size=max_size)
        self.max_size = self.codec.max_size
        self._closed = False
        self._ended = ended if ended is not None else asyncio.Event()
        self._eof: asyncio.Task[None] | None = None

    @property
    def closed(self) -> bool:
        """Whether this end has been closed."""
        return self._closed

    async def read(self) -> Frame | None:
        """The next frame, or ``None`` when the KajennBus ended."""
        wire = await self.inbound.get()
        if wire is None:
            return None
        return self.codec.get_frame(wire)

    async def write(self, frame: Frame) -> None:
        """Encode and enqueue one frame, waiting while the queue is full.

        Raises:
            BrokenPipeError: this end is closed, or either end closes while
                the write waits for room.
        """
        wire = self.codec.encode(frame)
        if self._closed or self._ended.is_set():
            raise BrokenPipeError("local KajennBus end is closed")
        if not self.outbound.full():
            self.outbound.put_nowait(wire)
            return
        put = asyncio.ensure_future(self.outbound.put(wire))
        ended = asyncio.ensure_future(self._ended.wait())
        try:
            await asyncio.wait({put, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ended.cancel()
            if not put.done():
                put.cancel()
        if not put.done() or put.cancelled():
            raise BrokenPipeError("local KajennBus closed while the write waited")

    async def close(self) -> None:
        """Close this end: EOF to our own parked reader, EOF to the peer."""
        if self._closed:
            return
        self._closed = True
        self._ended.set()
        while not self.inbound.empty():
            self.inbound.get_nowait()
        self.inbound.put_nowait(None)
        if self.outbound.full():
            self._eof = asyncio.create_task(self.outbound.put(None))
        else:
            self.outbound.put_nowait(None)


class LocalKBus(KBusEnd):
    """In-process KajennBus end: the member face of a queue-backed wire.

    Built by whoever owns the in-process worker, then handed to
    ``KBusHub.attach_local()``, which registers and connects it in one call.
    """

    def __init__(self, name: str, *, max_queue_size: int = 16, **kwargs: Any) -> None:
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        super().__init__(name, **kwargs)
        self.address = "local:"
        to_hub: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_queue_size)
        to_member: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_queue_size)
        ended = asyncio.Event()
        self._member_stream = LocalFrameStream(
            to_member, to_hub, max_size=self.max_size, max_queue_size=max_queue_size,
            ended=ended,
        )
        self._hub_stream = LocalFrameStream(
            to_hub, to_member, max_size=self.max_size, max_queue_size=max_queue_size,
            ended=ended,
        )

    @property
    def hub_stream(self) -> LocalFrameStream:
        """The hub-side end, consumed by ``KBusHub.attach_local()``."""
        return self._hub_stream

    async def open_stream(self) -> LocalFrameStream:
        """The member-side end of the queues."""
        return self._member_stream
