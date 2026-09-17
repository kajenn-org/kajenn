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

"""HTTP response: one flat, buffered, TYTX-aware class.

``Response`` is a single slotted class — no subclass hierarchy (no
JSON/HTML/Streaming/File variants). It buffers the body in memory and, as an
ASGI application, emits exactly two messages (``http.response.start`` +
``http.response.body``). It can be built with content or created empty and
configured through ``set_header``/``set_cookie``/``set_result``/``set_error``
before being sent.

``set_result`` dispatches by result type: ``dict``/``list`` → JSON bytes via
``genro_tytx.json_dumps`` (or TYTX serialization — media type from
``media_types.TRANSPORT_MIME`` — when the bound request is in TYTX mode),
``Path`` → file bytes, ``bytes`` → as-is, ``str`` → UTF-8 text, ``None`` →
empty. ``set_error`` maps an exception to a status:
``HTTPException`` subtypes carry their own status; ``ValueError``/``TypeError``
→ 400, ``FileNotFoundError`` → 404, ``PermissionError`` → 403, anything else
→ 500 (logged). ``set_cookie`` appends a ``set-cookie`` header.

The ``request`` binding is optional (``request=None``); every request-dependent
branch (the TYTX path) guards for its absence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

from genro_tytx import json_dumps, to_tytx

from .exceptions import HTTPException
from .media_types import TRANSPORT_MIME
from .types import Receive, Scope, Send

__all__ = ["Response"]

# Header inputs accepted at construction time.
HeadersInput = Mapping[str, str] | list[tuple[str, str]] | None


class Response:
    """Buffered HTTP response, usable directly as an ASGI application.

    Example:
        >>> response = Response(content="Hello", media_type="text/plain")
        >>> await response(scope, receive, send)

        # Or create empty and configure:
        >>> response = Response()
        >>> response.set_header("X-Custom", "value")
        >>> response.set_result({"data": 123})  # auto-detects JSON
        >>> await response(scope, receive, send)
    """

    __slots__ = ("body", "status_code", "_media_type", "_headers", "request")

    media_type: str | None = None
    charset: str = "utf-8"

    # Non-HTTPException error types mapped to a status code (else 500).
    ERROR_MAP: dict[str, int] = {
        "ValueError": 400,
        "TypeError": 400,
        "FileNotFoundError": 404,
        "PermissionError": 403,
    }

    def __init__(
        self,
        content: bytes | str | None = None,
        status_code: int = 200,
        headers: HeadersInput = None,
        media_type: str | None = None,
        request: Any = None,
    ) -> None:
        """Build a response.

        Note:
            Status 204 (No Content) and 304 (Not Modified) must not carry a
            body per RFC 7230; providing content with those codes may be
            rejected or truncated by the ASGI server.
        """
        self.request = request
        self.status_code = status_code
        if headers is None:
            self._headers: list[tuple[str, str]] = []
        elif isinstance(headers, list):
            self._headers = list(headers)
        else:
            self._headers = list(headers.items())
        self._media_type = media_type
        self.body = self._encode_content(content)

        effective_media_type = self._media_type if self._media_type is not None else self.media_type
        if effective_media_type is not None:
            header_names = {name.lower() for name, _ in self._headers}
            if "content-type" not in header_names:
                content_type = self._get_content_type()
                if content_type:
                    self._headers.append(("content-type", content_type))
        self._add_content_length()

    def _encode_content(self, content: bytes | str | None) -> bytes:
        """Encode content to bytes: None → b"", bytes → as-is, str → charset."""
        if content is None:
            return b""
        if isinstance(content, bytes):
            return content
        return content.encode(self.charset)

    def _get_content_type(self) -> str | None:
        """Content-Type value; appends the charset for text types lacking one."""
        effective = self._media_type if self._media_type is not None else self.media_type
        if effective is None:
            return None
        if effective.startswith("text/") and "charset" not in effective:
            return f"{effective}; charset={self.charset}"
        return effective

    def _add_content_length(self) -> None:
        """Append a content-length header if none is present."""
        header_names = {name.lower() for name, _ in self._headers}
        if "content-length" not in header_names:
            self._headers.append(("content-length", str(len(self.body))))

    def _build_headers(self) -> list[tuple[bytes, bytes]]:
        """ASGI headers: names lowercased, latin-1 encoded (HTTP standard)."""
        return [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in self._headers
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI interface: send exactly ``http.response.start`` + one body."""
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self._build_headers(),
            }
        )
        await send({"type": "http.response.body", "body": self.body})

    def set_header(self, name: str, value: str) -> None:
        """Append a response header. Usable before ``set_result``."""
        self._headers.append((name, value))

    def set_cookie(
        self,
        key: str,
        value: str = "",
        *,
        max_age: int | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: str | None = "lax",
    ) -> None:
        """Append a ``set-cookie`` header (the value is URL-encoded)."""
        cookie = f"{key}={quote(value, safe='')}"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        if path:
            cookie += f"; Path={path}"
        if domain:
            cookie += f"; Domain={domain}"
        if secure:
            cookie += "; Secure"
        if httponly:
            cookie += "; HttpOnly"
        if samesite:
            cookie += f"; SameSite={samesite.capitalize()}"
        self.set_header("set-cookie", cookie)

    def set_result(self, result: Any, metadata: dict[str, Any] | None = None) -> None:
        """Set the body from a handler result, dispatching by type.

        ``dict``/``list`` → JSON bytes (``genro_tytx.json_dumps``), or TYTX
        bytes/text when the bound request is in TYTX mode; ``Path`` → file
        bytes; ``bytes`` → as-is;
        ``str`` → UTF-8 text; ``None`` → empty; anything else → its ``str``.
        A ``media_type`` in ``metadata`` overrides the type-based default.
        """
        override = metadata.get("media_type") if metadata else None
        if isinstance(result, (dict, list)):
            if self.request is not None and self.request.tytx_mode:
                transport = cast(
                    Literal["json", "xml", "msgpack"], self.request.tytx_transport or "json"
                )
                encoded = to_tytx(result, transport)
                self.body = encoded if isinstance(encoded, bytes) else encoded.encode("utf-8")
                self._media_type = override or TRANSPORT_MIME[transport]
            else:
                self.body = json_dumps(result)
                self._media_type = override or "application/json"
        elif isinstance(result, Path):
            self.body = result.read_bytes()
            self._media_type = override or "application/octet-stream"
        elif isinstance(result, bytes):
            self.body = result
            self._media_type = override or "application/octet-stream"
        elif isinstance(result, str):
            self.body = result.encode(self.charset)
            self._media_type = override or "text/plain"
        elif result is None:
            self.body = b""
            self._media_type = override or "text/plain"
        else:
            self.body = str(result).encode(self.charset)
            self._media_type = override or "text/plain"
        self._update_content_headers()

    def _update_content_headers(self) -> None:
        """Rebuild content-type and content-length after the body changes."""
        self._headers = [
            (name, value)
            for name, value in self._headers
            if name.lower() not in ("content-type", "content-length")
        ]
        content_type = self._get_content_type()
        if content_type:
            self._headers.append(("content-type", content_type))
        self._headers.append(("content-length", str(len(self.body))))

    def set_error(self, error: Exception) -> None:
        """Set the response as an error, mapping the exception to a status.

        ``HTTPException`` subtypes carry their own status; other types are
        looked up in ``ERROR_MAP`` (default 500, logged). The body is the
        ``{"error": <message>}`` document through ``set_result``.
        """
        if isinstance(error, HTTPException):
            self.status_code = error.status
        else:
            self.status_code = self.ERROR_MAP.get(type(error).__name__, 500)
        if self.status_code == 500:
            logging.getLogger(__name__).exception("Handler error: %s", error)
        self.set_result({"error": str(error)})
