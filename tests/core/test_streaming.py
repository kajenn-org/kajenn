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

"""Tests for StreamingResponse (core 1e Phase 5): the chunked ASGI sibling.

Real objects, no mocks: drive the response through its ``__call__`` with a
recording ``send`` and assert the ASGI message sequence — one ``start``, one
``body`` per chunk with ``more_body=True``, a terminal empty body with
``more_body=False``. A regression check confirms ``Response`` is untouched (it
stays buffered, two messages, no ``more_body``).
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from kajenn.response import Response
from kajenn.streaming import StreamingResponse
from kajenn.types import Message, Scope


async def drive(app: Any) -> list[Message]:
    """Run an ASGI app once and return the recorded ``send`` messages."""
    scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def gen(*chunks: bytes) -> AsyncIterable[bytes]:
    """An async iterator over the given byte chunks."""
    for chunk in chunks:
        yield chunk


class TestMessageSequence:
    """start once, one body per chunk (more_body=True), terminal more_body=False."""

    async def test_start_then_chunks_then_terminal(self) -> None:
        sent = await drive(StreamingResponse(gen(b"a", b"b", b"c")))
        assert sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == 200
        bodies = [m for m in sent[1:]]
        assert [m["body"] for m in bodies] == [b"a", b"b", b"c", b""]
        assert [m["more_body"] for m in bodies] == [True, True, True, False]

    async def test_empty_iterator_still_terminates(self) -> None:
        sent = await drive(StreamingResponse(gen()))
        assert sent[0]["type"] == "http.response.start"
        assert len(sent) == 2                       # start + terminal only
        assert sent[1]["body"] == b"" and sent[1]["more_body"] is False

    async def test_only_one_start(self) -> None:
        sent = await drive(StreamingResponse(gen(b"x", b"y")))
        assert sum(1 for m in sent if m["type"] == "http.response.start") == 1


class TestHeaders:
    """media_type -> content-type; explicit headers preserved; text gets charset."""

    async def test_media_type_becomes_content_type(self) -> None:
        sent = await drive(StreamingResponse(gen(b"x"), media_type="application/octet-stream"))
        headers = dict(sent[0]["headers"])
        assert headers[b"content-type"] == b"application/octet-stream"

    async def test_text_media_type_gets_charset(self) -> None:
        sent = await drive(StreamingResponse(gen(b"x"), media_type="text/plain"))
        headers = dict(sent[0]["headers"])
        assert headers[b"content-type"] == b"text/plain; charset=utf-8"

    async def test_explicit_headers_preserved(self) -> None:
        resp = StreamingResponse(gen(b"x"), headers=[("x-custom", "v")], media_type="text/plain")
        resp.set_header("x-late", "w")
        sent = await drive(resp)
        headers = dict(sent[0]["headers"])
        assert headers[b"x-custom"] == b"v" and headers[b"x-late"] == b"w"

    async def test_custom_status(self) -> None:
        sent = await drive(StreamingResponse(gen(b"x"), status_code=206))
        assert sent[0]["status"] == 206


class TestResponseUntouched:
    """Response stays buffered: two messages, no more_body (the whole point)."""

    async def test_response_two_messages_no_more_body(self) -> None:
        sent = await drive(Response(content="hi", media_type="text/plain"))
        assert len(sent) == 2
        assert sent[1]["type"] == "http.response.body" and sent[1]["body"] == b"hi"
        assert "more_body" not in sent[1]

    def test_response_has_no_streaming_attrs(self) -> None:
        assert not hasattr(Response, "body_iterator")
        assert not hasattr(StreamingResponse, "set_result")
