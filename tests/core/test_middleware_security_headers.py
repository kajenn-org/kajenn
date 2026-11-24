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

"""``SecurityHeadersMiddleware`` tests.

Two levels: the middleware driven directly around a canned inner app (the
header set, HSTS by scheme, the ``headers`` mapping, ``override``, the
construction-time validation), and the middleware assembled by the server
(on by default, outermost, switched off or configured through ``middleware=``
and the recipe, error responses included).
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn import AsgiConfigBuilder, AsgiServer, BaseApplication, BaseServer, MiddlewareMixin
from kajenn.exceptions import HTTPNotFound
from kajenn.middleware import SecurityHeadersMiddleware, default_registry
from kajenn.middleware.base import BaseMiddleware
from kajenn.types import Message, Receive, Scope, Send

DEFAULTS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"SAMEORIGIN",
    b"referrer-policy": b"strict-origin-when-cross-origin",
}


def make_inner(headers: list[tuple[bytes, bytes]] | None = None) -> Any:
    """An inner ASGI app answering 200 with ``headers``."""

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": list(headers or [])})
        await send({"type": "http.response.body", "body": b"ok"})

    return inner


async def drive(app: Any, scheme: str = "http") -> list[tuple[bytes, bytes]]:
    """One GET through ``app``; the start message's header pairs."""
    scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "scheme": scheme}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return [(bytes(n), bytes(v)) for n, v in start["headers"]]


def build(inner_headers: list[tuple[bytes, bytes]] | None = None, **options: Any) -> Any:
    return SecurityHeadersMiddleware(make_inner(inner_headers), server=None, **options)


class TestDefaults:
    async def test_default_set_on_plain_http(self) -> None:
        headers = dict(await drive(build()))
        for name, value in DEFAULTS.items():
            assert headers[name] == value
        assert b"strict-transport-security" not in headers
        assert b"content-security-policy" not in headers

    async def test_hsts_auto_on_https(self) -> None:
        headers = dict(await drive(build(), scheme="https"))
        assert headers[b"strict-transport-security"] == b"max-age=31536000"

    async def test_registry_entry_is_on_and_outermost(self) -> None:
        registry = default_registry()
        assert registry["security_headers"] is SecurityHeadersMiddleware
        assert SecurityHeadersMiddleware.middleware_default is True
        orders = [cls.middleware_order for name, cls in registry.items() if name != "security_headers"]
        assert SecurityHeadersMiddleware.middleware_order < min(orders)


class TestHsts:
    async def test_always_sends_on_http(self) -> None:
        headers = dict(await drive(build(hsts="always")))
        assert b"strict-transport-security" in headers

    async def test_never_sends_on_https(self) -> None:
        headers = dict(await drive(build(hsts="never"), scheme="https"))
        assert b"strict-transport-security" not in headers

    async def test_directives(self) -> None:
        mw = build(hsts_max_age=63072000, hsts_include_subdomains=True, hsts_preload=True)
        headers = dict(await drive(mw, scheme="https"))
        assert headers[b"strict-transport-security"] == b"max-age=63072000; includeSubDomains; preload"

    async def test_headers_mapping_replaces_the_built_value(self) -> None:
        mw = build(headers={"Strict-Transport-Security": "max-age=60"})
        headers = dict(await drive(mw, scheme="https"))
        assert headers[b"strict-transport-security"] == b"max-age=60"

    async def test_headers_mapping_none_removes_it(self) -> None:
        mw = build(headers={"Strict-Transport-Security": None})
        headers = dict(await drive(mw, scheme="https"))
        assert b"strict-transport-security" not in headers

    @pytest.mark.parametrize("bad", ["sometimes", True, ""])
    def test_unknown_mode_raises(self, bad: Any) -> None:
        with pytest.raises(ValueError, match="hsts must be"):
            build(hsts=bad)

    @pytest.mark.parametrize("bad", [-1, "1", True])
    def test_bad_max_age_raises(self, bad: Any) -> None:
        with pytest.raises(ValueError, match="hsts_max_age"):
            build(hsts_max_age=bad)


