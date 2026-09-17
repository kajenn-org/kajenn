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

"""A neutral, bounded endpoint for calling a buffered ASGI application.

The endpoint presents one complete request message and buffers one complete
response.  It deliberately keeps transport routing and SPA state outside the
application call.  WSK response conversion happens here because this is the
endpoint at which the application that produced the media type is available.
"""

import asyncio
from typing import Any, Awaitable, Callable

from .wsx_payload import WsxResponseEncoder
from .types import Scope
from .transport_limits import DEFAULT_MAX_FRAME_SIZE, HttpBodyTooLarge, http_max_body_size


class BufferedAsgiEndpoint:
    """Call an ASGI application with bounded, whole request and response bodies."""

    DEFAULT_MAX_BODY_SIZE = DEFAULT_MAX_FRAME_SIZE

    def __init__(
        self,
        application: Callable[..., Awaitable[None]],
        max_body_size: int | None = None,
        reject_streaming: bool = True,
    ) -> None:
        max_body_size = http_max_body_size() if max_body_size is None else max_body_size
        if not callable(application):
            raise TypeError("application must be callable")
        if isinstance(max_body_size, bool) or not isinstance(max_body_size, int):
            raise TypeError("max_body_size must be an integer")
        if max_body_size < 0:
            raise ValueError("max_body_size must not be negative")
        if not isinstance(reject_streaming, bool):
            raise TypeError("reject_streaming must be a boolean")
        self.application = application
        self.max_body_size = max_body_size
        self.reject_streaming = reject_streaming

    async def serve(self, scope: Scope, body: bytes) -> dict[str, Any]:
        """Serve one buffered request and return its complete buffered response."""
        if not isinstance(scope, dict):
            raise TypeError("scope must be a dictionary")
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        if len(body) > self.max_body_size:
            raise HttpBodyTooLarge("request body exceeds configured limit")
        extensions = scope.get("extensions", {})
        if not isinstance(extensions, dict):
            raise TypeError("scope extensions must be a dictionary")
        if self.reject_streaming and "http.response.trailers" in extensions:
            raise ValueError("response trailers are not supported by a buffered endpoint")

        request_sent = False
        response_complete = asyncio.Event()
        status: int | None = None
        headers: list[list[str]] = []
        chunks: list[bytes] = []
        response_size = 0
        complete = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            if self.reject_streaming:
                await response_complete.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status, headers, response_size, complete
            if not isinstance(message, dict):
                raise TypeError("ASGI response message must be a dictionary")
            message_type = message.get("type")
            if message_type == "http.response.start":
                if status is not None:
                    raise ValueError("HTTP response started more than once")
                if complete:
                    raise ValueError("HTTP response is already complete")
                candidate_status = message.get("status")
                if (
                    isinstance(candidate_status, bool)
                    or not isinstance(candidate_status, int)
                    or not 100 <= candidate_status <= 599
                ):
                    raise ValueError("response status must be an integer from 100 to 599")
                if message.get("trailers", False):
                    raise ValueError("response trailers are not supported by a buffered endpoint")
                candidate_headers = self._decode_headers(message.get("headers", []))
                if self.reject_streaming and self._is_sse(candidate_headers):
                    raise ValueError("server-sent event responses cannot be buffered")
                status = candidate_status
                headers = candidate_headers
                return
            if message_type == "http.response.body":
                if status is None:
                    raise ValueError("HTTP response body was sent before response start")
                if complete:
                    raise ValueError("HTTP response completed more than once")
                chunk = message.get("body", b"")
                if not isinstance(chunk, bytes):
                    raise TypeError("HTTP response body must be bytes")
                next_size = response_size + len(chunk)
                if next_size > self.max_body_size:
                    raise HttpBodyTooLarge("response body exceeds configured limit")
                more_body = message.get("more_body", False)
                if not isinstance(more_body, bool):
                    raise TypeError("more_body must be a boolean")
                if self.reject_streaming and more_body:
                    raise ValueError("streaming responses are not supported by this endpoint")
                chunks.append(chunk)
                response_size = next_size
                if not more_body:
                    complete = True
                    response_complete.set()
                return
            if message_type == "http.response.trailers":
                raise ValueError("response trailers are not supported by a buffered endpoint")
            raise ValueError(f"invalid ASGI response message type: {message_type!r}")

        await self.application(scope, receive, send)
        if status is None:
            raise RuntimeError("the ASGI application did not start a response")
        if not complete:
            raise RuntimeError("the ASGI application did not complete its response")

        response_body = b"".join(chunks)
        if scope.get("method") == "WSK":
            response_body, headers = self._encode_wsk_response(response_body, headers)
        return {"status": status, "headers": headers, "body": response_body}

    def _decode_headers(self, raw_headers: Any) -> list[list[str]]:
        if not isinstance(raw_headers, (list, tuple)):
            raise TypeError("response headers must be a sequence")
        headers: list[list[str]] = []
        for pair in raw_headers:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("each response header must be a pair")
            name, value = pair
            if not isinstance(name, bytes) or not isinstance(value, bytes):
                raise TypeError("ASGI response headers must contain byte names and values")
            headers.append([name.decode("latin-1"), value.decode("latin-1")])
        return headers

    def _is_sse(self, headers: list[list[str]]) -> bool:
        for name, value in headers:
            if name.lower() == "content-type":
                return value.split(";", 1)[0].strip().lower() == "text/event-stream"
        return False

    def _encode_wsk_response(
        self, body: bytes, headers: list[list[str]]
    ) -> tuple[bytes, list[list[str]]]:
        content_type = next(
            (value for name, value in headers if name.lower() == "content-type"), ""
        )
        encoded = WsxResponseEncoder().encode(body, content_type)
        response_body = (encoded.text or "").encode("utf-8")
        adapted_headers = [
            [name, value]
            for name, value in headers
            if name.lower() not in {"content-type", "content-length"}
        ]
        adapted_headers.extend(
            [
                ["content-type", "application/json"],
                ["content-length", str(len(response_body))],
            ]
        )
        return response_body, adapted_headers
