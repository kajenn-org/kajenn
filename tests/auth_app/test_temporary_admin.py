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

"""End-to-end tests of the temporary administrator ``_temporary_admin``.

Every test starts a real ``AsgiServer`` with ``AuthApplicationBase`` over a
``FileAuthStore`` in a temporary directory, empty unless the test says
otherwise. A restart is a second server over the same directory and the same
storage key. The tests read what a caller sees: the warning lines of the log,
and the HTTP status and JSON answers of the ``/_auth`` routes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tests.auth_app.test_auth_application import basic, drive, payload, status
from tests.conftest import start_server
from tests.storage_support import MemoryAuthStore, site_mounts

from kajenn import AsgiServer, BaseApplication
from kajenn.auth_app import AuthApplicationBase
from kajenn.types import Message

TEMPORARY = "_temporary_admin"
PASSWORD_LINE = re.compile(rf"temporary administrator {TEMPORARY}, password (\S+)$")


class Site:
    """A site directory and its storage key: every server built from it shares the store."""

    def __init__(self, tmp_path: Path) -> None:
        self.base_dir = tmp_path
        self.storage_key = Fernet.generate_key().decode()

    async def start(self) -> AsgiServer:
        """A started server with ``_auth`` over a ``FileAuthStore`` of this site."""
        server = AsgiServer(
            applications=[(AuthApplicationBase, {}), (BaseApplication, {"mount": ""})],
            storage=site_mounts(self.base_dir),
            storage_key=self.storage_key,
        )
        await start_server(server)
        return server


@pytest.fixture
def site(tmp_path: Path) -> Site:
    return Site(tmp_path)


@pytest.fixture
def log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def password_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The passwords of the temporary administrator in the log, in order."""
    found = []
    for record in caplog.records:
        match = PASSWORD_LINE.search(record.getMessage())
        if match:
            found.append(match.group(1))
    return found


def logged_password(caplog: pytest.LogCaptureFixture) -> str:
    """The last password of the temporary administrator in the log."""
    passwords = password_lines(caplog)
    assert passwords, "no password of the temporary administrator in the log"
    return passwords[-1]


async def login(server: AsgiServer, identity: str, password: str) -> dict[str, Any]:
    answer: dict[str, Any] = payload(
        await drive(
            server, "/_auth/login", "POST", body={"identity": identity, "password": password}
        )
    )
    return answer


async def cookie_of(server: AsgiServer, identity: str, password: str) -> str:
    answer = await login(server, identity, password)
    assert "session_id" in answer, answer
    return f"session_id={answer['session_id']}"


async def post(server: AsgiServer, path: str, cookie: str, body: dict[str, Any]) -> list[Message]:
    return await drive(server, path, "POST", body=body, cookie=cookie)


async def create_user(server: AsgiServer, cookie: str, identity: str, **fields: Any) -> Any:
    body = {"password": "secret", "password_confirm": "secret", **fields}
    return payload(
        await post(server, f"/_auth/users/create_user?identity={identity}", cookie, body)
    )


async def identities(server: AsgiServer, cookie: str) -> list[str]:
    answer = payload(await drive(server, "/_auth/users/list", cookie=cookie))
    return sorted(user["identity"] for user in answer["users"])


