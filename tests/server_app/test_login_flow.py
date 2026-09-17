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

"""Password login surface tests (core 1d wave 1, Phase 4).

The flow is driven through a full hand-built ``AsgiServer`` at the ASGI level
(no uvicorn), the same driving style as ``test_session.py``: JSON POST to
``/_server/login`` verifies against a seeded in-memory ``UserStore`` and
attaches the avatar to the request's session in place
(``request.session.attach_avatar``) — the id never changes, so no login-time
cookie is issued and handlers never touch cookies. The HTML page, the public
``login_methods``, ``logout``, the ``AuthMethod``/``AuthSection`` contract and
the ``safe_next_path`` guard are covered alongside.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tests.storage_support import site_mounts

from kajenn import AsgiServer, BaseApplication, FileUserStore, UserStore
from kajenn_server_app import (
    AuthMethod,
    AuthSection,
    PasswordMethod,
    ServerApplication,
    safe_next_path,
)
from kajenn.types import Message, Scope


class MemoryUserStore(UserStore):
    """In-memory ``UserStore`` backend: the contract suite over a plain dict."""

    __slots__ = ("_records",)

    def __init__(self, storage: object = None) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def load_all(self) -> list[dict[str, Any]]:
        return list(self._records.values())

    def get(self, identity: str) -> dict[str, Any] | None:
        return self._records.get(identity)

    def save(self, record: dict[str, Any]) -> None:
        self._records[record["identity"]] = record

    def delete(self, identity: str) -> bool:
        return self._records.pop(identity, None) is not None


class SeededUserStore(MemoryUserStore):
    """The store the configuration names: alice enabled, mallory disabled."""

    def __init__(self, storage: object = None) -> None:
        super().__init__(storage)
        self.save(
            {
                "identity": "alice",
                "password_hash": self.hash_password("wonder"),
                "tags": ["admin"],
                "enabled": True,
            }
        )
        self.save(
            {
                "identity": "mallory",
                "password_hash": self.hash_password("evil"),
                "tags": [],
                "enabled": False,
            }
        )


def make_server(with_users: bool = True) -> AsgiServer:
    """A full server composed in code; ``with_users`` declares the seeded store."""
    users = {"store_class": SeededUserStore} if with_users else None
    return AsgiServer(
        applications=[ServerApplication, (BaseApplication, {"mount": ""})],
        **({"users": users} if users else {}),
    )


async def drive(
    server: AsgiServer,
    path: str,
    method: str = "GET",
    cookie: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[Scope, list[Message]]:
    """Drive one request through ``server`` at the ASGI level (JSON body when given)."""
    headers: list[tuple[bytes, bytes]] = []
    raw = b""
    if body is not None:
        headers.append((b"content-type", b"application/json"))
        raw = json.dumps(body).encode()
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
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
    return scope, sent


def response_status(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def response_headers(sent: list[Message]) -> dict[bytes, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {name: value for name, value in start["headers"] if name != b"set-cookie"}


def response_body(sent: list[Message]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def json_body(sent: list[Message]) -> Any:
    return json.loads(response_body(sent))


def set_cookie_value(sent: list[Message]) -> str | None:
    start = next(m for m in sent if m["type"] == "http.response.start")
    for name, value in start["headers"]:
        if name == b"set-cookie":
            return value.decode()
    return None


def cookie_token(sent: list[Message]) -> str:
    cookie = set_cookie_value(sent)
    assert cookie is not None
    return cookie.split(";")[0].split("=", 1)[1]


class Clock:
    """Controllable stand-in for ``time.time`` — drives the lockout window tests."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_lockout_server(
    policy: dict[str, Any] | None = None,
) -> tuple[AsgiServer, MemoryUserStore]:
    """A server seeded with alice/wonder; ``policy`` rides the ``login()`` config lift."""

    class AliceStore(MemoryUserStore):
        """The store the configuration names, born with alice in it."""

        def __init__(self, storage: object = None) -> None:
            super().__init__(storage)
            self.save(
                {
                    "identity": "alice",
                    "password_hash": self.hash_password("wonder"),
                    "tags": ["admin"],
                    "enabled": True,
                }
            )

    server_app = (ServerApplication, {"login": policy} if policy else {})
    server = AsgiServer(
        applications=[server_app, (BaseApplication, {"mount": ""})],
        users={"store_class": AliceStore},
    )
    return server, server.user_store


