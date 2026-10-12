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

"""Revoking a credential reaches the credential cache of every process.

An external application authenticates on its own face, through the server's
authentication route over the bus, and keeps its own credential cache. The
server's ``/_server/tokens/revoke``, ``/_server/tokens/delete`` and
``/_server/auth/forget_credential`` empty the server's cache and the cache of
every joined process before they answer. Driven end to end on the bench of
``tests/core/test_external_application.py``: HTTP through the mount and
``server.kbus_call``.
"""

from __future__ import annotations

from kajenn import Avatar, AsgiServer

from .test_external_application import running
from .test_external_parity import bearer, call

RECIPE = '''
import os
from typing import Any

from genro_routes import route

from kajenn import ApiKeyStore, RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from kajenn.exceptions import HTTPUnauthorized


class MemoryApiKeyStore(ApiKeyStore):
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


class Billing(RoutedApplication):
    @route()
    def prepared_state(self):
        return {"prepared": True, "pid": os.getpid()}

    @route(auth_rule="admin")
    def secret(self, _request=None):
        return {"identity": _request.avatar().identity}


class Local(RoutedApplication):
    @route(auth_rule="admin")
    def secret(self, _request=None):
        return {"identity": _request.avatar().identity}


class Idp(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    @route()
    def check(self, credential: str = "", channel: str = ""):
        self.calls.append(credential)
        if credential != "Bearer good":
            raise HTTPUnauthorized("unknown token")
        return {"identity": "ann", "tags": ["admin"], "data": {}}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def main(self, root):
        cfg = root.configuration()
        self.site_section(cfg)
        self.server_section(cfg)
        self.storage_section(cfg)
        self.applications_section(cfg)
        cfg.authentication().tokens(store_class=MemoryApiKeyStore)
        CHANNELS

    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess", request_timeout=5)
        apps.application(code="local", app_class=Local)
        apps.application(code="idp", app_class=Idp)
'''

REST_CHANNEL = 'cfg.channels().channel(name="rest", authentication_route="/idp/check")'
SUPERADMIN = Avatar("root", ["SUPERADMIN"])


def write_recipe(tmp_path, *, channels: str = "pass") -> str:
    path = tmp_path / "config.py"
    path.write_text(RECIPE.replace("CHANNELS", channels))
    return str(path)


async def status_of(server: AsgiServer, path: str, headers) -> int:
    status, _, _ = await call(server, path, headers=headers)
    return status


async def test_a_revoked_api_key_is_refused_by_the_external_application_at_once(tmp_path) -> None:
    # wf:contract: an api key with the admin tag answers 200 on /billing/secret (external) and
    # wf:contract: on /local/secret (in-process); after server.kbus_call("/_server/tokens/revoke",
    # wf:contract: {"key_id": ...}) answered {"revoked": True}, the very next request with the
    # wf:contract: same key answers 401 on both.
    async with running(write_recipe(tmp_path)) as server:
        key = server.api_key_store.issue("audit", ["admin"])
        key_id = server.api_key_store.load_all()[0]["key_id"]
        headers = bearer(key)
        assert await status_of(server, "/billing/secret", headers) == 200
        assert await status_of(server, "/local/secret", headers) == 200
        answer = await server.kbus_call("/_server/tokens/revoke", {"key_id": key_id}, auth=SUPERADMIN)
        assert answer["revoked"] is True
        assert await status_of(server, "/local/secret", headers) == 401
        assert await status_of(server, "/billing/secret", headers) == 401


async def test_a_deleted_api_key_is_refused_by_the_external_application_at_once(tmp_path) -> None:
    # wf:contract: same as revoke, through server.kbus_call("/_server/tokens/delete",
    # wf:contract: {"key_id": ...}) answering {"deleted": True}: the next request with the key
    # wf:contract: answers 401 in-process and external.
    async with running(write_recipe(tmp_path)) as server:
        key = server.api_key_store.issue("audit", ["admin"])
        key_id = server.api_key_store.load_all()[0]["key_id"]
        headers = bearer(key)
        assert await status_of(server, "/billing/secret", headers) == 200
        answer = await server.kbus_call("/_server/tokens/delete", {"key_id": key_id}, auth=SUPERADMIN)
        assert answer["deleted"] is True
        assert await status_of(server, "/local/secret", headers) == 401
        assert await status_of(server, "/billing/secret", headers) == 401


async def test_forget_credential_evicts_the_cache_of_the_external_process(tmp_path) -> None:
    # wf:contract: with the rest channel verified by the in-process Idp, two requests with the
    # wf:contract: same credential on /billing/secret cost ONE Idp call (the external process
    # wf:contract: caches); after server.kbus_call("/_server/auth/forget_credential",
    # wf:contract: {"credential": "Bearer good"}) the next request costs a second Idp call.
    async with running(write_recipe(tmp_path, channels=REST_CHANNEL)) as server:
        idp = server.applications["idp"]
        headers = bearer("good")
        assert await status_of(server, "/billing/secret", headers) == 200
        assert await status_of(server, "/billing/secret", headers) == 200
        assert idp.calls == ["Bearer good"]
        await server.kbus_call("/_server/auth/forget_credential", {"credential": "Bearer good"})
        assert await status_of(server, "/billing/secret", headers) == 200
        assert idp.calls == ["Bearer good", "Bearer good"]
