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

An external application authenticates on its own face, through the
``/_auth/authenticate`` of the server process over the bus, and keeps its own
credential cache. ``/_auth/tokens/revoke``, ``/_auth/tokens/delete`` and
``/_auth/forget_credential`` empty the server's cache and the cache of every
joined process before they answer. A process that does not confirm is
relaunched, so it comes back with an empty cache. Driven end to end on the
bench of ``tests/core/test_external_application.py``: HTTP through the mount
and ``server.kbus_call``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from kajenn import Avatar, AsgiServer
from kajenn import kbus_mixin
from kajenn.exceptions import KBusCallError
from tests.storage_support import add_user, issue_token

from .test_external_application import answering, running
from .test_external_parity import bearer, call

RECIPE = '''
import os
import time
from typing import Any

from genro_routes import route

from kajenn import AuthApplicationBase, RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.storage_support import MemoryAuthStore


class Billing(RoutedApplication):
    @route()
    def prepared_state(self):
        return {"prepared": True, "pid": os.getpid()}

    @route(auth_rule="admin")
    def secret(self, _request=None):
        return {"identity": _request.avatar().identity}

    @route()
    async def freeze(self, marker: str = ""):
        with open(marker, "w"):
            pass
        time.sleep(60)
        return {}


class Local(RoutedApplication):
    @route(auth_rule="admin")
    def secret(self, _request=None):
        return {"identity": _request.avatar().identity}


class Idp(AuthApplicationBase):
    """Verifies ``Bearer good`` itself and counts every verification."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    def verify_credential(self, credential):
        self.calls.append(credential)
        if credential != "Bearer good":
            return super().verify_credential(credential)
        return {"identity": "ann", "group": None, "tags": ["admin"], "data": {},
                "valid_until": None}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def main(self, root):
        cfg = root.configuration()
        self.site_section(cfg)
        SERVER
        self.storage_section(cfg)
        self.applications_section(cfg)

    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess", request_timeout=5)
        apps.application(code="local", app_class=Local)
        apps.application(code="_auth", app_class=AUTH_CLASS).store(store_class=MemoryAuthStore)
'''

AUTH_ADMIN = Avatar("root", ["AUTH_ADMIN"])


SHORT_SHUTDOWN = 'cfg.server(shutdown_timeout_seconds=1)'


def write_recipe(tmp_path, *, auth_class: str = "AuthApplicationBase",
                 server: str = "self.server_section(cfg)") -> str:
    path = tmp_path / "config.py"
    path.write_text(RECIPE.replace("AUTH_CLASS", auth_class).replace("SERVER", server))
    return str(path)


def audit_key(server: AsgiServer) -> tuple[str, str]:
    """A token of the system user audit (tag admin) and its key_id."""
    add_user(server, "audit", ["admin"], system=True)
    key = issue_token(server, "audit", ["admin"])
    return key, key[len("gak_"):].partition("_")[0]


async def status_of(server: AsgiServer, path: str, headers) -> int:
    status, _, _ = await call(server, path, headers=headers)
    return status


async def test_a_revoked_api_key_is_refused_by_the_external_application_at_once(tmp_path) -> None:
    # wf:contract: an api key with the admin tag answers 200 on /billing/secret (external) and
    # wf:contract: on /local/secret (in-process); after server.kbus_call("/_auth/tokens/revoke",
    # wf:contract: {"key_id": ...}) answered {"revoked": True}, the very next request with the
    # wf:contract: same key answers 401 on both.
    async with running(write_recipe(tmp_path)) as server:
        key, key_id = audit_key(server)
        headers = bearer(key)
        assert await status_of(server, "/billing/secret", headers) == 200
        assert await status_of(server, "/local/secret", headers) == 200
        answer = await server.kbus_call("/_auth/tokens/revoke", {"key_id": key_id}, auth=AUTH_ADMIN)
        assert answer["revoked"] is True
        assert await status_of(server, "/local/secret", headers) == 401
        assert await status_of(server, "/billing/secret", headers) == 401