class TestFirstStart:
    async def test_the_log_holds_the_password_of_the_temporary_administrator(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        password = logged_password(log)
        answer = await login(server, TEMPORARY, password)
        assert (answer["identity"], answer["tags"]) == (TEMPORARY, ["AUTH_ADMIN"])

    async def test_the_password_is_in_one_log_line_and_in_no_answer(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        password = logged_password(log)
        assert sum(password in record.getMessage() for record in log.records) == 1
        cookie = await cookie_of(server, TEMPORARY, password)
        answers = [
            await drive(server, "/_auth/users/list", cookie=cookie),
            await drive(server, f"/_auth/users/get?identity={TEMPORARY}", cookie=cookie),
            await drive(server, "/_auth/login_methods"),
            await drive(
                server,
                "/_auth/login",
                "POST",
                body={"identity": TEMPORARY, "password": password},
            ),
        ]
        for sent in answers:
            body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
            assert password.encode() not in body
        record = payload(answers[1])
        assert record["tags"] == ["AUTH_ADMIN"]
        assert (record["status"], record["system"], record["groups"]) == ("confirmed", False, [])
        assert sum(password in record.getMessage() for record in log.records) == 1

    async def test_a_store_that_cannot_be_written_logs_a_warning_and_no_password(
        self, tmp_path: Path, log: pytest.LogCaptureFixture
    ) -> None:
        class Unwritable(MemoryAuthStore):
            def write_user(self, record: dict[str, Any]) -> None:
                raise RuntimeError("the store refuses every user")

        server = AsgiServer(
            applications=[
                (AuthApplicationBase, {"store": {"store_class": Unwritable}}),
                (BaseApplication, {"mount": ""}),
            ]
        )
        await start_server(server)
        assert password_lines(log) == []
        assert any(
            "temporary administrator not written" in record.getMessage() for record in log.records
        )
        assert status(await drive(server, "/_auth/login_methods")) == 200


class TestRealAdministrator:
    async def test_the_temporary_administrator_creates_the_real_one_and_disappears(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        password = logged_password(log)
        temporary = await cookie_of(server, TEMPORARY, password)
        created = await create_user(server, temporary, "boss", tags=["AUTH_ADMIN"])
        assert created["identity"] == "boss"
        boss = await cookie_of(server, "boss", "secret")
        assert await identities(server, boss) == ["boss"]
        assert await login(server, TEMPORARY, password) == {"error": "Invalid credentials"}
        refused = await drive(server, "/_auth/users/list", authorization=basic(TEMPORARY, password))
        assert status(refused) == 401

    async def test_the_session_of_the_temporary_administrator_stops_at_once(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        temporary = await cookie_of(server, TEMPORARY, logged_password(log))
        assert status(await drive(server, "/_auth/users/list", cookie=temporary)) == 200
        await create_user(server, temporary, "boss", tags=["AUTH_ADMIN"])
        assert status(await drive(server, "/_auth/users/list", cookie=temporary)) == 401

    async def test_a_token_of_the_temporary_administrator_stops_at_once(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        temporary = await cookie_of(server, TEMPORARY, logged_password(log))
        issued = payload(
            await post(server, "/_auth/tokens/issue", temporary, {"user": TEMPORARY, "tags": "*"})
        )
        bearer = f"Bearer {issued['key']}"
        assert status(await drive(server, "/_auth/users/list", authorization=bearer)) == 200
        await create_user(server, temporary, "boss", tags=["AUTH_ADMIN"])
        assert status(await drive(server, "/_auth/users/list", authorization=bearer)) == 401

    async def test_an_administrator_through_a_group_counts(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        password = logged_password(log)
        temporary = await cookie_of(server, TEMPORARY, password)
        await post(server, "/_auth/groups/create_group?code=admins", temporary, {"tags": []})
        await create_user(server, temporary, "boss", groups=["admins"], primary_group="admins")
        assert TEMPORARY in await identities(server, temporary)
        saved = payload(
            await post(
                server, "/_auth/groups/save?code=admins", temporary, {"tags": ["AUTH_ADMIN"]}
            )
        )
        assert saved["tags"] == ["AUTH_ADMIN"]
        boss = await cookie_of(server, "boss", "secret")
        assert await identities(server, boss) == ["boss"]
        assert await login(server, TEMPORARY, password) == {"error": "Invalid credentials"}

    async def test_a_banned_or_system_user_with_auth_admin_does_not_count(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        password = logged_password(log)
        temporary = await cookie_of(server, TEMPORARY, password)
        await create_user(server, temporary, "banned", tags=["AUTH_ADMIN"], status="banned")
        await create_user(server, temporary, "robot", tags=["AUTH_ADMIN"], system=True)
        assert await identities(server, temporary) == [TEMPORARY, "banned", "robot"]
        assert (await login(server, TEMPORARY, password))["identity"] == TEMPORARY

    async def test_saving_a_user_into_an_administrator_removes_the_temporary_one(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        server = await site.start()
        password = logged_password(log)
        temporary = await cookie_of(server, TEMPORARY, password)
        await create_user(server, temporary, "boss", status="waiting", tags=["AUTH_ADMIN"])
        assert TEMPORARY in await identities(server, temporary)
        await post(server, "/_auth/users/save?identity=boss", temporary, {"status": "confirmed"})
        assert await login(server, TEMPORARY, password) == {"error": "Invalid credentials"}


class TestRestart:
    async def test_a_restart_gives_a_new_password_and_refuses_the_old_one(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        first_server = await site.start()
        first = logged_password(log)
        for _ in range(5):
            await login(first_server, TEMPORARY, "wrong")
        assert first_server.applications["_auth"].store.get_login_failures(TEMPORARY) is not None
        server = await site.start()
        assert server.applications["_auth"].store.get_login_failures(TEMPORARY) is None
        second = logged_password(log)
        assert password_lines(log) == [first, second]
        assert first != second
        assert await login(server, TEMPORARY, first) == {
            "error": "Invalid credentials",
            "remaining_attempts": 4,
        }
        assert (await login(server, TEMPORARY, second))["identity"] == TEMPORARY

    async def test_a_restart_refuses_the_tokens_of_the_temporary_administrator(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        first = await site.start()
        temporary = await cookie_of(first, TEMPORARY, logged_password(log))
        issued = payload(
            await post(first, "/_auth/tokens/issue", temporary, {"user": TEMPORARY, "tags": "*"})
        )
        bearer = f"Bearer {issued['key']}"
        assert status(await drive(first, "/_auth/users/list", authorization=bearer)) == 200
        second = await site.start()
        assert status(await drive(second, "/_auth/users/list", authorization=bearer)) == 401

    async def test_a_site_with_an_administrator_generates_none(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        first_server = await site.start()
        temporary = await cookie_of(first_server, TEMPORARY, logged_password(log))
        await create_user(first_server, temporary, "boss", tags=["AUTH_ADMIN"])
        log.clear()
        server = await site.start()
        assert password_lines(log) == []
        boss = await cookie_of(server, "boss", "secret")
        assert await identities(server, boss) == ["boss"]

    async def test_an_administrator_written_outside_the_management_ends_it_at_restart(
        self, site: Site, log: pytest.LogCaptureFixture
    ) -> None:
        first_server = await site.start()
        store = first_server.applications["_auth"].store
        store.save_user(
            {
                "identity": "boss",
                "groups": [],
                "primary_group": None,
                "tags": ["AUTH_ADMIN"],
                "status": "confirmed",
                "system": False,
                "email": None,
                "external_ids": [],
                "password_hash": store.hash_password("secret"),
            }
        )
        password = logged_password(log)
        log.clear()
        server = await site.start()
        assert password_lines(log) == []
        assert await login(server, TEMPORARY, password) == {"error": "Invalid credentials"}
        boss = await cookie_of(server, "boss", "secret")
        assert await identities(server, boss) == ["boss"]
