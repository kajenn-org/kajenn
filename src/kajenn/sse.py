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

"""Server-Sent Events framing over ``StreamingResponse``.

An event is a small dict — ``{"data": ..., "event": ..., "id": ...}`` — framed
into the ``text/event-stream`` wire format: ``id:``/``event:``/``data:`` lines
(``data`` split across lines on newlines, per the spec), ``retry:`` once at the
start when configured, and a ``: keepalive`` comment when the source falls
silent longer than the heartbeat interval (the comment keeps proxies from
closing an idle connection; the client ignores it). Each event ends with a
blank line. ``data`` that is not a string is JSON-encoded.

The framing is shaped like ``channel/frame.py`` (a slotted codec, its own wire
format) but has no bytes in common — SSE is a text protocol over HTTP, not the
length-prefixed channel frame. ``SseStream`` is SELF-CONTAINED: it wraps ANY
async source of event dicts (a user generator, a task hub subscription) and
yields wire ``bytes``; the source is the caller's concern. Resumability
(``Last-Event-ID`` → a snapshot baseline then the live source) is built by the
consumer that owns the event source, not here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from .streaming import StreamingResponse

__all__ = ["SseStream", "KEEPALIVE_SECONDS"]

KEEPALIVE_SECONDS = 15.0     # idle interval after which a ``: keepalive`` comment goes out
_KEEPALIVE_FRAME = b": keepalive\n\n"


class SseStream:
    """Frames an async source of event dicts into ``text/event-stream`` bytes.

    Note:
        The stream is bound to one source (dual relationship: ``self.source``).
        Iterating it yields wire bytes; ``response()`` wraps it in a
        ``StreamingResponse`` with the SSE headers already set.
    """

    __slots__ = ("source", "retry_ms", "keepalive_seconds")

    def __init__(
        self,
        source: AsyncIterable[dict[str, Any]],
        *,
        retry_ms: int | None = None,
        keepalive_seconds: float = KEEPALIVE_SECONDS,
    ) -> None:
        """Bind to an async source of event dicts.

        Args:
            source: Any async iterable of events; each event is a dict with an
                optional ``id``/``event`` and a ``data`` payload.
            retry_ms: When set, a ``retry:`` line is emitted once at the start
                (the client's reconnection delay).
            keepalive_seconds: Idle interval after which a ``: keepalive``
                comment is sent to hold the connection open.
        """
        self.source = source
        self.retry_ms = retry_ms
        self.keepalive_seconds = keepalive_seconds

    def frame(self, event: dict[str, Any]) -> bytes:
        """Encode one event dict into an SSE record (ends with a blank line)."""
        lines: list[str] = []
        if event.get("id") is not None:
            lines.append(f"id: {event['id']}")
        if event.get("event") is not None:
            lines.append(f"event: {event['event']}")
        data = event.get("data")
        text = data if isinstance(data, str) else json.dumps(data)
        for line in text.split("\n"):
            lines.append(f"data: {line}")
        return ("\n".join(lines) + "\n\n").encode("utf-8")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield wire bytes: an optional ``retry:``, then framed events.

        The source is consumed one event at a time; while it stays silent past
        ``keepalive_seconds`` a ``: keepalive`` comment is emitted so the
        connection is not reaped (the pending read is waited on, never
        cancelled, so the same read is still there at the next interval). The
        loop ends when the source is exhausted; when the CONSUMER goes away
        instead (cancellation / close), the pending read is cancelled and
        awaited so the source's ``finally`` runs before the stream unwinds
        (a subscription source must get to unsubscribe).
        """
        if self.retry_ms is not None:
            yield f"retry: {self.retry_ms}\n\n".encode()
        iterator = self.source.__aiter__()
        nxt: asyncio.Future[dict[str, Any]] | None = None
        try:
            while True:
                nxt = asyncio.ensure_future(iterator.__anext__())
                while not nxt.done():
                    await asyncio.wait({nxt}, timeout=self.keepalive_seconds)
                    if not nxt.done():
                        yield _KEEPALIVE_FRAME     # source silent: hold the connection
                try:
                    event = nxt.result()
                except StopAsyncIteration:
                    return                         # source exhausted: end the stream
                finally:
                    nxt = None
                yield self.frame(event)
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()                       # client gone: close the source
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await nxt

    def response(self, status_code: int = 200) -> StreamingResponse:
        """A ``StreamingResponse`` over this stream with the SSE headers set."""
        return StreamingResponse(
            self,
            status_code=status_code,
            headers=[
                ("cache-control", "no-cache"),
                ("connection", "keep-alive"),
            ],
            media_type="text/event-stream",
        )
