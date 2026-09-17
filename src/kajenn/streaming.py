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

"""Streaming HTTP response: a chunked ASGI sibling of ``Response``.

``Response`` (response.py) is flat and BUFFERED — two ASGI messages, the whole
body in memory. A stream is a different shape, not a variant, so it is a
separate slotted class rather than a subclass: ``StreamingResponse`` sends the
``http.response.start`` once, then one ``http.response.body`` per chunk with
``more_body=True``, and a terminal empty body with ``more_body=False``. It has
NO ``set_result`` (the buffered type-dispatch is deliberately not carried over):
the body is an async iterator of ``bytes`` the caller supplies.

The iterator is the whole contract — a plain ``async for`` over user chunks, an
``SseStream`` (sse.py), or any bounded event source. This class only frames the
ASGI message sequence around it; backpressure and heartbeats live in the source.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Mapping

from .types import Receive, Scope, Send

__all__ = ["StreamingResponse"]

HeadersInput = Mapping[str, str] | list[tuple[str, str]] | None


class StreamingResponse:
    """Chunked HTTP response, usable directly as an ASGI application.

    Example:
        >>> async def chunks():
        ...     yield b"one"
        ...     yield b"two"
        >>> response = StreamingResponse(chunks(), media_type="text/plain")
        >>> await response(scope, receive, send)
    """

    __slots__ = ("body_iterator", "status_code", "media_type", "_headers")

    charset: str = "utf-8"

    def __init__(
        self,
        body_iterator: AsyncIterable[bytes],
        status_code: int = 200,
        headers: HeadersInput = None,
        media_type: str | None = None,
    ) -> None:
        """Build a streaming response over an async iterator of byte chunks."""
        self.body_iterator = body_iterator
        self.status_code = status_code
        self.media_type = media_type
        if headers is None:
            self._headers = []
        elif isinstance(headers, list):
            self._headers = list(headers)
        else:
            self._headers = list(headers.items())
        if media_type is not None:
            names = {name.lower() for name, _ in self._headers}
            if "content-type" not in names:
                self._headers.append(("content-type", self._content_type(media_type)))

    def _content_type(self, media_type: str) -> str:
        """Content-Type value, appending the charset for text types lacking one."""
        if media_type.startswith("text/") and "charset" not in media_type:
            return f"{media_type}; charset={self.charset}"
        return media_type

    def _build_headers(self) -> list[tuple[bytes, bytes]]:
        """ASGI headers: names lowercased, latin-1 encoded (HTTP standard)."""
        return [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in self._headers
        ]

    def set_header(self, name: str, value: str) -> None:
        """Append a response header (before the response is sent)."""
        self._headers.append((name, value))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI interface: ``http.response.start`` then one body per chunk.

        Each chunk goes out with ``more_body=True``; a terminal empty body with
        ``more_body=False`` closes the stream (never omitted, even when the
        iterator yields nothing).
        """
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self._build_headers(),
            }
        )
        async for chunk in self.body_iterator:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
