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

"""Middleware core tests (SPECIFICATION.md D16, Macro 2 Phase 2).

The chain is driven directly through the composed server's ``__call__`` (no
uvicorn): a canned http scope, a recording ``send``. Asserted here: chain
ordering by ``middleware_order``, the error middleware's exception mapping
(404 from ``HTTPNotFound``, 302 from ``Redirect``, 500 from a plain
``Exception`` — and the server survives), the ``lifespan`` bypass, the
untouched plain-``BaseServer`` composition, and the D16 leftover-kwarg
``TypeError`` through the full MRO.
"""

from __future__ import annotations

import pytest

from kajenn import BaseApplication, BaseServer, MiddlewareMixin
from kajenn.exceptions import HTTPNotFound, Redirect
from kajenn.middleware import BaseMiddleware
from kajenn.middleware.base import headers_dict
from kajenn.types import Message, Receive, Scope, Send


class MwServer(MiddlewareMixin, BaseServer):
    """The Phase 2 composition: middleware capability over the base server."""


class RoutedApp(BaseApplication):
    """Test app: routes raising the control-flow exceptions under test."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        if path == "/missing":
            raise HTTPNotFound("nothing here")
        if path == "/old":
            raise Redirect("/new")
        if path == "/boom":
            raise RuntimeError("boom")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": f"ok:{path}".encode()})


class RecordingMiddleware(BaseMiddleware):
    """Middleware appending its label to a shared list when invoked."""

    def __init__(self, app, server, label="", calls=None, **options):
        super().__init__(app, server, **options)
        self.label = label
        self.calls = calls if calls is not None else []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls.append(self.label)
        await self.app(scope, receive, send)


class EarlyMiddleware(RecordingMiddleware):
    middleware_order = 200


class LateMiddleware(RecordingMiddleware):
    middleware_order = 800


async def http_get(server: BaseServer, path: str) -> list[Message]:
    """Drive one GET through ``server`` at the ASGI level; return what it sent."""
    scope: Scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def response_status(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def response_headers(sent: list[Message]) -> dict[bytes, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"])


def response_body(sent: list[Message]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


class TestHeadersDict:
    """The header helper every middleware shares: latin-1 decode, lowercase, cached."""

    def test_names_are_lowercased_and_the_last_duplicate_wins(self) -> None:
        scope: Scope = {"headers": [(b"X-One", b"1"), (b"x-one", b"2")]}
        assert headers_dict(scope) == {"x-one": "2"}

    def test_the_parsed_dict_is_cached_on_the_scope(self) -> None:
        scope: Scope = {"headers": [(b"X-One", b"1")]}
        parsed = headers_dict(scope)
        assert scope["_headers"] is parsed
        assert headers_dict(scope) is parsed  # second call reuses the cache


class TestChainAssembly:
    async def test_chain_invokes_middlewares_in_order(self) -> None:
        calls: list[str] = []
        server = MwServer(
            applications=[RoutedApp(mount="")],
            middleware={
                "late": {"label": "late", "calls": calls},
                "early": {"label": "early", "calls": calls},
            },
            middleware_registry={"early": EarlyMiddleware, "late": LateMiddleware},
        )
        sent = await http_get(server, "/")
        assert calls == ["early", "late"]
        assert response_status(sent) == 200
        assert response_body(sent) == b"ok:/"

    async def test_unknown_middleware_name_raises(self) -> None:
        with pytest.raises(ValueError, match="bogus"):
            MwServer(applications=[RoutedApp(mount="")], middleware={"bogus": True})

    async def test_false_switch_disables_a_default_middleware(self) -> None:
        server = MwServer(applications=[RoutedApp(mount="")], middleware={"errors": False})
        with pytest.raises(RuntimeError, match="boom"):
            await http_get(server, "/boom")


class TestErrorMiddleware:
    async def test_http_exception_maps_to_its_status(self) -> None:
        server = MwServer(applications=[RoutedApp(mount="")])
        sent = await http_get(server, "/missing")
        assert response_status(sent) == 404
        assert response_body(sent) == b"nothing here"

    async def test_redirect_maps_to_status_and_location(self) -> None:
        server = MwServer(applications=[RoutedApp(mount="")])
        sent = await http_get(server, "/old")
        assert response_status(sent) == 302
        assert response_headers(sent)[b"location"] == b"/new"

    async def test_plain_exception_maps_to_500_and_server_survives(self) -> None:
        server = MwServer(applications=[RoutedApp(mount="")])
        boom = await http_get(server, "/boom")
        assert response_status(boom) == 500
        assert response_body(boom) == b"Internal Server Error"
        healthy = await http_get(server, "/")
        assert response_status(healthy) == 200


class TestScopeRouting:
    async def test_lifespan_scope_bypasses_the_chain(self) -> None:
        calls: list[str] = []
        server = MwServer(
            applications=[RoutedApp(mount="")],
            middleware={"early": {"label": "early", "calls": calls}},
            middleware_registry={"early": EarlyMiddleware},
        )
        queue = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[Message] = []

        async def receive() -> Message:
            return queue.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        await server({"type": "lifespan"}, receive, send)
        assert calls == []
        assert {"type": "lifespan.startup.complete"} in sent
        assert {"type": "lifespan.shutdown.complete"} in sent


class TestComposition:
    def test_plain_base_server_lacks_the_mixin_attrs(self) -> None:
        server = BaseServer(applications=[RoutedApp(mount="")])
        assert not hasattr(server, "middleware_chain")

    def test_leftover_kwarg_raises_naming_it_through_the_mro(self) -> None:
        with pytest.raises(TypeError, match="bogus"):
            MwServer(applications=[RoutedApp(mount="")], bogus=3)

    def test_middleware_options_leftover_raises_naming_it(self) -> None:
        with pytest.raises(TypeError, match="bogus"):
            MwServer(applications=[RoutedApp(mount="")], middleware={"errors": {"bogus": True}})
