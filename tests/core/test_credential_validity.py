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

"""The credential cache never outlives the credential.

``server.authenticate_credential(credential, channel)`` caches the avatar
``/_auth/authenticate`` answered. The answer reports ``valid_until`` (a POSIX
timestamp) when the credential carries an expiry — the ``expires_at`` of a
token — and the cache entry ends at the earlier of the TTL and that instant.
An answer that reports no ``valid_until`` keeps the TTL alone. ``Idp`` is a
subclass of ``AuthApplicationBase`` whose ``verify_credential`` answers.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from kajenn import AsgiServer, AuthApplicationBase, BaseApplication
from kajenn.exceptions import HTTPUnauthorized
from tests.conftest import start_server
from tests.storage_support import MemoryAuthStore, add_user, issue_token


class Idp(AuthApplicationBase):
    """``verify_credential`` answers ``Bearer good``; ``valid_until`` is configurable."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []
        self.valid_until: float | None = None

    def verify_credential(self, credential: str) -> dict[str, Any]:
        self.calls.append(credential)
        if credential != "Bearer good":
            return super().verify_credential(credential)
        return {
            "identity": "ann",
            "group": None,
            "tags": ["dev"],
            "data": {},
            "valid_until": self.valid_until,
        }


def make_server() -> AsgiServer:
    return AsgiServer(
        applications=[
            (Idp, {"store": {"store_class": MemoryAuthStore}, "cache_ttl": 300.0}),
            (BaseApplication, {"mount": ""}),
        ],
    )


async def test_an_expired_api_key_is_refused_even_when_cached() -> None:
    # wf:contract: a token issued with expires_at one second ahead is authenticated and
    # wf:contract: cached under a long TTL; once expires_at has passed,
    # wf:contract: authenticate_credential refuses it with HTTPUnauthorized.
    server = make_server()
    await start_server(server)
    add_user(server, "ci-deploy", ["admin"], system=True)
    key = issue_token(server, "ci-deploy", ["admin"], expires_at=time.time() + 1)
    credential = f"Bearer {key}"
    avatar = await server.authenticate_credential(credential, "api")
    assert avatar.identity == "ci-deploy"
    await asyncio.sleep(1.5)
    with pytest.raises(HTTPUnauthorized):
        await server.authenticate_credential(credential, "api")


async def test_an_answer_reporting_valid_until_bounds_the_cache() -> None:
    # wf:contract: an answer {identity, group, tags, data, valid_until} with valid_until one
    # wf:contract: second ahead is asked again after that instant although the TTL has not
    # wf:contract: elapsed: the cache entry ends at valid_until.
    server = make_server()
    idp: Idp = server.applications["_auth"]
    idp.valid_until = time.time() + 1
    await start_server(server)
    await server.authenticate_credential("Bearer good", "mcp")
    await server.authenticate_credential("Bearer good", "mcp")
    assert len(idp.calls) == 1
    await asyncio.sleep(1.5)
    await server.authenticate_credential("Bearer good", "mcp")
    assert len(idp.calls) == 2


async def test_an_answer_without_valid_until_keeps_the_ttl() -> None:
    # wf:contract: an answer whose valid_until is None is asked once: the second
    # wf:contract: authentication 1.5 s later is served by the cache (TTL 300 s).
    server = make_server()
    idp: Idp = server.applications["_auth"]
    await start_server(server)
    await server.authenticate_credential("Bearer good", "mcp")
    await asyncio.sleep(1.5)
    await server.authenticate_credential("Bearer good", "mcp")
    assert len(idp.calls) == 1
