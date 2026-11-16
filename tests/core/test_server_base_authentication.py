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

"""The server's base authentication: ``authenticate_credential`` and its cache.

``server.authenticate_credential(credential, channel)`` asks
``/_auth/authenticate`` of the application mounted at ``/_auth`` and caches the
avatar by TTL; ``server.forget_credential`` evicts it. ``Idp`` is a subclass of
``AuthApplicationBase`` whose ``verify_credential`` answers ``Bearer good``.
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn import AsgiServer, AuthApplicationBase, Avatar, BaseApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.exceptions import HTTPUnauthorized
from tests.conftest import start_server
from tests.storage_support import MemoryAuthStore


class Idp(AuthApplicationBase):
    """An authentication application whose verification answers ``Bearer good``."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    def verify_credential(self, credential: str) -> dict[str, Any]:
        self.calls.append(credential)
        if credential != "Bearer good":
            raise HTTPUnauthorized("unknown token")
        return {
            "identity": "ann",
            "group": "dev",
            "tags": ["dev"],
            "data": {"allowed_repository": ["r1"]},
            "valid_until": None,
        }


def server_with_idp(ttl: float | None = None) -> AsgiServer:
    params: dict[str, Any] = {"store": {"store_class": MemoryAuthStore}}
    if ttl is not None:
        params["cache_ttl"] = ttl
    return AsgiServer(applications=[(Idp, params), (BaseApplication, {"mount": ""})])


def idp(server: AsgiServer) -> Idp:
    return server.applications["_auth"]


class TestAuthenticateCredential:
    async def test_the_answer_is_an_avatar_with_group_and_data(self) -> None:
        # wf:contract: authenticate_credential(credential, channel) asks /_auth/authenticate
        # wf:contract: and builds the Avatar, group and data included.
        server = server_with_idp()
        await start_server(server)
        avatar = await server.authenticate_credential("Bearer good", "mcp")
        assert isinstance(avatar, Avatar)
        assert (avatar.identity, avatar.group, avatar.tags) == ("ann", "dev", ["dev"])
        assert avatar.data["allowed_repository"] == ["r1"]
        assert idp(server).calls == ["Bearer good"]

    async def test_a_refusal_is_401_and_not_cached(self) -> None:
        # wf:contract: a 401 from /_auth/authenticate is HTTPUnauthorized for the caller and
        # wf:contract: the next call asks again.
        server = server_with_idp()
        for _ in range(2):
            with pytest.raises(HTTPUnauthorized):
                await start_server(server)
                await server.authenticate_credential("Bearer bad", "mcp")
        assert len(idp(server).calls) == 2

    async def test_the_cache_answers_the_second_call(self) -> None:
        # wf:contract: a verified credential is cached by (sha256(credential), channel):
        # wf:contract: two calls reach /_auth/authenticate once. Each call gives its own
        # wf:contract: Avatar object with the same identity, group and tags.
        server = server_with_idp()
        await start_server(server)
        first = await server.authenticate_credential("Bearer good", "mcp")
        await start_server(server)
        second = await server.authenticate_credential("Bearer good", "mcp")
        assert first is not second
        assert (first.identity, first.group, first.tags) == (
            second.identity,
            second.group,
            second.tags,
        )
        assert len(idp(server).calls) == 1

    async def test_the_cache_is_per_channel(self) -> None:
        # wf:contract: the same credential on another channel is asked again: the cache key
        # wf:contract: carries the channel.
        server = server_with_idp()
        await start_server(server)
        await server.authenticate_credential("Bearer good", "api")
        await server.authenticate_credential("Bearer good", "mcp")
        assert len(idp(server).calls) == 2

    async def test_forget_credential_evicts(self) -> None:
        # wf:contract: forget_credential(credential) drops the entries of that credential
        # wf:contract: for every channel; the next call asks /_auth/authenticate again.
        server = server_with_idp()
        await start_server(server)
        await server.authenticate_credential("Bearer good", "mcp")
        server.forget_credential("Bearer good")
        await start_server(server)
        await server.authenticate_credential("Bearer good", "mcp")
        assert len(idp(server).calls) == 2

    async def test_a_zero_ttl_disables_the_cache(self) -> None:
        # wf:contract: cache_ttl=0 on the _auth application makes every call reach
        # wf:contract: /_auth/authenticate.
        server = server_with_idp(ttl=0)
        await start_server(server)
        await server.authenticate_credential("Bearer good", "mcp")
        await start_server(server)
        await server.authenticate_credential("Bearer good", "mcp")
        assert len(idp(server).calls) == 2

    def test_cache_ttl_comes_from_the_grammar(self) -> None:
        # wf:contract: application(code="_auth", ..., cache_ttl=<seconds>) reaches the server
        # wf:contract: as credential_cache_ttl; the default is 300.

        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=0)
                apps = cfg.applications(default="root")
                apps.application(code="root", mount="", app_class=BaseApplication)
                apps.application(code="_auth", app_class=Idp, cache_ttl=7)

        assert AsgiServer(config=Recipe).credential_cache_ttl == 7
        assert server_with_idp().credential_cache_ttl == 300
