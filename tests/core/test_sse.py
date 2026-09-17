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

"""Tests for SseStream (core 1e Phase 5): text/event-stream framing.

Real objects, no mocks: frame single events directly, iterate a real async
source into wire bytes, exercise the heartbeat on a genuinely silent source
(a small ``keepalive_seconds``, no clock faking), and confirm the
``StreamingResponse`` wrapper carries the SSE headers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable
from typing import Any

from kajenn.sse import KEEPALIVE_SECONDS, SseStream
from kajenn.streaming import StreamingResponse
from kajenn.types import Message, Scope


async def source(*events: dict[str, Any]) -> AsyncIterable[dict[str, Any]]:
    """An async iterator over the given event dicts."""
    for event in events:
        yield event


class TestFrame:
    """One event dict -> one SSE record (ends with a blank line)."""

    def test_data_only(self) -> None:
        frame = SseStream(source()).frame({"data": "hello"})
        assert frame == b"data: hello\n\n"

    def test_id_and_event(self) -> None:
        frame = SseStream(source()).frame({"id": "7", "event": "progress", "data": "x"})
        assert frame == b"id: 7\nevent: progress\ndata: x\n\n"

    def test_non_string_data_is_json(self) -> None:
        frame = SseStream(source()).frame({"data": {"pct": 50}})
        assert frame == b'data: {"pct": 50}\n\n'

    def test_multiline_data_split(self) -> None:
        frame = SseStream(source()).frame({"data": "line1\nline2"})
        assert frame == b"data: line1\ndata: line2\n\n"

    def test_id_omitted_when_absent(self) -> None:
        frame = SseStream(source()).frame({"event": "ping", "data": "1"})
        assert b"id:" not in frame


class TestIteration:
    """Iterating the stream yields wire bytes for each event."""

    async def test_events_framed_in_order(self) -> None:
        stream = SseStream(source({"data": "a"}, {"data": "b"}))
        chunks = [chunk async for chunk in stream]
        assert chunks == [b"data: a\n\n", b"data: b\n\n"]

    async def test_retry_emitted_once_at_start(self) -> None:
        stream = SseStream(source({"data": "a"}), retry_ms=5000)
        chunks = [chunk async for chunk in stream]
        assert chunks[0] == b"retry: 5000\n\n"
        assert chunks[1] == b"data: a\n\n"
        assert sum(c.startswith(b"retry:") for c in chunks) == 1

    async def test_exhausted_source_ends_stream(self) -> None:
        stream = SseStream(source())
        chunks = [chunk async for chunk in stream]
        assert chunks == []


class TestHeartbeat:
    """A silent source past keepalive_seconds emits ``: keepalive`` comments."""

    async def test_keepalive_while_silent_then_event(self) -> None:
        async def slow() -> AsyncIterable[dict[str, Any]]:
            await asyncio.sleep(0.12)          # silent longer than keepalive
            yield {"data": "late"}

        stream = SseStream(slow(), keepalive_seconds=0.04)
        chunks = [chunk async for chunk in stream]
        keepalives = [c for c in chunks if c == b": keepalive\n\n"]
        assert len(keepalives) >= 2            # at least two idle intervals
        assert chunks[-1] == b"data: late\n\n"  # the real event still arrives

    async def test_no_keepalive_when_source_is_prompt(self) -> None:
        stream = SseStream(source({"data": "a"}), keepalive_seconds=1.0)
        chunks = [chunk async for chunk in stream]
        assert b": keepalive\n\n" not in chunks

    def test_default_keepalive_interval(self) -> None:
        assert SseStream(source()).keepalive_seconds == KEEPALIVE_SECONDS

    async def test_a_source_that_ends_after_a_keepalive_is_quiet(self, caplog) -> None:
        # A source that ends BY ITSELF past a keepalive — what a server leaving
        # RUNNING now does — must not leave an unretrieved exception behind.
        async def slow_then_done() -> AsyncIterable[dict[str, Any]]:
            await asyncio.sleep(0.06)
            return
            yield {"data": "never"}                      # pragma: no cover

        stream = SseStream(slow_then_done(), keepalive_seconds=0.02)
        with caplog.at_level(logging.ERROR):
            chunks = [chunk async for chunk in stream]
            await asyncio.sleep(0.05)

        assert chunks == [b": keepalive\n\n"] * len(chunks)
        assert caplog.text == ""


class TestConsumerGone:
    """Cancelling the consumer closes the source (its ``finally`` must run)."""

    async def test_source_finalized_on_cancel(self) -> None:
        closed = asyncio.Event()

        async def endless() -> AsyncIterable[dict[str, Any]]:
            try:
                yield {"data": "first"}
                await asyncio.Event().wait()          # blocks forever
                yield {"data": "never"}
            finally:
                closed.set()                          # the unsubscribe seam

        async def consume() -> None:
            async for _ in SseStream(endless(), keepalive_seconds=60.0):
                task_started.set()

        task_started = asyncio.Event()
        task = asyncio.get_running_loop().create_task(consume())
        await task_started.wait()                     # first frame arrived
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert closed.is_set()                        # finally ran, no leaked read


class TestResponseWrapper:
    """``response()`` wraps the stream in a StreamingResponse with SSE headers."""

    async def test_sse_headers_set(self) -> None:
        stream = SseStream(source({"data": "a"}))
        response = stream.response()
        assert isinstance(response, StreamingResponse)

        scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        await response(scope, receive, send)
        headers = dict(sent[0]["headers"])
        # text/* gets the charset appended, uniform with Response (SSE is UTF-8)
        assert headers[b"content-type"] == b"text/event-stream; charset=utf-8"
        assert headers[b"cache-control"] == b"no-cache"
        assert headers[b"connection"] == b"keep-alive"
        # the event and the terminal body come through the stream transport
        assert any(m.get("body") == b"data: a\n\n" for m in sent)
        assert sent[-1]["more_body"] is False
