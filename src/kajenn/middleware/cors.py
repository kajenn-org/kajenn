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

"""CORS (Cross-Origin Resource Sharing) middleware.

Adds CORS headers to HTTP responses and answers preflight ``OPTIONS``
requests. Preflight-only headers are precomputed once in ``__init__``.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

from .base import BaseMiddleware

if TYPE_CHECKING:
    from ..types import ASGIApp, Receive, Scope, Send

__all__ = ["CORSMiddleware"]


def _split_and_strip(value: str | list[str] | None, default: list[str] | None = None) -> list[str]:
    """Split a comma-separated string into a stripped list; pass a list through."""
    if value is None:
        return list(default) if default is not None else []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",")]
    return list(value)


class CORSMiddleware(BaseMiddleware):
    """Answer CORS preflight requests and add CORS headers to responses."""

    middleware_order = 300
    middleware_default = False

    def __init__(
        self,
        app: ASGIApp,
        server: Any,
        allow_origins: str | list[str] | None = None,
        allow_methods: str | list[str] | None = None,
        allow_headers: str | list[str] | None = None,
        allow_credentials: bool = False,
        expose_headers: str | list[str] | None = None,
        max_age: int = 600,
        **options: Any,
    ) -> None:
        super().__init__(app, server, **options)
        self._allow_origins = _split_and_strip(allow_origins, ["*"])
        self._allow_methods = _split_and_strip(
            allow_methods, ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD"]
        )
        self._allow_headers = _split_and_strip(allow_headers, ["*"])
        self._allow_credentials = allow_credentials
        self._expose_headers = _split_and_strip(expose_headers)
        self._max_age = max_age
        self._allow_all_origins = "*" in self._allow_origins
        self._preflight_headers = self._build_preflight_headers()

    def _build_preflight_headers(self) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"access-control-allow-methods", ", ".join(self._allow_methods).encode()),
            (b"access-control-max-age", str(self._max_age).encode()),
        ]
        if self._allow_headers:
            value = (
                b"*"
                if "*" in self._allow_headers
                else ", ".join(self._allow_headers).encode()
            )
            headers.append((b"access-control-allow-headers", value))
        if self._allow_credentials:
            headers.append((b"access-control-allow-credentials", b"true"))
        return headers

    def _cors_headers(self, origin: str | None) -> list[tuple[bytes, bytes]]:
        """CORS headers for one response, or ``[]`` when the origin is not allowed."""
        if not origin:
            return []
        if self._allow_all_origins:
            if self._allow_credentials:
                headers = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]
            else:
                headers = [(b"access-control-allow-origin", b"*")]
        elif origin in self._allow_origins:
            headers = [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]
        else:
            return []
        if self._allow_credentials:
            headers.append((b"access-control-allow-credentials", b"true"))
        if self._expose_headers:
            headers.append(
                (b"access-control-expose-headers", ", ".join(self._expose_headers).encode())
            )
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Short-circuit an OPTIONS preflight; otherwise wrap ``send`` to add CORS headers."""
        origin = None
        for name, value in scope.get("headers", []):
            if name == b"origin":
                origin = value.decode("latin-1")
                break

        if scope.get("method") == "OPTIONS" and origin:
            await self._respond_preflight(send, origin)
            return

        cors_headers = self._cors_headers(origin)

        async def send_with_cors(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start" and cors_headers:
                headers = list(message.get("headers", []))
                headers.extend(cors_headers)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)

    async def _respond_preflight(self, send: Send, origin: str) -> None:
        headers = self._cors_headers(origin)
        if not headers:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        headers = headers + self._preflight_headers
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": b""})