async def login_attempt(
    server: AsgiServer, session_id: str, password: str, identity: str = "alice"
) -> Any:
    """One login POST riding ``session_id``; returns the JSON payload."""
    _, sent = await drive(
        server,
        "/_server/login",
        "POST",
        cookie=f"session_id={session_id}",
        body={"identity": identity, "password": password},
    )
    return json_body(sent)


class TestLoginHappyPath:
    async def test_login_attaches_the_avatar_and_keeps_the_session_id(self) -> None:
        server = make_server()
        anonymous = server.session_store.create()
        anonymous.data["cart"] = "kept"
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            cookie=f"session_id={anonymous.id}",
            body={"identity": "alice", "password": "wonder"},
        )
        assert response_status(sent) == 200
        payload = json_body(sent)
        assert payload["identity"] == "alice"
        assert payload["tags"] == ["admin"]
        assert payload["session_id"] == anonymous.id  # the id never changes at login
        assert set_cookie_value(sent) is None  # the client's cookie is still valid
        promoted = server.session_store.get(anonymous.id)
        assert promoted is anonymous
        assert promoted.avatar() is not None
        assert promoted.avatar().identity == "alice"
        assert promoted.avatar().tags == ["admin"]
        assert promoted.data["cart"] == "kept"  # the cart survives the login

    async def test_login_green_path_against_a_file_user_store(self, tmp_path: Path) -> None:
        class SeededFileStore(FileUserStore):
            """The file store the configuration names, seeded at birth."""

            def __init__(self, storage: Any, **params: Any) -> None:
                super().__init__(storage, **params)
                self.save(
                    {
                        "identity": "alice",
                        "password_hash": self.hash_password("wonder"),
                        "tags": ["admin"],
                        "enabled": True,
                    }
                )

        server = AsgiServer(
            applications=[ServerApplication, (BaseApplication, {"mount": ""})],
            storage=site_mounts(tmp_path),
            storage_key=Fernet.generate_key().decode(),
            users={"store_class": SeededFileStore},
        )
        anonymous = server.session_store.create()
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            cookie=f"session_id={anonymous.id}",
            body={"identity": "alice", "password": "wonder"},
        )
        assert response_status(sent) == 200
        payload = json_body(sent)
        assert payload["identity"] == "alice"
        assert payload["tags"] == ["admin"]

    async def test_login_accepts_the_pages_form_encoded_post(self) -> None:
        server = make_server()
        anonymous = server.session_store.create()
        raw = b"identity=alice&password=wonder"
        headers = [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"cookie", f"session_id={anonymous.id}".encode()),
        ]
        scope: Scope = {
            "type": "http",
            "method": "POST",
            "path": "/_server/login",
            "query_string": b"",
            "headers": headers,
        }
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)
        payload = json_body(sent)
        assert payload["identity"] == "alice"
        assert payload["session_id"] == anonymous.id  # id kept, no cookie re-issue
        assert set_cookie_value(sent) is None

    async def test_login_on_first_contact_rides_the_new_session_cookie(self) -> None:
        server = make_server()
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            body={"identity": "alice", "password": "wonder"},
        )
        payload = json_body(sent)
        assert cookie_token(sent) == payload["session_id"]
        promoted = server.session_store.get(payload["session_id"])
        assert promoted is not None and promoted.avatar() is not None


class TestLoginFailures:
    async def test_invalid_credentials_answer_the_error_and_no_cookie(self) -> None:
        server = make_server()
        anonymous = server.session_store.create()
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            cookie=f"session_id={anonymous.id}",
            body={"identity": "alice", "password": "nope"},
        )
        assert response_status(sent) == 200
        assert json_body(sent) == {"error": "Invalid credentials", "remaining_attempts": 4}
        assert set_cookie_value(sent) is None

    async def test_disabled_user_never_authenticates(self) -> None:
        server = make_server()
        anonymous = server.session_store.create()
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            cookie=f"session_id={anonymous.id}",
            body={"identity": "mallory", "password": "evil"},
        )
        # disabled users have a record: their failures count and surface the counter
        assert json_body(sent) == {"error": "Invalid credentials", "remaining_attempts": 4}
        assert set_cookie_value(sent) is None

    async def test_missing_credentials_answer_the_error(self) -> None:
        server = make_server()
        anonymous = server.session_store.create()
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            cookie=f"session_id={anonymous.id}",
            body={"identity": "alice"},
        )
        assert json_body(sent) == {"error": "Identity and password are required"}
        assert set_cookie_value(sent) is None

    async def test_server_without_a_user_store_answers_the_error(self) -> None:
        server = make_server(with_users=False)
        anonymous = server.session_store.create()
        _, sent = await drive(
            server,
            "/_server/login",
            "POST",
            cookie=f"session_id={anonymous.id}",
            body={"identity": "alice", "password": "wonder"},
        )
        assert json_body(sent) == {"error": "Login is not available"}
        assert set_cookie_value(sent) is None


