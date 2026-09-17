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

"""Auth tests (Macro 2 Phases 5+9): AuthCore verification + §5.5 identity precedence.

Header verification (basic/bearer/jwt, wrong credentials → 401) is driven at
the ASGI level through an ``AuthMixin/MiddlewareMixin/BaseServer`` composition,
the same driving style as ``test_session.py``. The §5.5 precedence — header
identity wins over the session, an invalid header is a 401 with no fallback —
is exercised both by calling ``server.authenticate(scope)`` at app-dispatch
time and END-TO-END through the REAL combined chain (session middleware order
400 OUTSIDE auth 450) on the full
``AuthMixin/SessionMixin/MiddlewareMixin/BaseServer`` composition.
"""

from __future__ import annotations

import base64
from pathlib import Path

import jwt
import pytest
from cryptography.fernet import Fernet

from tests.storage_support import site_mounts

from kajenn import (
    ApiKeyStore,
    AsgiServer,
    AuthCore,
    AuthMixin,
    Avatar,
    BaseApplication,
    BaseServer,
    FileApiKeyStore,
    FileUserStore,
    Session,
    SessionMixin,
    UserStore,
)
from kajenn.exceptions import HTTPUnauthorized
from kajenn.middleware import MiddlewareMixin
from kajenn.types import Message, Receive, Scope, Send


class MemoryUserStore(UserStore):
    """In-memory ``UserStore`` backend used as a ready store instance in tests."""

    __slots__ = ("_records",)

    def __init__(self, storage: object = None) -> None:
        self._records: dict[str, dict] = {}

    def load_all(self) -> list[dict]:
        return list(self._records.values())

    def get(self, identity: str) -> dict | None:
        return self._records.get(identity)

    def save(self, record: dict) -> None:
        self._records[record["identity"]] = record

    def delete(self, identity: str) -> bool:
        return self._records.pop(identity, None) is not None


class MemoryApiKeyStore(ApiKeyStore):
    """In-memory ``ApiKeyStore`` backend: the shared issue/verify logic over a dict."""

    __slots__ = ("_records",)

    def __init__(self, storage: object = None) -> None:
        self._records: dict[str, dict] = {}

    def load_all(self) -> list[dict]:
        return list(self._records.values())

    def get(self, key_id: str) -> dict | None:
        return self._records.get(key_id)

    def save(self, record: dict) -> None:
        self._records[record["key_id"]] = record

    def delete(self, key_id: str) -> bool:
        return self._records.pop(key_id, None) is not None


def basic_header(username: str, password: str) -> str:
    """The value of a Basic ``Authorization`` header for these credentials."""
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {raw}"


AUTH_CONFIG = {
    "basic": {"alice": {"password": "wonderland", "tags": "admin,ops"}},
    "bearer": {"svc": {"token": "sk_live_xyz", "tags": "api"}},
    "jwt": [{"secret": "topsecret", "algorithm": "HS256"}],
}


# --- AuthCore unit ---


