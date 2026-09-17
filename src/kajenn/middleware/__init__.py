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

"""Middleware capability: the chain as a mixin over the base server (D16).

The base server has NO middleware. This mixin adds the chain as a capability,
composed BEFORE the server class (``class MyServer(MiddlewareMixin,
BaseServer)``): its cooperative ``__init__`` peels ``middleware=`` (the
``{name: bool | dict}`` switches; ``None`` arms only the defaults) and
``middleware_registry=`` (extra ``{name: class}`` entries merged over
``default_registry()``), builds the chain ONCE around ``_base_call`` — the
adapter delegating to the next ``__call__`` in the MRO, i.e. the base
dispatch — and routes ONLY ``http`` scopes through it: ``lifespan`` and
``websocket`` go straight to ``super().__call__``. A composition WITHOUT the
mixin simply lacks the attributes — a different type, not a ghost.

``default_registry()`` returns a FRESH dict per call ({"errors":
ErrorMiddleware, "logging": LoggingMiddleware, "cors": CORSMiddleware,
"auth": AuthMiddleware, "session": SessionMiddleware}) — deliberately a function so no module-level
mutable registry exists.
It lives in this module, not in ``base.py``, because ``base.py`` cannot import
the concrete middleware modules (which subclass ``BaseMiddleware``) without a
cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .authentication import AuthMiddleware
from .base import BaseMiddleware, build_chain, headers_dict
from .cors import CORSMiddleware
from .errors import ErrorMiddleware
from .logging import LoggingMiddleware
from .session import SessionMiddleware

if TYPE_CHECKING:
    from ..types import ASGIApp, Receive, Scope, Send

__all__ = [
    "AuthMiddleware",
    "BaseMiddleware",
    "CORSMiddleware",
    "ErrorMiddleware",
    "LoggingMiddleware",
    "MiddlewareMixin",
    "SessionMiddleware",
    "build_chain",
    "default_registry",
    "headers_dict",
]


def default_registry() -> dict[str, type[BaseMiddleware]]:
    """A fresh ``{name: class}`` mapping of the middlewares shipped with the core."""
    return {
        "errors": ErrorMiddleware,
        "logging": LoggingMiddleware,
        "cors": CORSMiddleware,
        "auth": AuthMiddleware,
        "session": SessionMiddleware,
    }


class MiddlewareMixin:
    """Middleware capability mixin, composed BEFORE a server class.

    Constructor kwargs peeled here: ``middleware`` — the ``{name: bool | dict}``
    switches (a dict value enables the middleware and becomes its constructor
    options); ``middleware_registry`` — extra ``{name: class}`` entries merged
    over ``default_registry()``.
    """

    def __init__(self, **kwargs: Any) -> None:
        middleware: dict[str, bool | dict[str, Any]] | None = kwargs.pop("middleware", None)
        extra: dict[str, type[BaseMiddleware]] | None = kwargs.pop("middleware_registry", None)
        super().__init__(**kwargs)
        registry = default_registry()
        if extra:
            registry.update(extra)
        self._middleware_chain = build_chain(middleware or {}, self._base_call, self, registry)

    @property
    def middleware_chain(self) -> ASGIApp:
        """The assembled chain: outermost middleware first, base dispatch innermost."""
        return self._middleware_chain

    def get_middleware(self, middleware_class: type[BaseMiddleware]) -> BaseMiddleware | None:
        """The layer of that class in this chain, or ``None`` when it is off.

        Args:
            middleware_class: the class to look for; a subclass of it answers too.

        Returns:
            The assembled instance, or ``None`` — a middleware nobody enabled
            has none.

        The chain is walked from its head: every layer holds the next one, so
        the instances need not be kept a second time. What reads this is code
        that must do for a websocket what a middleware does for HTTP — the
        handshake asking ``SessionMiddleware`` for the session of a scope the
        chain never saw.
        """
        layer = self.middleware_chain
        while isinstance(layer, BaseMiddleware):
            if isinstance(layer, middleware_class):
                return layer
            layer = layer.app
        return None

    async def _base_call(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Innermost chain target: the next ``__call__`` after this mixin in the MRO."""
        await super().__call__(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route ``http`` scopes through the chain; every other scope passes straight through."""
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return
        await self.middleware_chain(scope, receive, send)