class TestLoginLockout:
    @pytest.fixture
    def clock(self, monkeypatch: pytest.MonkeyPatch) -> Clock:
        """Freeze ``time.time`` on a controllable clock."""
        frozen = Clock()
        monkeypatch.setattr(time, "time", frozen)
        return frozen

    async def test_max_attempts_failures_lock_even_the_correct_password(
        self, clock: Clock
    ) -> None:
        server, store = make_lockout_server()
        session = server.session_store.create()
        for expected_remaining in (4, 3, 2, 1, 0):
            payload = await login_attempt(server, session.id, "nope")
            assert payload == {
                "error": "Invalid credentials",
                "remaining_attempts": expected_remaining,
            }
        payload = await login_attempt(server, session.id, "wonder")
        assert payload == {"error": "Too many failed attempts"}
        assert store.get("alice")["failed_attempts"] == 5

    async def test_locked_attempt_does_not_touch_the_counter(self, clock: Clock) -> None:
        server, store = make_lockout_server({"max_attempts": 2, "backoff": 10})
        session = server.session_store.create()
        await login_attempt(server, session.id, "nope")
        await login_attempt(server, session.id, "nope")
        record = store.get("alice")
        assert record is not None
        locked_at = record["last_failed_at"]
        clock.advance(5)  # still inside the 10s window
        payload = await login_attempt(server, session.id, "nope")
        assert payload == {"error": "Too many failed attempts"}
        assert record["failed_attempts"] == 2  # the refused attempt never counted
        assert record["last_failed_at"] == locked_at  # nor extended the lock

    async def test_window_expiry_re_allows_and_success_resets(self, clock: Clock) -> None:
        server, store = make_lockout_server({"max_attempts": 2, "backoff": 10})
        session = server.session_store.create()
        await login_attempt(server, session.id, "nope")
        await login_attempt(server, session.id, "nope")
        clock.advance(11)  # past the 10s window
        payload = await login_attempt(server, session.id, "wonder")
        assert payload["identity"] == "alice"
        assert payload["session_id"] == session.id
        assert store.get("alice")["failed_attempts"] == 0

    async def test_success_resets_the_counter(self, clock: Clock) -> None:
        server, store = make_lockout_server()
        session = server.session_store.create()
        await login_attempt(server, session.id, "nope")
        payload = await login_attempt(server, session.id, "nope")
        assert payload == {"error": "Invalid credentials", "remaining_attempts": 3}
        payload = await login_attempt(server, session.id, "wonder")
        assert payload["identity"] == "alice"
        assert store.get("alice")["failed_attempts"] == 0
        payload = await login_attempt(server, session.id, "nope")
        assert payload == {"error": "Invalid credentials", "remaining_attempts": 4}

    async def test_unknown_identity_has_no_counter(self, clock: Clock) -> None:
        server, store = make_lockout_server()
        session = server.session_store.create()
        payload = await login_attempt(server, session.id, "nope", identity="ghost")
        assert payload == {"error": "Invalid credentials"}  # no remaining_attempts
        assert store.get("ghost") is None  # no record is ever created

    async def test_policy_is_tunable_via_the_login_config(self, clock: Clock) -> None:
        server, store = make_lockout_server({"max_attempts": 2, "backoff": 10})
        session = server.session_store.create()
        await login_attempt(server, session.id, "nope")
        payload = await login_attempt(server, session.id, "nope")
        assert payload == {"error": "Invalid credentials", "remaining_attempts": 0}
        payload = await login_attempt(server, session.id, "wonder")
        assert payload == {"error": "Too many failed attempts"}

    async def test_backoff_grows_exponentially(self, clock: Clock) -> None:
        server, store = make_lockout_server({"max_attempts": 2, "backoff": 10})
        session = server.session_store.create()
        await login_attempt(server, session.id, "nope")
        await login_attempt(server, session.id, "nope")  # locked: window 10 * 2**0
        clock.advance(11)  # first window expired
        payload = await login_attempt(server, session.id, "nope")
        assert payload == {"error": "Invalid credentials", "remaining_attempts": 0}
        clock.advance(11)  # the third failure doubled the window to 20s
        payload = await login_attempt(server, session.id, "wonder")
        assert payload == {"error": "Too many failed attempts"}
        clock.advance(10)  # 21s past the third failure — window passed
        payload = await login_attempt(server, session.id, "wonder")
        assert payload["identity"] == "alice"


