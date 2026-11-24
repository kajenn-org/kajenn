# Copyright 2026 Softwell S.r.l.
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

"""Security headers middleware: hardening headers on every HTTP response.

``SecurityHeadersMiddleware`` (order 50, enabled by default —
``security_headers=False`` disables it) adds a set of response headers that
tell the browser to be stricter with what it receives. Out of the box it adds
only the headers that cannot break a working application:

- ``X-Content-Type-Options: nosniff`` — no MIME sniffing;
- ``X-Frame-Options: SAMEORIGIN`` — no framing by another origin;
- ``Referrer-Policy: strict-origin-when-cross-origin`` — no full URL leaks
  to other origins;
- ``Strict-Transport-Security: max-age=31536000`` — ONLY on a request whose
  scope scheme is ``https`` (``hsts="auto"``): a browser ignores HSTS over
  plain HTTP, and sending it there would only mislead a reader of the logs.

Everything that depends on the application — ``Content-Security-Policy``,
``Permissions-Policy``, ``Cross-Origin-*-Policy`` — is off until configured,
by its named option or through the free ``headers`` mapping, which also
overrides (a string) or removes (``None``, ``False`` or ``""``) any default.

The headers are computed ONCE in ``__init__`` and validated there: a name that
is not an HTTP token, or a value carrying CR/LF, raises ``ValueError`` at
construction, never on a request. On the way out the middleware respects what
the application already said: a header the response carries is left alone,
unless ``override=True`` — so a route that sets its own CSP keeps it.

Order 50 puts this layer OUTSIDE ``errors`` (100): the error middleware writes
its answer through its own outer ``send``, so a 404 or a 500 still crosses
this layer and carries the headers. The chain only carries ``http`` scopes,
so no scope filtering happens here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING, Any

from .base import BaseMiddleware

if TYPE_CHECKING:
    from ..types import ASGIApp, Receive, Scope, Send

__all__ = ["SecurityHeadersMiddleware"]

HSTS_MODES = ("auto", "always", "never")
DEFAULT_HSTS_MAX_AGE = 31_536_000   # one year, the HSTS preload list's minimum

_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _encode_header(name: str, value: str) -> tuple[bytes, bytes]:
    """Validate one header and encode it as an ASGI ``(name, value)`` pair.

    Raises:
        ValueError: ``name`` is not an RFC 9110 token, or ``value`` carries
            a CR, LF or NUL (header injection).
    """
    if not isinstance(name, str) or not _TOKEN.match(name):
        raise ValueError(f"security_headers: invalid header name {name!r}")
    if not isinstance(value, str):
        raise ValueError(f"security_headers: header {name!r} value must be a string, got {value!r}")
    if any(char in value for char in "\r\n\0"):
        raise ValueError(f"security_headers: header {name!r} value contains a control character")
    return name.lower().encode("latin-1"), value.encode("latin-1")


class SecurityHeadersMiddleware(BaseMiddleware):
    """Add browser-hardening headers to every HTTP response.

    Every named option takes the header's value as a string; ``None`` (or any
    falsy value) means "do not send it". ``headers`` maps further header names
    to values and wins over the named options: a string sets or replaces, a
    falsy value removes.
    """

    middleware_order = 50
    middleware_default = True

    def __init__(
        self,
        app: ASGIApp,
        server: Any,
        x_content_type_options: str | None = "nosniff",
        x_frame_options: str | None = "SAMEORIGIN",
        referrer_policy: str | None = "strict-origin-when-cross-origin",
        content_security_policy: str | None = None,
        content_security_policy_report_only: str | None = None,
        permissions_policy: str | None = None,
        cross_origin_opener_policy: str | None = None,
        cross_origin_resource_policy: str | None = None,
        cross_origin_embedder_policy: str | None = None,
        hsts: str = "auto",
        hsts_max_age: int = DEFAULT_HSTS_MAX_AGE,
        hsts_include_subdomains: bool = False,
        hsts_preload: bool = False,
        headers: Mapping[str, str | bool | None] | None = None,
        override: bool = False,
        **options: Any,
    ) -> None:
        super().__init__(app, server, **options)
        if hsts not in HSTS_MODES:
            raise ValueError(f"security_headers: hsts must be one of {HSTS_MODES}, got {hsts!r}")
        if isinstance(hsts_max_age, bool) or not isinstance(hsts_max_age, int) or hsts_max_age < 0:
            raise ValueError(f"security_headers: hsts_max_age must be an int >= 0, got {hsts_max_age!r}")
        named: dict[str, Any] = {
            "x-content-type-options": x_content_type_options,
            "x-frame-options": x_frame_options,
            "referrer-policy": referrer_policy,
            "content-security-policy": content_security_policy,
            "content-security-policy-report-only": content_security_policy_report_only,
            "permissions-policy": permissions_policy,
            "cross-origin-opener-policy": cross_origin_opener_policy,
            "cross-origin-resource-policy": cross_origin_resource_policy,
            "cross-origin-embedder-policy": cross_origin_embedder_policy,
        }
        for name, value in (headers or {}).items():
            named[str(name).lower()] = value
        self._override = override
        self._hsts_mode = hsts
        if "strict-transport-security" in named:   # spelled out in ``headers``: it wins
            hsts_value: Any = named.pop("strict-transport-security")
        else:
            hsts_value = self._hsts_value(hsts_max_age, hsts_include_subdomains, hsts_preload)
        if hsts == "never" or not hsts_value:
            self._hsts: tuple[bytes, bytes] | None = None
        else:
            self._hsts = _encode_header("strict-transport-security", hsts_value)
        self._headers = [_encode_header(name, value) for name, value in named.items() if value]

    def _hsts_value(self, max_age: int, include_subdomains: bool, preload: bool) -> str:
        """The ``Strict-Transport-Security`` value built from the ``hsts_*`` options."""
        parts = [f"max-age={max_age}"]
        if include_subdomains:
            parts.append("includeSubDomains")
        if preload:
            parts.append("preload")
        return "; ".join(parts)

    @property
    def security_headers(self) -> list[tuple[bytes, bytes]]:
        """The headers added to every response, HSTS excluded (it depends on the scheme)."""
        return list(self._headers)

    def headers_for(self, scope: Scope) -> list[tuple[bytes, bytes]]:
        """The headers this layer adds to the response of ``scope``."""
        if self._hsts is None:
            return list(self._headers)
        if self._hsts_mode == "auto" and scope.get("scheme") != "https":
            return list(self._headers)
        return [*self._headers, self._hsts]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap ``send`` so the response start carries the security headers."""
        extra = self.headers_for(scope)
        if not extra:
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": self._merge(message.get("headers", []), extra)}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

    def _merge(
        self, response_headers: Any, extra: list[tuple[bytes, bytes]]
    ) -> list[tuple[bytes, bytes]]:
        """The response headers with ``extra`` added, respecting ``override``."""
        current = [(bytes(name), bytes(value)) for name, value in response_headers]
        present = {name.lower() for name, _value in current}
        if self._override:
            ours = {name for name, _value in extra}
            current = [(name, value) for name, value in current if name.lower() not in ours]
            return current + extra
        return current + [(name, value) for name, value in extra if name not in present]
