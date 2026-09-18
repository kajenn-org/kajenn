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

"""Error middleware: the outermost try/except of the chain.

``ErrorMiddleware`` (order 100, the only middleware enabled by default —
``errors=False`` disables it) maps control-flow exceptions to responses:
``Redirect`` → its status plus the ``Location`` header, ``HTTPException`` →
its status with the detail, any other ``Exception`` → a hidden 500 logged via
the instance logger. Responses are built with the ``Response`` class; an
exception's ``headers`` (e.g. a ``WWW-Authenticate`` challenge) are forwarded
onto the response.

Content negotiation: the error body follows the caller's ``Accept``. A
caller asking for JSON (``application/json`` or ``*/*``, never
``text/html``) gets the ``{"error": ...}`` document built by
``Response.set_error`` — the single JSON error path. Anyone else gets a
``text/plain`` body, and a missing ``Accept`` is ``text/plain`` too, so a
browser navigation never receives the JSON document.

A 401 is answered exactly like any other error: the bare status with the
exception's ``WWW-Authenticate`` challenge forwarded onto it. The core
never points a caller at a login page — it does not own one; an application
that wants to redirect a browser to its own login surface does it in its own
routes, not here.

The middleware wraps ``send`` to track whether ``http.response.start`` has
already passed downstream: an exception raised AFTER the response started
cannot be answered (a second start would corrupt the stream), so it is logged
and re-raised — the server/transport tears the connection down. The chain only
carries ``http`` scopes (the mixin routes the others past it), so no scope
filtering happens here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import HTTPException, Redirect
from ..response import Response
from .base import BaseMiddleware, headers_dict

if TYPE_CHECKING:
    from ..types import Message, Receive, Scope, Send

__all__ = ["ErrorMiddleware"]


class ErrorMiddleware(BaseMiddleware):
    """Outermost middleware answering raised exceptions with HTTP responses."""

    middleware_order = 100
    middleware_default = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the chain; map raised exceptions to responses unless already started."""
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                self.logger.exception(
                    "error after response started serving %s", scope.get("path", "?")
                )
                raise
            response = self._error_response(exc, scope)
            await response(scope, receive, send)

    def _error_response(self, exc: Exception, scope: Scope) -> Response:
        """Build the ``Response`` for a raised exception, negotiating the body format."""
        if isinstance(exc, Redirect):
            response = Response(status_code=exc.status, media_type="text/plain")
            response.set_header("location", exc.location)
            self._forward_headers(response, exc)
            return response
        wants_json = self._wants_json(headers_dict(scope))
        if isinstance(exc, HTTPException):
            if wants_json:
                response = Response()
                response.set_error(exc)
            else:
                response = Response(
                    content=exc.detail or "", status_code=exc.status, media_type="text/plain"
                )
        else:
            self.logger.exception("unhandled error serving %s", scope.get("path", "?"))
            if wants_json:
                response = Response(status_code=500)
                response.set_result({"error": "Internal Server Error"})
            else:
                response = Response(
                    content="Internal Server Error", status_code=500, media_type="text/plain"
                )
        self._forward_headers(response, exc)
        return response

    def _wants_json(self, headers: dict[str, str]) -> bool:
        """True when the caller's ``Accept`` asks for JSON (never for a browser navigation).

        A missing ``Accept`` keeps the ``text/plain`` default; an
        ``Accept`` naming ``text/html`` (a browser) also stays text; only an API
        caller (``application/json`` or ``*/*``) gets the JSON error document.
        """
        accept = headers.get("accept", "")
        if not accept or "text/html" in accept:
            return False
        return "application/json" in accept or "*/*" in accept

    def _forward_headers(self, response: Response, exc: Exception) -> None:
        """Forward an exception's ASGI header pairs onto the response."""
        for name, value in getattr(exc, "headers", []):
            response.set_header(name.decode("latin-1"), value.decode("latin-1"))
