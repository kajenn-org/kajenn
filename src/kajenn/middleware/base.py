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

"""Middleware base class and the chain mechanism.

``BaseMiddleware(app, server, **options)`` receives BOTH ends at construction
(dual parent-child): ``app`` is the next ASGI callable in the chain, ``server``
the owning server — never discovered by walking wrappers. Subclasses declare
``middleware_order`` (lower = outermost; errors 100, logging 200, security 300,
auth 400, business 500-800, transformation 900) and ``middleware_default``
(their on/off state when the config does not name them).

``build_chain(config, innermost, server, registry)`` assembles the chain from
explicit inputs — ``config`` maps ``{name: bool | dict}`` (a dict value means
"on" and becomes the middleware's constructor options), ``registry`` maps
``{name: class}`` and is always passed in: there is NO module-level registry
and no import-time registration anywhere. Enabled middlewares are sorted by
``middleware_order`` and wrapped innermost-out, so the lowest order ends up
outermost. A config name missing from the registry raises ``ValueError``.

``headers_dict(scope)`` parses the ASGI headers into a lowercase-keyed dict
cached as ``scope["_headers"]`` — the one header-parse shared by the session
and auth middlewares downstream. ``cookie_value(scope, name)`` reads one
cookie off it, pair by pair, so a malformed sibling cookie never costs the
request the cookies that are well-formed.
"""

from __future__ import annotations

import logging
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..types import ASGIApp, Receive, Scope, Send

__all__ = ["BaseMiddleware", "build_chain", "cookie_value", "headers_dict"]


def headers_dict(scope: Scope) -> dict[str, str]:
    """Parse scope headers into a lowercase-keyed dict, cached as ``scope["_headers"]``.

    Names and values are decoded as latin-1 per the ASGI spec; duplicate
    headers collapse to the last value.
    """
    headers: dict[str, str] | None = scope.get("_headers")
    if headers is None:
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
        }
        scope["_headers"] = headers
    return headers


def cookie_value(scope: Scope, name: str) -> str | None:
    """The value of the ``name`` cookie on the request, or ``None``.

    Parsed pair by pair: a whole-header ``SimpleCookie.load`` raises
    ``CookieError`` on the first cookie with an illegal key (third-party
    trackers ship them), losing every well-formed sibling with it.
    """
    cookie_header = headers_dict(scope).get("cookie")
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    for pair in cookie_header.split(";"):
        try:
            jar.load(pair.strip())
        except CookieError:
            continue
    morsel = jar.get(name)
    return morsel.value if morsel is not None else None


class BaseMiddleware:
    """Base class for chain middlewares: holds the next app and the server.

    Class attributes:
        middleware_order: position in the chain (lower = outermost).
        middleware_default: on/off state when the config does not name it.
    """

    middleware_order: int = 500
    middleware_default: bool = False

    def __init__(self, app: ASGIApp, server: Any, **options: Any) -> None:
        self._app = app
        self._server = server
        self._logger = logging.getLogger(f"{type(self).__module__}.{type(self).__name__}")
        if options:
            unexpected = ", ".join(sorted(options))
            raise TypeError(
                f"{type(self).__name__}.__init__() got unexpected keyword arguments: {unexpected}"
            )

    @property
    def app(self) -> ASGIApp:
        """The next ASGI callable in the chain (towards the base dispatch)."""
        return self._app

    @property
    def server(self) -> Any:
        """The owning server, handed in at construction — never walked to."""
        return self._server

    @property
    def logger(self) -> logging.Logger:
        """This middleware's instance logger."""
        return self._logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point: concrete middlewares must implement it."""
        raise NotImplementedError(f"{type(self).__name__} does not implement the ASGI callable")


def build_chain(
    config: dict[str, bool | dict[str, Any]],
    innermost: ASGIApp,
    server: Any,
    registry: dict[str, type[BaseMiddleware]],
) -> ASGIApp:
    """Assemble the middleware chain around ``innermost`` and return its head.

    Every middleware named by ``config`` must exist in ``registry``
    (``ValueError`` otherwise); registry entries the config does not name
    follow their ``middleware_default``. A dict config value enables the
    middleware and becomes its constructor options.
    """
    unknown = sorted(name for name in config if name not in registry)
    if unknown:
        raise ValueError(f"unknown middleware name(s) in config: {', '.join(unknown)}")
    enabled: list[tuple[int, type[BaseMiddleware], dict[str, Any]]] = []
    for name, cls in registry.items():
        value = config.get(name)
        if value is None:
            if not cls.middleware_default:
                continue
            options: dict[str, Any] = {}
        elif isinstance(value, dict):
            options = value
        elif value:
            options = {}
        else:
            continue
        enabled.append((cls.middleware_order, cls, options))
    enabled.sort(key=lambda item: item[0])
    app = innermost
    for _order, cls, options in reversed(enabled):
        app = cls(app, server, **options)
    return app
