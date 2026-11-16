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

"""Contract tests of ``AuthApplicationBase``, the application mounted at ``/_auth``.

Every test drives a real ``AsgiServer`` at the ASGI level, with the
application mounted over an encrypted ``site:`` mount in a temporary
directory. The server asks the application mounted at ``/_auth``, so an
``Authorization`` header on an HTTP request is verified by the application
through the bus. Users, groups and tags are seeded
through the application's public ``store``; the tests read only what a caller
sees: HTTP status and JSON answers, bus answers and errors, the session's
avatar.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tests.conftest import start_server
from tests.storage_support import site_mounts

from kajenn import AsgiConfigBuilder, AsgiServer, Avatar, BaseApplication
from kajenn.auth_app import AuthApplicationBase, AuthStore, FileAuthStore, PasswordMethod
from kajenn.exceptions import HTTPUnauthorized, KBusCallError
from kajenn.types import Message, Scope


def make_server(
    tmp_path: Path,
    app_class: type = AuthApplicationBase,
    **app_kwargs: Any,
) -> AsgiServer:
    """A server with ``app_class`` mounted at ``/_auth``, the application the server asks."""
    return AsgiServer(
        applications=[(app_class, app_kwargs), (BaseApplication, {"mount": ""})],
        storage=site_mounts(tmp_path),
        storage_key=Fernet.generate_key().decode(),
    )


def auth_store(server: AsgiServer) -> AuthStore:
    """The store of the mounted ``_auth`` application."""
    store: AuthStore = server.applications["_auth"].store
    return store


def seed(server: AsgiServer) -> None:
    """Groups ``staff`` and ``ops``, an admin, alice in both groups, a banned and a system user."""
    store = auth_store(server)
    store.save_group({"code": "staff", "tags": ["STAFF_READ"]})
    store.save_group({"code": "ops", "tags": ["OPS_DEPLOY"]})
    store.save_group({"code": "admins", "tags": ["AUTH_ADMIN"]})
    store.save_user(user(store, "root", "rootpw", groups=["admins"], primary_group="admins"))
    store.save_user(
        user(store, "alice", "wonder", groups=["staff", "ops"], primary_group="staff", tags=["OWN"])
    )
    store.save_user(user(store, "nogroup", "pw", groups=["staff"], tags=["OWN"]))
    store.save_user(user(store, "mallory", "evil", status="banned"))
    store.save_user(user(store, "robot", "botpw", system=True, tags=["AUTH_ADMIN"]))


def user(store: AuthStore, identity: str, password: str, **fields: Any) -> dict[str, Any]:
    """A complete user record with ``password`` hashed; ``fields`` override the defaults."""
    record: dict[str, Any] = {
        "identity": identity,
        "groups": [],
        "primary_group": None,
        "tags": [],
        "status": "confirmed",
        "system": False,
        "password_hash": store.hash_password(password),
        "email": None,
        "external_ids": [],
    }
    record.update(fields)
    return record


async def started(
    tmp_path: Path, app_class: type = AuthApplicationBase, **kwargs: Any
) -> AsgiServer:
    """A started, seeded server."""
    server = make_server(tmp_path, app_class, **kwargs)
    await start_server(server)
    seed(server)
    return server


async def drive(
    server: AsgiServer,
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    cookie: str | None = None,
    authorization: str | None = None,
) -> list[Message]:
    """Drive one request through ``server`` at the ASGI level (JSON body when given)."""
    headers: list[tuple[bytes, bytes]] = []
    raw = b""
    if body is not None:
        headers.append((b"content-type", b"application/json"))
        raw = json.dumps(body).encode()
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    path, _, query = path.partition("?")
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": headers,
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def status(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def payload(sent: list[Message]) -> Any:
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(body)


def response_headers(sent: list[Message]) -> list[tuple[bytes, bytes]]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers: list[tuple[bytes, bytes]] = start["headers"]
    return headers


def set_cookie_value(sent: list[Message]) -> str | None:
    for name, value in response_headers(sent):
        if name == b"set-cookie":
            return value.decode()
    return None


def basic(identity: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{identity}:{password}".encode()).decode()


async def login(
    server: AsgiServer, identity: str, password: str, group: str | None = None
) -> dict[str, Any]:
    """POST ``/_auth/login``; the JSON answer."""
    body = {"identity": identity, "password": password}
    if group is not None:
        body["group"] = group
    answer: dict[str, Any] = payload(await drive(server, "/_auth/login", "POST", body=body))
    return answer


async def session_cookie(server: AsgiServer, identity: str, password: str) -> str:
    """The cookie of a session logged in as ``identity``."""
    answer = await login(server, identity, password)
    return f"session_id={answer['session_id']}"


async def admin(server: AsgiServer) -> str:
    """The cookie of a session logged in as the AUTH_ADMIN user ``root``."""
    return await session_cookie(server, "root", "rootpw")


class Clock:
    """Controllable stand-in for ``time.time``."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestMounting:
    def test_code_and_mount_are_fixed(self) -> None:
        app = AuthApplicationBase()
        assert (app.code, app.mount) == ("_auth", "_auth")

    def test_the_server_never_adds_it_on_its_own(self, tmp_path: Path) -> None:
        server = AsgiServer(applications=[(BaseApplication, {"mount": ""})])
        assert "_auth" not in server.applications

    async def test_mounted_as_is_it_uses_a_file_auth_store(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        assert isinstance(auth_store(server), FileAuthStore)
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"


class TestLogin:
    async def test_password_login_attaches_the_avatar_to_the_session(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        anonymous = server.session_store.create()
        sent = await drive(
            server,
            "/_auth/login",
            "POST",
            body={"identity": "alice", "password": "wonder"},
            cookie=f"session_id={anonymous.id}",
        )
        assert status(sent) == 200
        answer = payload(sent)
        assert answer == {
            "session_id": anonymous.id,
            "identity": "alice",
            "group": "staff",
            "tags": ["OWN", "STAFF_READ"],
            "groups": ["staff", "ops"],
        }
        avatar = server.session_store.get(anonymous.id).avatar()
        assert (avatar.identity, avatar.group, avatar.tags) == (
            "alice",
            "staff",
            ["OWN", "STAFF_READ"],
        )

    async def test_a_group_of_the_user_gives_the_tags_of_that_group(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = await login(server, "alice", "wonder", group="ops")
        assert answer["group"] == "ops"
        assert answer["tags"] == ["OWN", "OPS_DEPLOY"]
        avatar = server.session_store.get(answer["session_id"]).avatar()
        assert (avatar.group, avatar.tags) == ("ops", ["OWN", "OPS_DEPLOY"])

    async def test_without_a_group_the_primary_group_applies(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = await login(server, "alice", "wonder")
        assert (answer["group"], answer["tags"]) == ("staff", ["OWN", "STAFF_READ"])

    async def test_a_user_without_primary_group_gets_only_its_own_tags(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        answer = await login(server, "nogroup", "pw")
        assert (answer["group"], answer["tags"], answer["groups"]) == (None, ["OWN"], ["staff"])

    async def test_a_foreign_group_is_refused_and_attaches_nothing(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        anonymous = server.session_store.create()
        sent = await drive(
            server,
            "/_auth/login",
            "POST",
            body={"identity": "alice", "password": "wonder", "group": "admins"},
            cookie=f"session_id={anonymous.id}",
        )
        assert "error" in payload(sent)
        assert server.session_store.get(anonymous.id).avatar() is None

    async def test_wrong_password_answers_the_error_with_the_counter(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        assert await login(server, "alice", "nope") == {
            "error": "Invalid credentials",
            "remaining_attempts": 4,
        }
        assert await login(server, "ghost", "nope") == {"error": "Invalid credentials"}

    async def test_missing_credentials_answer_the_error(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = payload(await drive(server, "/_auth/login", "POST", body={"identity": "alice"}))
        assert answer == {"error": "Identity and password are required"}

    async def test_banned_and_system_users_never_log_in(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        assert "error" in await login(server, "mallory", "evil")
        assert "error" in await login(server, "robot", "botpw")

    async def test_logout_deletes_the_session(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = await login(server, "alice", "wonder")
        sent = await drive(server, f"/_auth/logout?session_id={answer['session_id']}", "POST")
        assert payload(sent) == {"status": "ok"}
        assert server.session_store.get(answer["session_id"]) is None

    async def test_logout_without_a_session_id_still_answers_ok(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        sent = await drive(server, "/_auth/logout", "POST", body={})
        assert payload(sent) == {"status": "ok"}

    async def test_login_accepts_the_pages_form_encoded_post(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        anonymous = server.session_store.create()
        scope: Scope = {
            "type": "http",
            "method": "POST",
            "path": "/_auth/login",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"cookie", f"session_id={anonymous.id}".encode()),
            ],
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {
                "type": "http.request",
                "body": b"identity=alice&password=wonder",
                "more_body": False,
            }

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)
        answer = payload(sent)
        assert answer["identity"] == "alice"
        assert answer["session_id"] == anonymous.id
        assert set_cookie_value(sent) is None
        assert server.session_store.get(anonymous.id).avatar().identity == "alice"

    async def test_login_on_first_contact_rides_the_new_session_cookie(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        sent = await drive(
            server, "/_auth/login", "POST", body={"identity": "alice", "password": "wonder"}
        )
        answer = payload(sent)
        cookie = set_cookie_value(sent)
        assert cookie is not None
        assert cookie.split(";")[0].split("=", 1)[1] == answer["session_id"]
        assert server.session_store.get(answer["session_id"]).avatar().identity == "alice"

    async def test_get_with_credentials_is_405_and_attaches_no_avatar(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        anonymous = server.session_store.create()
        sent = await drive(
            server,
            "/_auth/login?identity=alice&password=wonder",
            "GET",
            cookie=f"session_id={anonymous.id}",
        )
        assert status(sent) == 405
        assert (b"allow", b"POST") in response_headers(sent)
        assert server.session_store.get(anonymous.id).avatar() is None

    def test_a_second_method_with_the_same_id_is_refused(self, tmp_path: Path) -> None:
        server = make_server(tmp_path)
        application = server.applications["_auth"]
        with pytest.raises(ValueError, match="already registered"):
            application.register_auth_method(PasswordMethod(application, "password"))

    async def test_login_methods_lists_the_password_form(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        sent = await drive(server, "/_auth/login_methods")
        assert status(sent) == 200
        assert payload(sent) == {
            "methods": [
                {"id": "password", "kind": "form", "label": "Sign in", "action": "/_auth/login"}
            ]
        }


class TestLockout:
    @pytest.fixture
    def clock(self, monkeypatch: pytest.MonkeyPatch) -> Clock:
        frozen = Clock()
        monkeypatch.setattr(time, "time", frozen)
        return frozen

    async def test_failed_logins_never_change_the_user(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        before = store.get_user("alice")
        for remaining in (4, 3, 2):
            assert await login(server, "alice", "wrong") == {
                "error": "Invalid credentials",
                "remaining_attempts": remaining,
            }
            assert store.get_user("alice") == before
        assert store.get_login_failures("alice")["failed_attempts"] == 3
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"
        assert store.get_user("alice") == before
        assert store.get_login_failures("alice") is None

    async def test_management_ban_survives_failed_logins(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        cookie = await admin(server)
        await login(server, "alice", "wrong")
        failures = store.get_login_failures("alice")
        sent = await drive(
            server,
            "/_auth/users/save?identity=alice",
            "POST",
            body={"status": "banned"},
            cookie=cookie,
        )
        assert status(sent) == 200
        assert payload(sent)["status"] == "banned"
        assert store.get_login_failures("alice") == failures
        banned = store.get_user("alice")
        assert banned["status"] == "banned"
        assert "error" in await login(server, "alice", "wrong")
        assert store.get_user("alice") == banned
        assert store.get_login_failures("alice")["failed_attempts"] == 2

    async def test_ban_during_password_check_survives_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        check_password = store.check_password

        def ban_during_check(password: str, stored: str) -> bool:
            record = store.get_user("alice")
            record["status"] = "banned"
            store.save_user(record)
            return check_password(password, stored)

        monkeypatch.setattr(AuthStore, "check_password", staticmethod(ban_during_check))
        assert "error" in await login(server, "alice", "wrong")
        assert store.get_user("alice")["status"] == "banned"
        assert store.get_login_failures("alice")["failed_attempts"] == 1

    async def test_unknown_identity_writes_nothing(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        assert await login(server, "ghost", "wrong") == {"error": "Invalid credentials"}
        assert auth_store(server).get_user("ghost") is None
        assert auth_store(server).get_login_failures("ghost") is None

    async def test_max_attempts_failures_lock_even_the_correct_password(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        server = await started(tmp_path, login={"max_attempts": 2, "backoff": 10})
        await login(server, "alice", "nope")
        await login(server, "alice", "nope")
        assert await login(server, "alice", "wonder") == {"error": "Too many failed attempts"}
        assert auth_store(server).get_login_failures("alice")["failed_attempts"] == 2
        clock.advance(11)
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"
        assert auth_store(server).get_login_failures("alice") is None
        assert (await login(server, "alice", "wrong"))["remaining_attempts"] == 1

    async def test_a_locked_identity_is_refused_by_basic(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        server = await started(tmp_path, login={"max_attempts": 2, "backoff": 10})
        await login(server, "root", "nope")
        await login(server, "root", "nope")
        sent = await drive(server, "/_auth/users/list", authorization=basic("root", "rootpw"))
        assert status(sent) == 401

    async def test_basic_failures_count_and_a_success_resets(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        server = await started(tmp_path, login={"max_attempts": 2, "backoff": 10})
        sent = await drive(server, "/_auth/users/list", authorization=basic("root", "nope"))
        assert status(sent) == 401
        assert (await login(server, "root", "rootpw"))["identity"] == "root"
        await drive(server, "/_auth/users/list", authorization=basic("root", "nope"))
        assert await login(server, "root", "nope") == {
            "error": "Invalid credentials",
            "remaining_attempts": 0,
        }
        assert await login(server, "root", "rootpw") == {"error": "Too many failed attempts"}

    async def test_attempts_during_the_lock_do_not_extend_it(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        server = await started(tmp_path, login={"max_attempts": 2, "backoff": 10})
        await login(server, "alice", "nope")
        await login(server, "alice", "nope")
        clock.advance(6)
        assert await login(server, "alice", "nope") == {"error": "Too many failed attempts"}
        assert await login(server, "alice", "wonder") == {"error": "Too many failed attempts"}
        clock.advance(5)
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"

    async def test_each_failure_after_the_lock_doubles_the_window(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        server = await started(tmp_path, login={"max_attempts": 2, "backoff": 10})
        await login(server, "alice", "nope")
        await login(server, "alice", "nope")
        clock.advance(11)
        assert await login(server, "alice", "nope") == {
            "error": "Invalid credentials",
            "remaining_attempts": 0,
        }
        clock.advance(11)
        assert await login(server, "alice", "wonder") == {"error": "Too many failed attempts"}
        clock.advance(10)
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"

    async def test_a_basic_success_resets_the_counter(self, tmp_path: Path, clock: Clock) -> None:
        server = await started(tmp_path, login={"max_attempts": 2, "backoff": 10})
        await login(server, "root", "nope")
        sent = await drive(server, "/_auth/users/list", authorization=basic("root", "rootpw"))
        assert status(sent) == 200
        assert await login(server, "root", "nope") == {
            "error": "Invalid credentials",
            "remaining_attempts": 1,
        }

    async def test_the_login_word_of_the_recipe_sets_the_policy(
        self, tmp_path: Path, clock: Clock
    ) -> None:
        class SiteWithAuth(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                apps.application(code="home", mount="", app_class=BaseApplication)
                auth_app = apps.application(code="_auth", app_class=AuthApplicationBase)
                auth_app.login(max_attempts=1, backoff=10)

        server = AsgiServer(
            SiteWithAuth,
            storage=site_mounts(tmp_path),
            storage_key=Fernet.generate_key().decode(),
        )
        await start_server(server)
        seed(server)
        assert await login(server, "alice", "nope") == {
            "error": "Invalid credentials",
            "remaining_attempts": 0,
        }
        assert await login(server, "alice", "wonder") == {"error": "Too many failed attempts"}


class TestStoreWord:
    async def test_the_store_word_names_the_class_and_its_kwargs(self, tmp_path: Path) -> None:
        class OwnStore(FileAuthStore):
            """A store class the recipe names."""

        class SiteWithAuth(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                apps.application(code="home", mount="", app_class=BaseApplication)
                auth_app = apps.application(code="_auth", app_class=AuthApplicationBase)
                auth_app.store(store_class=OwnStore, prefix="people")

        server = AsgiServer(
            SiteWithAuth,
            storage=site_mounts(tmp_path),
            storage_key=Fernet.generate_key().decode(),
        )
        await start_server(server)
        store = auth_store(server)
        assert isinstance(store, OwnStore)
        assert store.prefix == "people"

    async def test_the_store_kwarg_of_a_hand_built_server(self, tmp_path: Path) -> None:
        server = await started(tmp_path, store={"prefix": "people"})
        store = auth_store(server)
        assert isinstance(store, FileAuthStore)
        assert store.prefix == "people"
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"


class TestCredentials:
    async def test_a_basic_header_is_verified_on_the_auth_store(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        sent = await drive(server, "/_auth/users/list", authorization=basic("root", "rootpw"))
        assert status(sent) == 200
        sent = await drive(server, "/_auth/users/list", authorization=basic("root", "wrong"))
        assert status(sent) == 401

    async def test_authenticate_answers_the_basic_avatar(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = await server.kbus_call(
            "/_auth/authenticate", {"credential": basic("alice", "wonder"), "channel": "api"}
        )
        assert answer == {
            "identity": "alice",
            "group": "staff",
            "tags": ["OWN", "STAFF_READ"],
            "data": {},
            "valid_until": None,
        }

    async def test_a_gak_token_is_verified_on_the_auth_store(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        key = auth_store(server).issue_token("robot", ["AUTH_ADMIN"])
        sent = await drive(server, "/_auth/users/list", authorization=f"Bearer {key}")
        assert status(sent) == 200
        sent = await drive(server, "/_auth/users/list", authorization=f"Bearer {key}x")
        assert status(sent) == 401

    async def test_authenticate_answers_the_token_avatar(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        expires_at = time.time() + 3600
        key = auth_store(server).issue_token("alice", "*", expires_at)
        answer = await server.kbus_call(
            "/_auth/authenticate", {"credential": f"Bearer {key}", "channel": "api"}
        )
        assert answer == {
            "identity": "alice",
            "group": "staff",
            "tags": ["OWN", "STAFF_READ"],
            "data": {},
            "valid_until": expires_at,
        }

    @pytest.mark.parametrize(
        "credential",
        ["Bearer sk_static", "Bearer not.a.jwt", "Token abc", "Basic !!!", "noscheme"],
    )
    async def test_anything_but_password_and_gak_is_refused(
        self, tmp_path: Path, credential: str
    ) -> None:
        server = await started(tmp_path)
        with pytest.raises(KBusCallError) as refused:
            await server.kbus_call(
                "/_auth/authenticate", {"credential": credential, "channel": "api"}
            )
        assert refused.value.status == 401


class TestBusOnly:
    @pytest.mark.parametrize(
        "path",
        [
            "/_auth/authenticate?credential=x&channel=api",
            "/_auth/forget_credential?credential=x",
            "/_auth/avatar?identity=alice",
        ],
    )
    async def test_an_http_caller_gets_403(self, tmp_path: Path, path: str) -> None:
        server = await started(tmp_path)
        sent = await drive(server, path, "POST")
        assert status(sent) == 403

    async def test_forget_credential_drops_the_cached_avatar(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        key = store.issue_token("alice", "*")
        credential = f"Bearer {key}"
        await server.authenticate_credential(credential, "api")
        store.delete_token(key.removeprefix("gak_").partition("_")[0])
        assert (await server.authenticate_credential(credential, "api")).identity == "alice"
        answer = await server.kbus_call("/_auth/forget_credential", {"credential": credential})
        assert answer == {"status": "ok"}
        with pytest.raises(HTTPUnauthorized):
            await server.authenticate_credential(credential, "api")


class TestAvatarRoute:
    async def test_identity_and_group_give_the_avatar(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = await server.kbus_call("/_auth/avatar", {"identity": "alice", "group": "ops"})
        assert answer == {
            "identity": "alice",
            "group": "ops",
            "tags": ["OWN", "OPS_DEPLOY"],
            "data": {},
            "valid_until": None,
        }

    async def test_an_empty_group_means_the_primary_group(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        answer = await server.kbus_call("/_auth/avatar", {"identity": "alice", "group": ""})
        assert (answer["group"], answer["tags"]) == ("staff", ["OWN", "STAFF_READ"])

    @pytest.mark.parametrize(
        "identity, group",
        [("ghost", ""), ("mallory", ""), ("alice", "admins")],
    )
    async def test_unknown_disabled_or_foreign_group_is_refused(
        self, tmp_path: Path, identity: str, group: str
    ) -> None:
        server = await started(tmp_path)
        with pytest.raises(KBusCallError) as refused:
            await server.kbus_call("/_auth/avatar", {"identity": identity, "group": group})
        assert refused.value.status == 401


class TestOverride:
    async def test_a_subclass_that_changes_verification_is_the_one_that_answers(
        self, tmp_path: Path
    ) -> None:
        class ExternalAuth(AuthApplicationBase):
            """Verifies one fixed bearer value, nothing else."""

            def verify_credential(self, credential: str) -> dict[str, Any]:
                if credential != "Bearer external":
                    raise HTTPUnauthorized("unknown")
                return {
                    "identity": "ext",
                    "group": None,
                    "tags": ["AUTH_ADMIN"],
                    "data": {"source": "external"},
                    "valid_until": None,
                }

        server = await started(tmp_path, ExternalAuth)
        answer = await server.kbus_call(
            "/_auth/authenticate", {"credential": "Bearer external", "channel": "api"}
        )
        assert answer["identity"] == "ext"
        assert answer["data"] == {"source": "external"}
        sent = await drive(server, "/_auth/users/list", authorization="Bearer external")
        assert status(sent) == 200
        sent = await drive(server, "/_auth/users/list", authorization=basic("root", "rootpw"))
        assert status(sent) == 401


class TestManagementGate:
    @pytest.mark.parametrize(
        "path",
        ["/_auth/users/list", "/_auth/groups/list", "/_auth/tags/list", "/_auth/tokens/list"],
    )
    async def test_anonymous_is_401_non_admin_403_admin_200(
        self, tmp_path: Path, path: str
    ) -> None:
        server = await started(tmp_path)
        assert status(await drive(server, path)) == 401
        alice = await session_cookie(server, "alice", "wonder")
        assert status(await drive(server, path, cookie=alice)) == 403
        assert status(await drive(server, path, cookie=await admin(server))) == 200

    @pytest.mark.parametrize(
        "path, body",
        [
            ("/_auth/users/create_user?identity=bob", {"password": "x", "password_confirm": "x"}),
            ("/_auth/groups/create_group?code=dev", {"tags": ["AUTH_ADMIN"]}),
            ("/_auth/tags/create_tag?code=DEV", {"description": "Developers"}),
            ("/_auth/tokens/issue", {"user": "alice", "tags": "*"}),
        ],
    )
    async def test_creation_needs_auth_admin(
        self, tmp_path: Path, path: str, body: dict[str, Any]
    ) -> None:
        server = await started(tmp_path)
        alice = await session_cookie(server, "alice", "wonder")
        assert status(await drive(server, path, "POST", body=body, cookie=alice)) == 403
        assert status(await drive(server, path, "POST", body=body)) == 401
        sent = await drive(server, path, "POST", body=body, cookie=await admin(server))
        assert status(sent) == 200
        assert "error" not in payload(sent)


class TestUsersSection:
    async def test_create_user_then_log_in_with_it(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/create_user?identity=bob",
            "POST",
            body={
                "password": "bobpw",
                "password_confirm": "bobpw",
                "groups": ["ops"],
                "primary_group": "ops",
            },
            cookie=cookie,
        )
        record = payload(sent)
        assert record["identity"] == "bob"
        assert record["status"] == "confirmed"
        assert "password_hash" not in record
        answer = await login(server, "bob", "bobpw")
        assert (answer["group"], answer["tags"]) == ("ops", ["OPS_DEPLOY"])

    async def test_a_user_created_without_a_password_enters_only_after_set_password(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server, "/_auth/users/create_user?identity=bob", "POST", body={}, cookie=cookie
        )
        assert payload(sent)["identity"] == "bob"
        assert "error" in await login(server, "bob", "guess")
        await drive(
            server,
            "/_auth/users/set_password?identity=bob",
            "POST",
            body={"password": "fresh", "password_confirm": "fresh"},
            cookie=cookie,
        )
        assert (await login(server, "bob", "fresh"))["identity"] == "bob"

    @pytest.mark.parametrize(
        "body",
        [{"password": "one", "password_confirm": "two"}, {"password": "one"}],
    )
    async def test_a_password_that_does_not_match_its_confirmation_creates_nothing(
        self, tmp_path: Path, body: dict[str, Any]
    ) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server, "/_auth/users/create_user?identity=bob", "POST", body=body, cookie=cookie
        )
        assert "error" in payload(sent)
        assert "error" in payload(
            await drive(server, "/_auth/users/get?identity=bob", cookie=cookie)
        )

    async def test_an_existing_identity_is_not_created_again(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/create_user?identity=alice",
            "POST",
            body={"password": "other", "password_confirm": "other"},
            cookie=cookie,
        )
        assert "error" in payload(sent)
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"
        assert "error" in await login(server, "alice", "other")

    async def test_save_drops_a_password_hash_sent_by_the_caller(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        forged = auth_store(server).hash_password("attacker")
        auth_store(server).save_tag({"code": "X"})
        await drive(
            server,
            "/_auth/users/save?identity=alice",
            "POST",
            body={"password_hash": forged, "tags": ["X"]},
            cookie=cookie,
        )
        assert "error" in await login(server, "alice", "attacker")
        assert (await login(server, "alice", "wonder"))["tags"] == ["X", "STAFF_READ"]

    async def test_records_never_show_the_password_hash(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        users = payload(await drive(server, "/_auth/users/list", cookie=cookie))["users"]
        assert {u["identity"] for u in users} >= {"root", "alice"}
        assert all("password_hash" not in u for u in users)
        one = payload(await drive(server, "/_auth/users/get?identity=alice", cookie=cookie))
        assert one["identity"] == "alice" and "password_hash" not in one

    async def test_an_unknown_status_is_refused_and_not_stored(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/create_user?identity=bob",
            "POST",
            body={"password": "x", "password_confirm": "x", "status": "active"},
            cookie=cookie,
        )
        assert status(sent) == 200
        assert "error" in payload(sent)
        assert "error" in payload(
            await drive(server, "/_auth/users/get?identity=bob", cookie=cookie)
        )

    async def test_a_primary_group_outside_groups_is_refused(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/save?identity=alice",
            "POST",
            body={"primary_group": "admins"},
            cookie=cookie,
        )
        assert "error" in payload(sent)
        assert (await login(server, "alice", "wonder"))["group"] == "staff"

    async def test_save_changes_tags_and_set_password_changes_the_password(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        auth_store(server).save_tag({"code": "NEW"})
        await drive(
            server,
            "/_auth/users/save?identity=alice",
            "POST",
            body={"tags": ["NEW"]},
            cookie=cookie,
        )
        sent = await drive(
            server,
            "/_auth/users/set_password?identity=alice",
            "POST",
            body={"password": "fresh", "password_confirm": "fresh"},
            cookie=cookie,
        )
        assert payload(sent) == {"identity": "alice", "updated": True}
        assert "error" in await login(server, "alice", "wonder")
        assert (await login(server, "alice", "fresh"))["tags"] == ["NEW", "STAFF_READ"]

    async def test_delete_removes_the_user(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        await login(server, "alice", "wrong")
        assert auth_store(server).get_login_failures("alice") is not None
        sent = await drive(server, "/_auth/users/delete?identity=alice", "POST", cookie=cookie)
        assert payload(sent) == {"identity": "alice", "deleted": True}
        assert auth_store(server).get_login_failures("alice") is None
        assert "error" in await login(server, "alice", "wonder")

    async def test_get_missing_user_reports_no_such_user(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(server, "/_auth/users/get?identity=ghost", cookie=cookie)
        assert status(sent) == 200
        assert payload(sent) == {"error": "No such user: ghost"}

    async def test_save_on_a_missing_user_is_an_error(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server, "/_auth/users/save?identity=ghost", "POST", body={"tags": []}, cookie=cookie
        )
        assert status(sent) == 200
        assert payload(sent) == {"error": "No such user: ghost"}
        assert "error" in payload(
            await drive(server, "/_auth/users/get?identity=ghost", cookie=cookie)
        )

    async def test_set_password_rejects_a_mismatch(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/set_password?identity=alice",
            "POST",
            body={"password": "a", "password_confirm": "b"},
            cookie=cookie,
        )
        assert "error" in payload(sent)
        assert (await login(server, "alice", "wonder"))["identity"] == "alice"
        assert "error" in await login(server, "alice", "a")

    async def test_set_password_on_a_missing_user_is_an_error(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/set_password?identity=ghost",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
            cookie=cookie,
        )
        assert status(sent) == 200
        assert payload(sent) == {"error": "No such user: ghost"}

    async def test_create_user_without_identity_is_rejected(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/users/create_user",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
            cookie=cookie,
        )
        assert status(sent) == 200
        assert payload(sent) == {"error": "Identity is required"}


class TestGroupsAndTagsSections:
    async def test_a_created_group_gives_its_tags_at_login(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        auth_store(server).save_tag({"code": "DEV"})
        created = payload(
            await drive(
                server,
                "/_auth/groups/create_group?code=dev",
                "POST",
                body={"tags": ["DEV"]},
                cookie=cookie,
            )
        )
        assert created == {"code": "dev", "tags": ["DEV"]}
        await drive(
            server,
            "/_auth/users/save?identity=alice",
            "POST",
            body={"groups": ["staff", "ops", "dev"]},
            cookie=cookie,
        )
        assert (await login(server, "alice", "wonder", group="dev"))["tags"] == ["OWN", "DEV"]

    async def test_a_group_with_members_is_not_deleted(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(server, "/_auth/groups/delete?code=ops", "POST", cookie=cookie)
        assert status(sent) == 200
        assert "error" in payload(sent)
        group = payload(await drive(server, "/_auth/groups/get?code=ops", cookie=cookie))
        assert group == {"code": "ops", "tags": ["OPS_DEPLOY"]}

    async def test_tags_are_created_read_and_deleted(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        created = payload(
            await drive(
                server,
                "/_auth/tags/create_tag?code=DEV",
                "POST",
                body={"description": "Developers", "parent": "STAFF"},
                cookie=cookie,
            )
        )
        assert created == {
            "code": "DEV",
            "description": "Developers",
            "owner": "",
            "reserved": False,
            "parent": "STAFF",
        }
        tags = payload(await drive(server, "/_auth/tags/list", cookie=cookie))["tags"]
        assert [t["code"] for t in tags if not t["owner"]] == ["DEV"]
        sent = await drive(server, "/_auth/tags/delete?code=DEV", "POST", cookie=cookie)
        assert payload(sent) == {"code": "DEV", "deleted": True}


class TestTokensSection:
    async def test_an_issued_token_authenticates_and_its_secret_is_never_listed(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        issued = payload(
            await drive(
                server,
                "/_auth/tokens/issue",
                "POST",
                body={"user": "robot", "tags": ["AUTH_ADMIN"]},
                cookie=cookie,
            )
        )
        assert issued["user"] == "robot" and issued["key"].startswith("gak_")
        tokens = payload(await drive(server, "/_auth/tokens/list", cookie=cookie))["tokens"]
        assert len(tokens) == 1 and "secret_hash" not in tokens[0]
        sent = await drive(server, "/_auth/users/list", authorization=f"Bearer {issued['key']}")
        assert status(sent) == 200

    async def test_an_empty_tag_list_is_refused_and_writes_nothing(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server, "/_auth/tokens/issue", "POST", body={"user": "alice", "tags": []}, cookie=cookie
        )
        assert status(sent) == 200
        assert "error" in payload(sent)
        assert payload(await drive(server, "/_auth/tokens/list", cookie=cookie))["tokens"] == []

    async def test_a_token_of_an_unknown_user_is_refused(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        sent = await drive(
            server,
            "/_auth/tokens/issue",
            "POST",
            body={"user": "ghost", "tags": "*"},
            cookie=cookie,
        )
        assert "error" in payload(sent)

    @pytest.mark.parametrize("verb", ["revoke", "delete"])
    async def test_a_cached_token_is_refused_at_once_after_revoke_or_delete(
        self, tmp_path: Path, verb: str
    ) -> None:
        server = await started(tmp_path)
        cookie = await admin(server)
        issued = payload(
            await drive(
                server,
                "/_auth/tokens/issue",
                "POST",
                body={"user": "alice", "tags": "*"},
                cookie=cookie,
            )
        )
        credential = f"Bearer {issued['key']}"
        await server.authenticate_credential(credential, "api")
        key_id = payload(await drive(server, "/_auth/tokens/list", cookie=cookie))["tokens"][0][
            "key_id"
        ]
        sent = await drive(server, f"/_auth/tokens/{verb}?key_id={key_id}", "POST", cookie=cookie)
        assert payload(sent)[f"{verb}d"] is True
        with pytest.raises(HTTPUnauthorized):
            await server.authenticate_credential(credential, "api")


class TestAvatarGroup:
    def test_the_group_field(self) -> None:
        assert Avatar("ann", ["x"], group="staff").group == "staff"
        assert Avatar("ann", ["x"]).group is None
