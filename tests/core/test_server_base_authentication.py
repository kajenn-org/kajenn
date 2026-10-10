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

"""The server's base authentication and the ``_server`` routes that expose it.

``AuthCore.verify(credential)`` turns an ``Authorization`` value into an
``Avatar``; ``server.authenticate_credential(credential, channel)`` asks the
channel's ``authentication_route`` — ``/_server/auth/authenticate`` by default,
backed by ``AuthCore`` — and caches the avatar by TTL;
``server.forget_credential`` evicts it. Nothing on the request path calls these
yet: that is Phase 4.
"""

from __future__ import annotations

from typing import Any

import pytest

from genro_routes import route

from kajenn import AsgiServer, AuthCore, Avatar, BaseApplication, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.exceptions import HTTPUnauthorized
from kajenn.kbus import KBusCallError
from kajenn.server_app import ServerApplication

BEARER = {"bearer": {"svc": {"token": "sk_live_xyz", "tags": "api"}}}


class Idp(RoutedApplication):
    """An application acting as the authentication route of the ``mcp`` channel."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[dict[str, Any]] = []

    @route()
    def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
        self.calls.append({"credential": credential, "channel": channel})
        if credential != "Bearer good":
            raise HTTPUnauthorized("unknown token")
        return {"identity": "ann", "tags": ["dev"], "data": {"allowed_repository": ["r1"]}}


def server_with_idp(ttl: float | None = None) -> AsgiServer:
    kwargs: dict[str, Any] = {
        "applications": [
            ServerApplication,
            (BaseApplication, {"mount": ""}),
            (Idp, {"code": "idp"}),
        ],
        "auth": BEARER,
        "channels": {"mcp": {"authentication_route": "/idp/check"}},
    }
    if ttl is not None:
        kwargs["credential_cache_ttl"] = ttl
    return AsgiServer(**kwargs)


def idp(server: AsgiServer) -> Idp:
    return server.applications["idp"]


class TestAuthCoreVerify:
    def test_a_valid_bearer_is_an_avatar(self) -> None:
        # wf:contract: AuthCore.verify("<scheme> <value>") returns the Avatar of a valid
        # wf:contract: credential; authenticate(scope) still answers None without header.
        core = AuthCore(**BEARER)
        avatar = core.verify("Bearer sk_live_xyz")
        assert (avatar.identity, avatar.tags) == ("svc", ["api"])
        assert core.authenticate({"type": "http", "headers": []}) is None

    def test_an_invalid_or_malformed_credential_is_401(self) -> None:
        # wf:contract: verify raises HTTPUnauthorized with a WWW-Authenticate challenge
        # wf:contract: on an unknown value and on a value without a scheme.
        core = AuthCore(**BEARER)
        with pytest.raises(HTTPUnauthorized) as refused:
            core.verify("Bearer nope")
        assert (b"www-authenticate", b"Bearer") in refused.value.headers
        with pytest.raises(HTTPUnauthorized):
            core.verify("noscheme")


class TestTheServerRoutes:
    async def test_the_default_route_verifies_with_auth_core(self) -> None:
        # wf:contract: /_server/auth/authenticate exists on every server declaring _server
        # wf:contract: and answers {identity, tags, data} for a credential AuthCore knows.
        server = server_with_idp()
        answer = await server.kbus_call(
            "/_server/auth/authenticate", {"credential": "Bearer sk_live_xyz", "channel": "rest"}
        )
        assert answer == {"identity": "svc", "tags": ["api"], "data": {}}

    async def test_the_default_route_refuses_with_401(self) -> None:
        # wf:contract: an invalid credential on /_server/auth/authenticate is a 401 for
        # wf:contract: the bus caller.
        server = server_with_idp()
        with pytest.raises(KBusCallError) as refused:
            await server.kbus_call(
                "/_server/auth/authenticate", {"credential": "Bearer nope", "channel": "rest"}
            )
        assert refused.value.status == 401

    async def test_forget_credential_route_answers_ok(self) -> None:
        # wf:contract: /_server/auth/forget_credential drops the cached avatar of the
        # wf:contract: credential and answers {"status": "ok"}.
        server = server_with_idp()
        await server.authenticate_credential("Bearer sk_live_xyz", "rest")
        answer = await server.kbus_call(
            "/_server/auth/forget_credential", {"credential": "Bearer sk_live_xyz"}
        )
        assert answer == {"status": "ok"}


class TestAuthenticateCredential:
    def test_the_route_of_a_channel(self) -> None:
        # wf:contract: server.authentication_route(channel) is the configured route of
        # wf:contract: the channel, or DEFAULT_AUTHENTICATION_ROUTE.
        server = server_with_idp()
        assert server.authentication_route("mcp") == "/idp/check"
        assert server.authentication_route("rest") == "/_server/auth/authenticate"
        assert server.DEFAULT_AUTHENTICATION_ROUTE == "/_server/auth/authenticate"

    async def test_a_channel_route_answers_an_avatar_with_data(self) -> None:
        # wf:contract: authenticate_credential(credential, channel) calls the channel's
        # wf:contract: route with {credential, channel} and builds the Avatar, data included.
        server = server_with_idp()
        avatar = await server.authenticate_credential("Bearer good", "mcp")
        assert isinstance(avatar, Avatar)
        assert (avatar.identity, avatar.tags) == ("ann", ["dev"])
        assert avatar.data["allowed_repository"] == ["r1"]
        assert idp(server).calls == [{"credential": "Bearer good", "channel": "mcp"}]

    async def test_a_refusal_is_401_and_not_cached(self) -> None:
        # wf:contract: a 401 from the route is HTTPUnauthorized for the caller and the
        # wf:contract: next call asks the route again.
        server = server_with_idp()
        for _ in range(2):
            with pytest.raises(HTTPUnauthorized):
                await server.authenticate_credential("Bearer bad", "mcp")
        assert len(idp(server).calls) == 2

    async def test_the_cache_answers_the_second_call(self) -> None:
        # wf:contract: a verified credential is cached by (sha256(credential), channel):
        # wf:contract: two calls reach the route once.
        server = server_with_idp()
        first = await server.authenticate_credential("Bearer good", "mcp")
        second = await server.authenticate_credential("Bearer good", "mcp")
        assert first is second
        assert len(idp(server).calls) == 1

    async def test_the_cache_is_per_channel(self) -> None:
        # wf:contract: the same credential on another channel is verified by that
        # wf:contract: channel's route: the cache key carries the channel.
        server = server_with_idp()
        await server.authenticate_credential("Bearer sk_live_xyz", "rest")
        with pytest.raises(HTTPUnauthorized):
            await server.authenticate_credential("Bearer sk_live_xyz", "mcp")
        assert len(idp(server).calls) == 1

    async def test_forget_credential_evicts(self) -> None:
        # wf:contract: forget_credential(credential) drops the entries of that credential
        # wf:contract: for every channel; the next call reaches the route again.
        server = server_with_idp()
        await server.authenticate_credential("Bearer good", "mcp")
        server.forget_credential("Bearer good")
        await server.authenticate_credential("Bearer good", "mcp")
        assert len(idp(server).calls) == 2

    async def test_a_zero_ttl_disables_the_cache(self) -> None:
        # wf:contract: credential_cache_ttl=0 (grammar: authentication(cache_ttl=0))
        # wf:contract: makes every call reach the route.
        server = server_with_idp(ttl=0)
        await server.authenticate_credential("Bearer good", "mcp")
        await server.authenticate_credential("Bearer good", "mcp")
        assert len(idp(server).calls) == 2

    def test_cache_ttl_comes_from_the_grammar(self) -> None:
        # wf:contract: configuration.authentication(cache_ttl=<seconds>) reaches the server
        # wf:contract: as credential_cache_ttl; the default is 300.

        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=0)
                cfg.authentication(cache_ttl=7)
                apps = cfg.applications(default="root")
                apps.application(code="root", mount="", app_class=BaseApplication)

        assert AsgiServer(config=Recipe).credential_cache_ttl == 7
        assert server_with_idp().credential_cache_ttl == 300