async def test_a_deleted_api_key_is_refused_by_the_external_application_at_once(tmp_path) -> None:
    # wf:contract: same as revoke, through server.kbus_call("/_auth/tokens/delete",
    # wf:contract: {"key_id": ...}) answering {"deleted": True}: the next request with the key
    # wf:contract: answers 401 in-process and external.
    async with running(write_recipe(tmp_path)) as server:
        key, key_id = audit_key(server)
        headers = bearer(key)
        assert await status_of(server, "/billing/secret", headers) == 200
        answer = await server.kbus_call("/_auth/tokens/delete", {"key_id": key_id}, auth=AUTH_ADMIN)
        assert answer["deleted"] is True
        assert await status_of(server, "/local/secret", headers) == 401
        assert await status_of(server, "/billing/secret", headers) == 401


async def test_a_banned_user_loses_token_and_session_in_the_external_application(
        tmp_path) -> None:
    # wf:contract: a token of audit and a session of ann open /billing/secret (external);
    # wf:contract: both users banned in the store, after server.forget_credentials_everywhere()
    # wf:contract: the token answers 401, the session answers 401 and is gone from the store.
    async with running(write_recipe(tmp_path)) as server:
        key, _ = audit_key(server)
        add_user(server, "ann", ["admin"])
        session = server.session_store.create(Avatar("ann"))
        cookie = [(b"cookie", f"session_id={session.id}".encode())]
        assert await status_of(server, "/billing/secret", bearer(key)) == 200
        assert await status_of(server, "/billing/secret", cookie) == 200
        add_user(server, "audit", ["admin"], system=True, status="banned")
        add_user(server, "ann", ["admin"], status="banned")
        await server.forget_credentials_everywhere()
        assert await status_of(server, "/billing/secret", bearer(key)) == 401
        assert await status_of(server, "/billing/secret", cookie) == 401
        assert server.session_store.get(session.id) is None


async def test_forget_credential_evicts_the_cache_of_the_external_process(tmp_path) -> None:
    # wf:contract: with _auth an Idp subclass verifying in the server process, two requests
    # wf:contract: with the same credential on /billing/secret cost ONE verification (the
    # wf:contract: external process caches); after server.kbus_call("/_auth/forget_credential",
    # wf:contract: {"credential": "Bearer good"}) the next request costs a second verification.
    async with running(write_recipe(tmp_path, auth_class="Idp")) as server:
        idp = server.applications["_auth"]
        headers = bearer("good")
        assert await status_of(server, "/billing/secret", headers) == 200
        assert await status_of(server, "/billing/secret", headers) == 200
        assert idp.calls == ["Bearer good"]
        await server.kbus_call("/_auth/forget_credential", {"credential": "Bearer good"})
        assert await status_of(server, "/billing/secret", headers) == 200
        assert idp.calls == ["Bearer good", "Bearer good"]


async def test_a_process_that_does_not_confirm_the_revocation_is_relaunched(
        tmp_path, monkeypatch) -> None:
    # wf:contract: billing's event loop is blocked, so it cannot confirm forget_credentials
    # wf:contract: within FORGET_CREDENTIALS_TIMEOUT; /_auth/tokens/revoke still answers
    # wf:contract: {"revoked": True}, billing comes back as a new process, and the revoked
    # wf:contract: key answers 401 through the mount.
    monkeypatch.setattr(kbus_mixin, "FORGET_CREDENTIALS_TIMEOUT", 1.0)
    async with running(write_recipe(tmp_path, server=SHORT_SHUTDOWN)) as server:
        key, key_id = audit_key(server)
        headers = bearer(key)
        assert await status_of(server, "/billing/secret", headers) == 200
        before = await answering(server)
        marker = tmp_path / "frozen"
        frozen = asyncio.create_task(server.kbus_call("/billing/freeze", {"marker": str(marker)}))
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline, "billing never entered freeze"
            await asyncio.sleep(0.05)
        answer = await server.kbus_call("/_auth/tokens/revoke", {"key_id": key_id}, auth=AUTH_ADMIN)
        assert answer["revoked"] is True
        await answering(server, other_than=before["pid"])
        assert await status_of(server, "/billing/secret", headers) == 401
        with pytest.raises(KBusCallError):
            await frozen