class TestConfiguration:
    async def test_named_option_disables_a_default(self) -> None:
        headers = dict(await drive(build(x_frame_options=None)))
        assert b"x-frame-options" not in headers
        assert headers[b"x-content-type-options"] == b"nosniff"

    async def test_named_options_add_opt_in_headers(self) -> None:
        mw = build(
            content_security_policy="default-src 'self'",
            permissions_policy="camera=()",
            cross_origin_opener_policy="same-origin",
        )
        headers = dict(await drive(mw))
        assert headers[b"content-security-policy"] == b"default-src 'self'"
        assert headers[b"permissions-policy"] == b"camera=()"
        assert headers[b"cross-origin-opener-policy"] == b"same-origin"

    async def test_headers_mapping_adds_overrides_and_removes(self) -> None:
        mw = build(headers={"X-Frame-Options": "DENY", "Referrer-Policy": False, "X-Custom": "1"})
        headers = dict(await drive(mw))
        assert headers[b"x-frame-options"] == b"DENY"
        assert b"referrer-policy" not in headers
        assert headers[b"x-custom"] == b"1"

    async def test_nothing_configured_passes_send_through(self) -> None:
        mw = build(x_content_type_options=None, x_frame_options=None, referrer_policy=None)
        assert await drive(mw) == []

    def test_unknown_option_raises(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword"):
            build(x_xss_protection="1")

    @pytest.mark.parametrize(
        "headers",
        [{"Bad Name": "x"}, {"X-Ok": "a\r\nSet-Cookie: x=1"}, {"X-Ok": 1}, {"": "x"}],
    )
    def test_invalid_header_raises_at_construction(self, headers: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="security_headers"):
            build(headers=headers)


class TestApplicationHeaders:
    async def test_app_header_wins_by_default(self) -> None:
        mw = build([(b"X-Frame-Options", b"DENY")])
        pairs = await drive(mw)
        frame = [v for n, v in pairs if n.lower() == b"x-frame-options"]
        assert frame == [b"DENY"]

    async def test_override_replaces_app_header(self) -> None:
        mw = build([(b"X-Frame-Options", b"ALLOWALL"), (b"content-type", b"text/plain")], override=True)
        pairs = await drive(mw)
        frame = [v for n, v in pairs if n.lower() == b"x-frame-options"]
        assert frame == [b"SAMEORIGIN"]
        assert (b"content-type", b"text/plain") in pairs


class OkApp(BaseApplication):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"].endswith("/missing"):
            raise HTTPNotFound("missing")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class MwServer(MiddlewareMixin, BaseServer):
    """Middleware capability over the base server."""


def chain_names(server: Any) -> list[str]:
    names: list[str] = []
    node: object = server.middleware_chain
    while isinstance(node, BaseMiddleware):
        names.append(type(node).__name__)
        node = node.app
    return names


class TestInTheServer:
    async def test_on_by_default_and_outermost(
        self, http_request, response_headers
    ) -> None:
        server = MwServer(applications=[OkApp(mount="")])
        assert chain_names(server)[0] == "SecurityHeadersMiddleware"
        headers = response_headers(await http_request(server, "/"))
        assert headers[b"x-content-type-options"] == b"nosniff"

    async def test_error_responses_carry_the_headers(
        self, http_request, response_status, response_headers
    ) -> None:
        server = MwServer(applications=[OkApp(mount="")])
        sent = await http_request(server, "/missing")
        assert response_status(sent) == 404
        assert response_headers(sent)[b"x-frame-options"] == b"SAMEORIGIN"

    async def test_switched_off(self, http_request, response_headers) -> None:
        server = MwServer(applications=[OkApp(mount="")], middleware={"security_headers": False})
        assert server.get_middleware(SecurityHeadersMiddleware) is None
        assert b"x-content-type-options" not in response_headers(await http_request(server, "/"))

    async def test_options_through_kwarg(self, http_request, response_headers) -> None:
        server = MwServer(
            applications=[OkApp(mount="")],
            middleware={"security_headers": {"content_security_policy": "default-src 'self'"}},
        )
        headers = response_headers(await http_request(server, "/"))
        assert headers[b"content-security-policy"] == b"default-src 'self'"

    def test_options_through_the_recipe(self) -> None:
        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.middleware(security_headers={"x_frame_options": "DENY"})
                cfg.applications().application(code="ok", mount="", app_class=OkApp)

        server = AsgiServer(config=Recipe)
        layer = server.get_middleware(SecurityHeadersMiddleware)
        assert layer is not None
        assert (b"x-frame-options", b"DENY") in layer.security_headers

    def test_switched_off_through_the_recipe(self) -> None:
        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.middleware(security_headers=False)
                cfg.applications().application(code="ok", mount="", app_class=OkApp)

        assert AsgiServer(config=Recipe).get_middleware(SecurityHeadersMiddleware) is None