class TestAuthCore:
    def test_no_header_returns_none(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        assert core.authenticate({"headers": []}) is None

    def test_basic_ok_returns_avatar(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        scope: Scope = {"headers": [(b"authorization", basic_header("alice", "wonderland").encode())]}
        avatar = core.authenticate(scope)
        assert avatar is not None
        assert avatar.identity == "alice"
        assert avatar.tags == ["admin", "ops"]

    def test_bearer_ok_returns_avatar(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        scope: Scope = {"headers": [(b"authorization", b"Bearer sk_live_xyz")]}
        avatar = core.authenticate(scope)
        assert avatar is not None and avatar.identity == "svc"

    def test_jwt_ok_returns_avatar(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        token = jwt.encode({"sub": "carol", "tags": ["reader"]}, "topsecret", algorithm="HS256")
        scope: Scope = {"headers": [(b"authorization", f"Bearer {token}".encode())]}
        avatar = core.authenticate(scope)
        assert avatar is not None
        assert avatar.identity == "carol"
        assert avatar.tags == ["reader"]

    def test_signing_config_is_the_symmetric_secret(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        signing = core.signing_jwt_config
        assert signing is not None
        assert signing["secret"] == "topsecret"

    def test_public_key_never_becomes_the_signing_config(self) -> None:
        # A verifier configured with public_key only (algorithm defaulted):
        # it can verify, but public key material must never mint tokens.
        core = AuthCore(jwt=[{"public_key": "not-a-secret"}])
        assert core.signing_jwt_config is None

    def test_wrong_password_raises_401(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        scope: Scope = {"headers": [(b"authorization", basic_header("alice", "nope").encode())]}
        with pytest.raises(HTTPUnauthorized) as excinfo:
            core.authenticate(scope)
        assert excinfo.value.status == 401
        assert (b"www-authenticate", b"Bearer") in excinfo.value.headers

    def test_malformed_header_raises_401_with_challenge(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        scope: Scope = {"headers": [(b"authorization", b"Basicabc123")]}
        with pytest.raises(HTTPUnauthorized) as excinfo:
            core.authenticate(scope)
        assert (b"www-authenticate", b"Bearer") in excinfo.value.headers

    def test_unknown_scheme_raises_401(self) -> None:
        core = AuthCore(**AUTH_CONFIG)
        scope: Scope = {"headers": [(b"authorization", b"Weird abc")]}
        with pytest.raises(HTTPUnauthorized):
            core.authenticate(scope)

    def test_missing_basic_password_config_raises(self) -> None:
        with pytest.raises(ValueError, match="missing 'password'"):
            AuthCore(basic={"bob": {"tags": "user"}})

    def test_missing_bearer_token_config_raises(self) -> None:
        with pytest.raises(ValueError, match="missing 'token'"):
            AuthCore(bearer={"svc": {"tags": "api"}})


# --- ASGI-level header authentication ---


class HeaderAuthServer(AuthMixin, MiddlewareMixin, BaseServer):
    """Header-only auth composition: no session capability."""


class EchoAuthApp(BaseApplication):
    """Echoes the identity of the avatar published on ``scope["auth"]``."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        auth = scope.get("auth")
        body = auth.identity.encode() if auth is not None else b"anonymous"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


async def http_get(
    server: BaseServer, authorization: str | None = None, cookie: str | None = None
) -> tuple[Scope, list[Message]]:
    """Drive one GET through ``server`` at the ASGI level; return the scope and what it sent."""
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return scope, sent


def response_status(sent: list[Message]) -> int:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"]


def response_body(sent: list[Message]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def set_cookie_value(sent: list[Message]) -> str | None:
    start = next(m for m in sent if m["type"] == "http.response.start")
    for name, value in start["headers"]:
        if name == b"set-cookie":
            return value.decode()
    return None


def header_value(sent: list[Message], header: bytes) -> bytes | None:
    start = next(m for m in sent if m["type"] == "http.response.start")
    for name, value in start["headers"]:
        if name == header:
            return value
    return None


class TestHeaderAuthFlow:
    async def test_basic_ok(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        scope, sent = await http_get(server, basic_header("alice", "wonderland"))
        assert response_status(sent) == 200
        assert response_body(sent) == b"alice"
        assert scope["auth"].identity == "alice"

    async def test_bearer_ok(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        _, sent = await http_get(server, "Bearer sk_live_xyz")
        assert response_body(sent) == b"svc"

    async def test_jwt_ok(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        token = jwt.encode({"sub": "carol"}, "topsecret", algorithm="HS256")
        _, sent = await http_get(server, f"Bearer {token}")
        assert response_body(sent) == b"carol"

    async def test_wrong_password_yields_401(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        _, sent = await http_get(server, basic_header("alice", "nope"))
        assert response_status(sent) == 401

    async def test_invalid_credentials_401_carries_www_authenticate(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        _, sent = await http_get(server, basic_header("alice", "nope"))
        assert response_status(sent) == 401
        assert header_value(sent, b"www-authenticate") == b"Bearer"

    async def test_malformed_credentials_401_carries_www_authenticate(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        _, sent = await http_get(server, "Basicabc123")
        assert response_status(sent) == 401
        assert header_value(sent, b"www-authenticate") == b"Bearer"

    async def test_no_header_is_anonymous(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        scope, sent = await http_get(server)
        assert scope["auth"] is None
        assert response_body(sent) == b"anonymous"

    async def test_explicit_auth_false_disarms_the_middleware(self) -> None:
        server = HeaderAuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG, middleware={"auth": False})
        scope, _ = await http_get(server, basic_header("alice", "wonderland"))
        assert "auth" not in scope


# --- §5.5 identity precedence (server.authenticate at app-dispatch time) ---


class AuthServer(AuthMixin, SessionMixin, MiddlewareMixin, BaseServer):
    """The shipped-shape composition: header auth over sessions and the chain."""


def scope_with(session: Session | None, authorization: str | None) -> Scope:
    """A scope carrying an attached session and/or an Authorization header."""
    headers = [(b"authorization", authorization.encode())] if authorization is not None else []
    scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    if session is not None:
        scope["session"] = session
    return scope


class TestIdentityPrecedence:
    def test_header_wins_over_session(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        session = Session("sid", avatar=Avatar("sessionuser"), ttl=3600)
        scope = scope_with(session, basic_header("alice", "wonderland"))
        avatar = server.authenticate(scope)
        assert avatar.identity == "alice"

    def test_invalid_header_is_401_no_session_fallback(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        session = Session("sid", avatar=Avatar("sessionuser"), ttl=3600)
        scope = scope_with(session, basic_header("alice", "nope"))
        with pytest.raises(HTTPUnauthorized):
            server.authenticate(scope)

    def test_no_header_falls_back_to_session(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        session = Session("sid", avatar=Avatar("sessionuser", ["member"]), ttl=3600)
        scope = scope_with(session, None)
        avatar = server.authenticate(scope)
        assert avatar.identity == "sessionuser"
        assert avatar.tags == ["member"]

    def test_no_header_anonymous_session_is_none(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        session = Session("sid", avatar=None, ttl=3600)
        assert server.authenticate(scope_with(session, None)) is None

    def test_no_header_no_session_is_none(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        assert server.authenticate(scope_with(None, None)) is None


# --- end-to-end through the REAL combined chain (Phase 9: B1/B2/B3) ---


class TestCombinedChainFlow:
    async def test_header_auth_without_cookie_gets_anonymous_session(self) -> None:
        # B1: header identity on the scope, anonymous session, Set-Cookie, no 500.
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        scope, sent = await http_get(server, basic_header("alice", "wonderland"))
        assert response_status(sent) == 200
        assert scope["auth"].identity == "alice"
        assert set_cookie_value(sent) is not None
        assert scope["session"].avatar() is None

    async def test_session_identity_flows_through_the_chain(self) -> None:
        # B2: cookie only — the §5.5 session fallback works through the chain.
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        session = server.session_store.create(avatar=Avatar("sessionuser", ["member"]))
        scope, sent = await http_get(server, cookie=f"session_id={session.id}")
        assert response_status(sent) == 200
        assert response_body(sent) == b"sessionuser"
        assert scope["auth"].identity == "sessionuser"

    async def test_header_wins_over_session_through_the_chain(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        session = server.session_store.create(avatar=Avatar("sessionuser"))
        _, sent = await http_get(
            server, basic_header("alice", "wonderland"), cookie=f"session_id={session.id}"
        )
        assert response_body(sent) == b"alice"

    async def test_jwt_null_tags_claim_yields_empty_tags(self) -> None:
        # B3: a validly signed token with "tags": null normalizes to empty tags.
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        token = jwt.encode({"sub": "carol", "tags": None}, "topsecret", algorithm="HS256")
        scope, sent = await http_get(server, f"Bearer {token}")
        assert response_status(sent) == 200
        assert scope["auth"].tags == []

    async def test_malformed_header_is_401(self) -> None:
        server = AuthServer(applications=[EchoAuthApp(mount="")], auth=AUTH_CONFIG)
        _, sent = await http_get(server, "Basicabc123")
        assert response_status(sent) == 401


# --- composition without the auth capability ---


class TestWithoutAuthMixin:
    def test_base_server_authenticate_is_none(self) -> None:
        server = BaseServer(applications=[EchoAuthApp(mount="")])
        assert server.authenticate({"headers": []}) is None

    def test_session_only_composition_authenticate_is_none(self) -> None:
        class SessionOnly(SessionMixin, MiddlewareMixin, BaseServer):
            pass

        server = SessionOnly(applications=[EchoAuthApp(mount="")])
        assert server.authenticate({"headers": []}) is None


def encrypted_server(tmp_path, **kwargs) -> AsgiServer:
    """An ``AsgiServer`` whose site storage carries key material, for store wiring."""
    return AsgiServer(
        applications=[(BaseApplication, {"mount": ""})],
        storage=site_mounts(tmp_path),
        storage_key=Fernet.generate_key().decode(),
        **kwargs,
    )


class TestStoreWiring:
    def test_stores_are_none_when_unconfigured(self) -> None:
        server = AsgiServer(applications=[(BaseApplication, {"mount": ""})])
        assert server.user_store is None
        assert server.api_key_store is None

    def test_users_config_dict_builds_a_file_store(self, tmp_path: Path) -> None:
        server = encrypted_server(tmp_path, users={})
        assert isinstance(server.user_store, FileUserStore)
        assert server.api_key_store is None

    def test_tokens_config_dict_builds_a_file_store(self, tmp_path: Path) -> None:
        server = encrypted_server(tmp_path, tokens={})
        assert isinstance(server.api_key_store, FileApiKeyStore)

    def test_config_dict_honours_mount_and_prefix(self, tmp_path: Path) -> None:
        server = encrypted_server(tmp_path, users={"prefix": "people"})
        server.user_store.save(
            {"identity": "bob", "password_hash": "x", "tags": [], "enabled": True}
        )
        node = server.storage.node("site:people/bob.json")
        assert node.exists()

    def test_the_declared_class_is_the_store_the_server_builds(self) -> None:
        """#91: the configuration names the CLASS; the server builds it."""
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            users={"store_class": MemoryUserStore},
        )
        assert isinstance(server.user_store, MemoryUserStore)

    def test_config_dict_without_storage_is_a_boot_error(self) -> None:
        class NoStorageServer(AuthMixin, MiddlewareMixin, BaseServer):
            pass

        with pytest.raises(RuntimeError, match="storage"):
            NoStorageServer(applications=[BaseApplication(mount="")], users={})


class TestTheServerCreatesNoUser:
    """#91: the server seeds no identity — the declared store carries the records."""

    def test_a_fresh_store_is_empty(self) -> None:
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            users={"store_class": MemoryUserStore},
        )
        assert server.user_store.load_all() == []

    def test_the_declared_store_class_is_what_carries_an_identity(self) -> None:
        class AdminStore(MemoryUserStore):
            """The store the configuration names, born with its administrator."""

            def __init__(self, storage: object = None) -> None:
                super().__init__(storage)
                self.save(
                    {
                        "identity": "admin",
                        "password_hash": self.hash_password("pw"),
                        "tags": ["SUPERADMIN", "SERVER_ADMIN"],
                        "enabled": True,
                    }
                )

        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            users={"store_class": AdminStore},
        )
        assert server.user_store.verify("admin", "pw") is not None
        assert server.user_store.get("admin")["tags"] == ["SUPERADMIN", "SERVER_ADMIN"]

    def test_the_bootstrap_password_is_no_longer_a_kwarg(self) -> None:
        with pytest.raises(TypeError, match="admin_password"):
            AsgiServer(
                applications=[(BaseApplication, {"mount": ""})],
                users={"store_class": MemoryUserStore},
                admin_password="pw",
            )


def bearer_scope(token: str) -> Scope:
    """A scope carrying ``Authorization: Bearer <token>``."""
    return {"headers": [(b"authorization", f"Bearer {token}".encode())]}


class TestApiKeyBearer:
    def _core_with_key(self, tags: list[str]) -> tuple[AuthCore, str, MemoryApiKeyStore]:
        store = MemoryApiKeyStore()
        key = store.issue("ci-bot", tags)
        return AuthCore(api_key_store=store), key, store

    def test_valid_gak_key_authenticates_with_label_identity(self) -> None:
        core, key, _ = self._core_with_key(["ci", "deploy"])
        avatar = core.authenticate(bearer_scope(key))
        assert avatar is not None
        assert avatar.identity == "ci-bot"  # identity is the key label
        assert avatar.tags == ["ci", "deploy"]

    def test_revoked_key_is_unauthorized_not_a_jwt_fallthrough(self) -> None:
        core, key, store = self._core_with_key(["ci"])
        key_id = key[len("gak_") :].partition("_")[0]
        store.revoke(key_id)
        with pytest.raises(HTTPUnauthorized):
            core.authenticate(bearer_scope(key))

    def test_unknown_gak_key_is_unauthorized(self) -> None:
        core, _, _ = self._core_with_key(["ci"])
        with pytest.raises(HTTPUnauthorized):
            core.authenticate(bearer_scope("gak_deadbeef_nonexistentsecret"))

    def test_gak_key_with_no_store_is_unauthorized(self) -> None:
        core = AuthCore()  # no api_key_store wired
        with pytest.raises(HTTPUnauthorized):
            core.authenticate(bearer_scope("gak_deadbeef_secret"))

    def test_non_gak_bearer_still_uses_the_static_chain(self) -> None:
        store = MemoryApiKeyStore()
        core = AuthCore(
            bearer={"svc": {"token": "sk_live_xyz", "tags": "service"}},
            api_key_store=store,
        )
        avatar = core.authenticate(bearer_scope("sk_live_xyz"))
        assert avatar is not None
        assert avatar.identity == "svc"
        assert avatar.tags == ["service"]

    def test_end_to_end_gak_key_authenticates_through_the_server(self) -> None:
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            tokens={"store_class": MemoryApiKeyStore},
        )
        key = server.api_key_store.issue("robot", ["worker"])
        avatar = server.auth_core.authenticate(bearer_scope(key))
        assert avatar is not None
        assert avatar.identity == "robot"
        assert avatar.tags == ["worker"]