class TestNoLoginPage:
    """D-SA-3: the HTML login page is gramlot's, not this app's."""

    async def test_login_page_is_gone(self) -> None:
        server = make_server()
        _, sent = await drive(server, "/_server/login_page")
        assert response_status(sent) == 404


class TestLoginMethods:
    async def test_login_methods_is_public_and_lists_the_password_descriptor(self) -> None:
        server = make_server(with_users=False)
        _, sent = await drive(server, "/_server/login_methods")
        assert response_status(sent) == 200
        assert json_body(sent) == {
            "methods": [
                {
                    "id": "password",
                    "kind": "form",
                    "label": "Sign in",
                    "action": "/_server/login",
                }
            ]
        }


class TestLogout:
    async def test_logout_deletes_the_session(self) -> None:
        server = make_server(with_users=False)
        session = server.session_store.create()
        _, sent = await drive(
            server, "/_server/logout", "POST", body={"session_id": session.id}
        )
        assert json_body(sent) == {"status": "ok"}
        assert server.session_store.get(session.id) is None

    async def test_logout_without_a_session_id_still_answers_ok(self) -> None:
        server = make_server(with_users=False)
        _, sent = await drive(server, "/_server/logout", "POST", body={})
        assert json_body(sent) == {"status": "ok"}


class TestAuthMethodContract:
    def test_password_method_owns_zero_routes(self) -> None:
        server = make_server(with_users=False)
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        assert app.auth_section is not None
        method = app.auth_section.methods["password"]
        assert isinstance(method, PasswordMethod)
        assert method.route.nodes() == {}

    async def test_password_method_is_never_attached_to_the_routing_tree(self) -> None:
        server = make_server(with_users=False)
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        assert app.auth_section is not None
        routers = app.auth_section.route.nodes(lazy=True).get("routers") or {}
        assert "password" not in routers
        _, sent = await drive(server, "/_server/auth/password/anything")
        assert response_status(sent) == 404

    def test_a_method_owning_routes_is_attached(self) -> None:
        from genro_routes import route

        class RoutedMethod(AuthMethod):
            kind = "redirect"

            @route(media_type="application/json")
            def start(self) -> dict[str, str]:
                """Entry route of the redirect method."""
                return {"ok": "start"}

        server = make_server(with_users=False)
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        assert app.auth_section is not None
        app.register_auth_method(RoutedMethod(app, "routed"))
        routers = app.auth_section.route.nodes(lazy=True).get("routers") or {}
        assert "routed" in routers

    def test_password_method_is_registered_under_the_auth_section(self) -> None:
        server = make_server(with_users=False)
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        assert isinstance(app.auth_section, AuthSection)
        assert app.sections["auth"] is app.auth_section
        assert list(app.auth_section.methods) == ["password"]
        method = app.auth_section.methods["password"]
        assert method.application is app
        assert method.server is server

    def test_auth_section_carries_the_server_and_describes_its_methods(self) -> None:
        server = make_server(with_users=False)
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        assert isinstance(app.auth_section, AuthSection)
        assert app.auth_section.server is server
        # A section with nothing registered describes nothing...
        assert AuthSection(app).descriptors() == []
        # ...and the app's own section describes exactly its registered methods.
        method = app.auth_section.methods["password"]
        assert app.auth_section.descriptors() == [method.descriptor()]

    def test_duplicate_method_id_is_rejected(self) -> None:
        server = make_server(with_users=False)
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        with pytest.raises(ValueError, match="already registered"):
            app.register_auth_method(PasswordMethod(app, "password"))


class TestSafeNextPath:
    @pytest.mark.parametrize(
        "value",
        ["/app/page", "/", "/a?b=c"],
    )
    def test_same_origin_relative_paths_pass(self, value: str) -> None:
        assert safe_next_path(value) == value

    @pytest.mark.parametrize(
        "value",
        [None, "", "app/page", "//evil.example", "https://evil.example", "/a\\b", "javascript:x"],
    )
    def test_unsafe_values_collapse_to_the_default(self, value: str | None) -> None:
        assert safe_next_path(value) == "/"
        assert safe_next_path(value, default="/home") == "/home"
