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

"""HTTP control-flow exceptions: raised anywhere, answered by the errors
middleware.

Plain classes, no framework machinery: ``HTTPException(status, detail=None,
headers=None)`` carries the response status (an optional plain-text detail and
optional response ``headers`` — ASGI ``(name, value)`` byte pairs forwarded to
the response, e.g. a ``WWW-Authenticate`` challenge on a 401); the common
errors are pre-filled subclasses — ``HTTPBadRequest`` (400), ``HTTPNotFound``
(404), ``HTTPUnauthorized`` (401), ``HTTPForbidden`` (403),
``HTTPUnsupportedMediaType`` (415), ``HTTPUnprocessableContent`` (422).
``Redirect(location, status=302)`` is the
redirecting sibling: its ``location`` becomes the ``Location`` header. The
mapping to actual ASGI responses lives in ``middleware/errors.py``.

One exception here is not an HTTP error at all: ``WebSocketDisconnect`` is how
the websocket facade reports that the client is gone. A disconnect is not a
value a read can return — every read would have to be checked — so it arrives
as an exception, and the read loop that catches it simply ends.
"""

from __future__ import annotations

__all__ = [
    "HTTPBadRequest",
    "HTTPException",
    "HTTPForbidden",
    "HTTPNotFound",
    "HTTPUnauthorized",
    "HTTPUnprocessableContent",
    "HTTPUnsupportedMediaType",
    "Redirect",
    "WebSocketDisconnect",
]


class HTTPException(Exception):
    """HTTP error carried as an exception: ``status``, optional ``detail`` and ``headers``.

    ``headers`` are ASGI ``(name, value)`` byte pairs the errors middleware
    forwards onto the response (e.g. a ``WWW-Authenticate`` challenge).
    """

    def __init__(
        self,
        status: int,
        detail: str | None = None,
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        super().__init__(detail if detail is not None else f"HTTP {status}")
        self.status = status
        self.detail = detail
        self.headers: list[tuple[bytes, bytes]] = headers or []


class HTTPBadRequest(HTTPException):
    """400 Bad Request."""

    def __init__(
        self, detail: str | None = None, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(400, detail, headers)


class HTTPNotFound(HTTPException):
    """404 Not Found."""

    def __init__(
        self, detail: str | None = None, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(404, detail, headers)


class HTTPUnauthorized(HTTPException):
    """401 Unauthorized."""

    def __init__(
        self, detail: str | None = None, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(401, detail, headers)


class HTTPForbidden(HTTPException):
    """403 Forbidden."""

    def __init__(
        self, detail: str | None = None, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(403, detail, headers)


class HTTPUnsupportedMediaType(HTTPException):
    """415 Unsupported Media Type."""

    def __init__(
        self, detail: str | None = None, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(415, detail, headers)


class HTTPUnprocessableContent(HTTPException):
    """422 Unprocessable Content."""

    def __init__(
        self, detail: str | None = None, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(422, detail, headers)


class Redirect(HTTPException):
    """HTTP redirect: ``location`` becomes the ``Location`` header."""

    def __init__(
        self, location: str, status: int = 302, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        super().__init__(status, headers=headers)
        self.location = location


class WebSocketDisconnect(Exception):
    """The client is gone: ``code`` and ``reason`` as the ASGI message carried them.

    Raised by every read of the facade, and by its iterator, which ends on it.
    The default is 1000 with no reason, which is what an ASGI server sends when
    the disconnect message carries neither.
    """

    def __init__(self, code: int = 1000, reason: str = "") -> None:
        super().__init__(f"websocket disconnected: {code} {reason}".rstrip())
        self.code = code
        self.reason = reason
