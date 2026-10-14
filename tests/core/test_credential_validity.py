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

``server.authenticate_credential(credential, channel)`` caches the avatar the
channel's route answered. The route reports ``valid_until`` (a POSIX timestamp)
when the credential carries an expiry — the ``exp`` of a JWT, the ``expires_at``
of an api key — and the cache entry ends at the earlier of the TTL and that
instant. A route that reports no ``valid_until`` keeps the TTL alone.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import jwt
import pytest

from genro_routes import route

from kajenn import ApiKeyStore, AsgiServer, BaseApplication, RoutedApplication
from kajenn.exceptions import HTTPUnauthorized
from kajenn.server_app import ServerApplication

JWT_SECRET = "sign-me-please"


class MemoryApiKeyStore(ApiKeyStore):
    """In-memory ``ApiKeyStore`` backend: the shared issue/verify logic over a dict."""

    __slots__ = ("_records",)

    def __init__(self, storage: object = None) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def load_all(self) -> list[dict[str, Any]]:
        return list(self._records.values())

    def get(self, key_id: str) -> dict[str, Any] | None:
        return self._records.get(key_id)

    def save(self, record: dict[str, Any]) -> None:
        self._records[record["key_id"]] = record

    def delete(self, key_id: str) -> bool:
        return self._records.pop(key_id, None) is not None


class Idp(RoutedApplication):
    """The authentication route of the ``mcp`` channel; ``valid_until`` is configurable."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []
        self.valid_until: float | None = None

    @route()
    def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
        self.calls.append(credential)
        if credential != "Bearer good":
            raise HTTPUnauthorized("unknown token")
        answer: dict[str, Any] = {"identity": "ann", "tags": ["dev"], "data": {}}
        if self.valid_until is not None:
            answer["valid_until"] = self.valid_until
        return answer


def make_server() -> AsgiServer:
    return AsgiServer(
        applications=[ServerApplication, (BaseApplication, {"mount": ""}), (Idp, {"code": "idp"})],
        auth={"jwt": [{"secret": JWT_SECRET, "algorithm": "HS256"}]},
        tokens={"store_class": MemoryApiKeyStore},
        channels={"mcp": {"authentication_route": "/idp/check"}},
        credential_cache_ttl=300.0,
    )


async def test_an_expired_jwt_is_refused_even_when_cached() -> None:
    # wf:contract: a JWT whose exp lies one to two seconds ahead is authenticated and cached under a
    # wf:contract: long TTL; once exp has passed, authenticate_credential refuses it with
    # wf:contract: HTTPUnauthorized instead of answering the cached avatar.
    server = make_server()
    token = jwt.encode({"sub": "carol", "exp": int(time.time()) + 2}, JWT_SECRET, algorithm="HS256")
    credential = f"Bearer {token}"
    avatar = await server.authenticate_credential(credential, "rest")
    assert avatar.identity == "carol"
    await asyncio.sleep(2.5)
    with pytest.raises(HTTPUnauthorized):
        await server.authenticate_credential(credential, "rest")


async def judged_like_pyjwt(exp: Any, expires: int) -> None:
    """Authenticate a JWT carrying ``exp``, then check it just past ``expires``.

    ``expires`` is the instant PyJWT reads in ``exp``: at that moment PyJWT
    refuses the token, and the cache refuses it as well.
    """
    server = make_server()
    token = jwt.encode({"sub": "carol", "exp": exp}, JWT_SECRET, algorithm="HS256")
    credential = f"Bearer {token}"
    avatar = await server.authenticate_credential(credential, "rest")
    assert avatar.identity == "carol"
    await asyncio.sleep(expires + 0.2 - time.time())
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    with pytest.raises(HTTPUnauthorized):
        await server.authenticate_credential(credential, "rest")


async def test_a_jwt_with_a_string_exp_is_cached_until_pyjwt_expires_it() -> None:
    # wf:contract: a JWT whose exp is the string of an instant three seconds ahead is
    # wf:contract: authenticated and cached; just past that instant PyJWT refuses it and
    # wf:contract: authenticate_credential refuses it with HTTPUnauthorized.
    expires = int(time.time()) + 3
    await judged_like_pyjwt(str(expires), expires)


async def test_a_jwt_with_a_fractional_exp_is_cached_until_pyjwt_expires_it() -> None:
    # wf:contract: a JWT whose exp is an integer instant three seconds ahead plus 0.9 is
    # wf:contract: authenticated and cached; 0.2 seconds past the integer instant PyJWT
    # wf:contract: refuses it and authenticate_credential refuses it with HTTPUnauthorized,
    # wf:contract: although the fraction has not elapsed.
    expires = int(time.time()) + 3
    await judged_like_pyjwt(expires + 0.9, expires)


async def test_an_expired_api_key_is_refused_even_when_cached() -> None:
    # wf:contract: an api key issued with expires_at one second ahead is authenticated and
    # wf:contract: cached under a long TTL; once expires_at has passed,
    # wf:contract: authenticate_credential refuses it with HTTPUnauthorized.
    server = make_server()
    key = server.api_key_store.issue("ci-deploy", ["admin"], expires_at=time.time() + 1)
    credential = f"Bearer {key}"
    avatar = await server.authenticate_credential(credential, "rest")
    assert avatar.identity == "ci-deploy"
    await asyncio.sleep(1.5)
    with pytest.raises(HTTPUnauthorized):
        await server.authenticate_credential(credential, "rest")


async def test_a_channel_route_reporting_valid_until_bounds_the_cache() -> None:
    # wf:contract: a channel route answering {identity, tags, data, valid_until} with
    # wf:contract: valid_until one second ahead is called again after that instant although the
    # wf:contract: TTL has not elapsed: the cache entry ends at valid_until.
    server = make_server()
    idp: Idp = server.applications["idp"]
    idp.valid_until = time.time() + 1
    await server.authenticate_credential("Bearer good", "mcp")
    await server.authenticate_credential("Bearer good", "mcp")
    assert len(idp.calls) == 1
    await asyncio.sleep(1.5)
    await server.authenticate_credential("Bearer good", "mcp")
    assert len(idp.calls) == 2


async def test_a_channel_route_without_valid_until_keeps_the_ttl() -> None:
    # wf:contract: a channel route answering {identity, tags, data} with no valid_until is
    # wf:contract: called once: the second authentication 1.5 s later is served by the cache
    # wf:contract: (TTL 300 s).
    server = make_server()
    idp: Idp = server.applications["idp"]
    await server.authenticate_credential("Bearer good", "mcp")
    await asyncio.sleep(1.5)
    await server.authenticate_credential("Bearer good", "mcp")
    assert len(idp.calls) == 1
