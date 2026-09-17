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

"""HTTP access logging middleware.

Logs each request's arrival and completion (method, path, status, timing)
through the instance logger inherited from ``BaseMiddleware`` — never a
module-level logger.
"""

from __future__ import annotations

import logging
import time
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

from .base import BaseMiddleware, headers_dict

if TYPE_CHECKING:
    from ..types import ASGIApp, Receive, Scope, Send

__all__ = ["LoggingMiddleware"]


class LoggingMiddleware(BaseMiddleware):
    """Log request arrival and response completion with timing."""

    middleware_order = 200
    middleware_default = False

    def __init__(
        self,
        app: ASGIApp,
        server: Any,
        level: str = "INFO",
        include_headers: bool = False,
        include_query: bool = True,
        **options: Any,
    ) -> None:
        super().__init__(app, server, **options)
        self._level = getattr(logging, level.upper(), logging.INFO)
        self._include_headers = include_headers
        self._include_query = include_query

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Log the request, run the chain, then log status and timing."""
        start = time.perf_counter()
        method = scope.get("method", "?")
        path = scope.get("path", "/")
        query = scope.get("query_string", b"").decode("latin-1")
        request_info = f"{method} {path}"
        if self._include_query and query:
            request_info += f"?{query}"
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        self.logger.log(self._level, "<- %s from %s", request_info, client_ip)
        if self._include_headers:
            self.logger.debug("   Headers: %s", headers_dict(scope))

        status_code = 0

        async def send_with_logging(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_with_logging)
        except Exception as exc:
            duration = (time.perf_counter() - start) * 1000
            self.logger.error("-> %s ERROR: %s (%.1fms)", request_info, exc, duration)
            raise

        duration = (time.perf_counter() - start) * 1000
        self.logger.log(self._level, "-> %s %s (%.1fms)", request_info, status_code, duration)
