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

"""Contract tests of the OIDC login of ``AuthApplicationBase``.

Every test drives a real ``AsgiServer`` at the ASGI level, with
``AuthApplicationBase`` mounted at ``/_auth`` over an encrypted ``site:`` mount
in a temporary directory, and one provider declared under code ``google``.
The provider is fake and lives in the process: discovery and the token
endpoint replace ``httpx.AsyncClient``, the JWKS client replaces
``jwt.PyJWKClient`` (it records the thread that resolves a key), and a
module RSA key signs the ``id_token``. Users and
groups are seeded through the application's public ``store``. The tests read
what a caller sees: HTTP status, ``Location``, JSON answers, the session's
avatar, the records of the store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import start_server
from tests.storage_support import site_mounts

from kajenn import AsgiConfigBuilder, AsgiServer, BaseApplication
from kajenn.auth_app import AuthApplicationBase, AuthStore
from kajenn.types import Message, Scope

ISSUER = "https://accounts.example.com"
CLIENT_ID = "client-123"
EXTERNAL_URL = "https://shop.example.com"
START = "/_auth/oidc:google/start"
CALLBACK = "/_auth/oidc:google/callback"

DISCOVERY_DOC = {
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/jwks",
}

PROVIDER = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": "s3cret"}

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_id_token(private_key: Any = SIGNING_KEY, **claims: Any) -> str:
    """A signed RS256 id_token; ``claims`` override the defaults (``None`` removes one)."""
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "alice@example.com",
        "email_verified": True,
    }
    payload.update(claims)
    payload = {name: value for name, value in payload.items() if value is not None}
    return jwt.encode(payload, private_key, algorithm="RS256")


class FakeSigningKey:
    """Stand-in for a ``jwt.PyJWK``: the public key as ``.key``."""

    def __init__(self, key: Any) -> None:
        self.key = key


class FakePyJWKClient:
    """Stand-in for ``jwt.PyJWKClient``: every token resolves to the module key."""

    instances = 0
    resolving_threads: list[int] = []

    def __init__(self, uri: str) -> None:
        type(self).instances += 1

    def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
        type(self).resolving_threads.append(threading.get_ident())
        return FakeSigningKey(SIGNING_KEY.public_key())


class FakeResponse:
    """Stand-in for an ``httpx.Response`` with a JSON payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient``: GET answers discovery, POST the token set.

    ``id_token`` is what the token endpoint answers next; ``token_response``
    replaces the whole answer when set. ``posted`` keeps every exchange form,
    ``fetched`` every url asked with GET.
    """

    id_token = ""
    token_response: dict[str, Any] = {}
    posted: list[dict[str, Any]] = []
    fetched: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def get(self, url: str) -> FakeResponse:
        type(self).fetched.append(url)
        return FakeResponse(DISCOVERY_DOC)

    async def post(self, url: str, data: dict[str, Any] | None = None) -> FakeResponse:
        type(self).posted.append(dict(data or {}))
        return FakeResponse(self.token_response or {"id_token": self.id_token or make_id_token()})


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake provider at the httpx and PyJWKClient boundaries, reset per test."""
    monkeypatch.setattr(FakeAsyncClient, "id_token", "")
    monkeypatch.setattr(FakeAsyncClient, "token_response", {})
    monkeypatch.setattr(FakeAsyncClient, "posted", [])
    monkeypatch.setattr(FakeAsyncClient, "fetched", [])
    monkeypatch.setattr(FakePyJWKClient, "instances", 0)
    monkeypatch.setattr(FakePyJWKClient, "resolving_threads", [])
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(jwt, "PyJWKClient", FakePyJWKClient)


def make_server(
    tmp_path: Path, external_url: str | None = EXTERNAL_URL, **app_kwargs: Any
) -> AsgiServer:
    """A server with ``_auth`` mounted; ``app_kwargs`` go to the application."""
    app_kwargs.setdefault("oidc", {"google": PROVIDER})
    server_kwargs: dict[str, Any] = {}
    if external_url is not None:
        server_kwargs["external_url"] = external_url
    return AsgiServer(
        applications=[(AuthApplicationBase, app_kwargs), (BaseApplication, {"mount": ""})],
        storage=site_mounts(tmp_path),
        storage_key=Fernet.generate_key().decode(),
        **server_kwargs,
    )


def auth_store(server: AsgiServer) -> AuthStore:
    """The store of the mounted ``_auth`` application."""
    store: AuthStore = server.applications["_auth"].store
    return store


def user(identity: str, **fields: Any) -> dict[str, Any]:
    """A complete user record without password; ``fields`` override the defaults."""
    record: dict[str, Any] = {
        "identity": identity,
        "groups": [],
        "primary_group": None,
        "tags": [],
        "status": "confirmed",
        "system": False,
        "password_hash": None,
        "email": None,
        "external_ids": [],
    }
    record.update(fields)
    return record


def seed(server: AsgiServer) -> None:
    """Groups ``staff`` and ``ops`` and ``alice`` (alice@example.com) in both, primary ``staff``."""
    store = auth_store(server)
    store.save_group({"code": "staff", "tags": ["STAFF_READ"]})
    store.save_group({"code": "ops", "tags": ["OPS_DEPLOY"]})
    store.save_user(
        user(
            "alice",
            groups=["staff", "ops"],
            primary_group="staff",
            tags=["OWN"],
            email="alice@example.com",
        )
    )


async def started(tmp_path: Path, **app_kwargs: Any) -> AsgiServer:
    """A started, seeded server."""
    server = make_server(tmp_path, **app_kwargs)
    await start_server(server)
    seed(server)
    return server


async def drive(server: AsgiServer, path: str, cookie: str | None = None) -> list[Message]:
    """Drive one GET through ``server`` at the ASGI level."""
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    path, _, query = path.partition("?")
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "headers": headers,
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def status(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def location(sent: list[Message]) -> str:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"])[b"location"].decode()


def location_query(sent: list[Message]) -> dict[str, list[str]]:
    return parse_qs(urlparse(location(sent)).query)


def payload(sent: list[Message]) -> Any:
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(body)


async def begin_flow(server: AsgiServer, next_target: str = "/app/page") -> tuple[str, str]:
    """Run ``start`` for a fresh session; ``(cookie, state)`` from the redirect."""
    session = server.session_store.create()
    cookie = f"session_id={session.id}"
    sent = await drive(server, f"{START}?next={next_target}", cookie=cookie)
    return cookie, location_query(sent)["state"][0]


async def oidc_login(server: AsgiServer, **claims: Any) -> tuple[str, list[Message]]:
    """A whole round trip with an id_token carrying ``claims``; ``(cookie, callback answer)``."""
    FakeAsyncClient.id_token = make_id_token(**claims)
    cookie, state = await begin_flow(server)
    sent = await drive(server, f"{CALLBACK}?state={state}&code=auth-code", cookie=cookie)
    return cookie, sent


def session_data(server: AsgiServer, cookie: str) -> Any:
    """The data of the session named by ``cookie``."""
    return server.session_store.get(cookie.removeprefix("session_id=")).data


def s256(verifier: str) -> str:
    """The S256 PKCE challenge of ``verifier``, computed as RFC 7636 4.2 writes it."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def avatar_of(server: AsgiServer, cookie: str) -> Any:
    """The avatar of the session named by ``cookie`` (None when nothing is attached)."""
    session = server.session_store.get(cookie.removeprefix("session_id="))
    return session.avatar()


class TestLoginMethods:
    async def test_a_declared_provider_is_listed_after_the_password(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        methods = payload(await drive(server, "/_auth/login_methods"))["methods"]
        assert [m["id"] for m in methods] == ["password", "oidc:google"]
        assert methods[1] == {
            "id": "oidc:google",
            "kind": "redirect",
            "label": "Sign in with google",
            "url": START,
        }

    async def test_a_site_without_providers_has_no_oidc_route(self, tmp_path: Path) -> None:
        server = await started(tmp_path, oidc={})
        methods = payload(await drive(server, "/_auth/login_methods"))["methods"]
        assert [m["id"] for m in methods] == ["password"]
        session = server.session_store.create()
        cookie = f"session_id={session.id}"
        assert status(await drive(server, START, cookie=cookie)) == 404
        assert status(await drive(server, f"{CALLBACK}?state=x&code=y", cookie=cookie)) == 404


class TestStart:
    async def test_start_redirects_to_the_provider_with_the_callback_under_auth(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        session = server.session_store.create()
        sent = await drive(server, f"{START}?next=/app/page", cookie=f"session_id={session.id}")
        assert status(sent) == 302
        assert location(sent).startswith(f"{ISSUER}/authorize?")
        query = location_query(sent)
        assert query["client_id"] == [CLIENT_ID]
        assert query["redirect_uri"] == [f"{EXTERNAL_URL}{CALLBACK}"]
        assert query["scope"] == ["openid email profile"]
        assert query["code_challenge_method"] == ["S256"]
        assert "client_secret" not in query

    async def test_the_token_exchange_repeats_the_redirect_uri(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        await oidc_login(server)
        exchanged = FakeAsyncClient.posted[-1]
        assert exchanged["redirect_uri"] == f"{EXTERNAL_URL}{CALLBACK}"
        assert exchanged["client_secret"] == "s3cret"

    async def test_the_code_verifier_of_the_exchange_matches_the_challenge_of_start(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        session = server.session_store.create()
        cookie = f"session_id={session.id}"
        sent = await drive(server, START, cookie=cookie)
        query = location_query(sent)
        await drive(server, f"{CALLBACK}?state={query['state'][0]}&code=c", cookie=cookie)
        verifier = FakeAsyncClient.posted[-1]["code_verifier"]
        assert query["code_challenge"] == [s256(verifier)]

    async def test_the_pkce_challenge_matches_the_rfc_7636_vector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # RFC 7636 Appendix B: a fixed verifier and its challenge, so an
        # implementation that is wrong but self-consistent cannot pass.
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        server = await started(tmp_path)
        session = server.session_store.create()
        cookie = f"session_id={session.id}"
        with monkeypatch.context() as fixed:
            fixed.setattr(secrets, "token_urlsafe", lambda nbytes=None: verifier)
            sent = await drive(server, START, cookie=cookie)
        query = location_query(sent)
        assert query["code_challenge"] == ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"]
        await drive(server, f"{CALLBACK}?state={query['state'][0]}&code=c", cookie=cookie)
        assert FakeAsyncClient.posted[-1]["code_verifier"] == verifier

    async def test_start_keeps_state_verifier_and_next_in_the_session(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        session = server.session_store.create()
        cookie = f"session_id={session.id}"
        sent = await drive(server, f"{START}?next=/app/page", cookie=cookie)
        data = session_data(server, cookie)
        assert data["oidc.oidc:google.state"] == location_query(sent)["state"][0]
        assert data["oidc.oidc:google.verifier"]
        assert data["oidc.oidc:google.next"] == "/app/page"

    async def test_the_callback_clears_the_one_shot_flow_keys(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server)
        assert status(sent) == 302
        data = session_data(server, cookie)
        for suffix in ("state", "verifier", "next"):
            assert f"oidc.oidc:google.{suffix}" not in data

    async def test_an_external_url_trailing_slash_does_not_double_the_separator(
        self, tmp_path: Path
    ) -> None:
        server = make_server(tmp_path, external_url=f"{EXTERNAL_URL}/")
        await start_server(server)
        session = server.session_store.create()
        sent = await drive(server, START, cookie=f"session_id={session.id}")
        assert location_query(sent)["redirect_uri"] == [f"{EXTERNAL_URL}{CALLBACK}"]

    async def test_an_unsafe_next_lands_on_the_root(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        FakeAsyncClient.id_token = make_id_token()
        cookie, state = await begin_flow(server, next_target="//evil.example")
        sent = await drive(server, f"{CALLBACK}?state={state}&code=c", cookie=cookie)
        assert status(sent) == 302
        assert location(sent) == "/"


class TestLinkedUser:
    async def test_a_linked_user_gets_group_and_tags_of_the_auth_store(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server)
        assert status(sent) == 302
        assert location(sent) == "/app/page"
        avatar = avatar_of(server, cookie)
        assert (avatar.identity, avatar.group, avatar.tags) == (
            "alice",
            "staff",
            ["OWN", "STAFF_READ"],
        )

    async def test_the_first_login_writes_iss_and_sub_on_the_record(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        await oidc_login(server)
        record = auth_store(server).get_user("alice")
        assert record["external_ids"] == [{"iss": ISSUER, "sub": "google-sub-1"}]

    async def test_the_email_is_compared_lowercased(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server, email="ALICE@Example.com")
        assert status(sent) == 302
        assert avatar_of(server, cookie).identity == "alice"

    async def test_the_second_login_finds_the_user_by_iss_and_sub_with_a_new_email(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        await oidc_login(server)
        cookie, sent = await oidc_login(server, email="alice.new@example.com")
        assert status(sent) == 302
        avatar = avatar_of(server, cookie)
        assert (avatar.identity, avatar.group) == ("alice", "staff")

    async def test_iss_and_sub_win_even_without_a_verified_email(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        await oidc_login(server)
        cookie, sent = await oidc_login(server, email=None, email_verified=None)
        assert status(sent) == 302
        assert avatar_of(server, cookie).identity == "alice"

    async def test_the_callback_does_not_touch_the_lockout_counter(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        record = {"identity": "alice", "failed_attempts": 2, "last_failed_at": 123.0}
        store.save_login_failures(record)
        await oidc_login(server)
        after = store.get_login_failures("alice")
        assert (after["failed_attempts"], after["last_failed_at"]) == (2, 123.0)


class TestRefusedLink:
    async def test_an_email_not_verified_is_refused_and_nothing_is_written(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server, email_verified=False)
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert auth_store(server).get_user("alice")["external_ids"] == []

    async def test_two_users_with_the_email_are_refused_and_nothing_is_written(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        store.save_user(user("alice2", email="alice@example.com"))
        cookie, sent = await oidc_login(server)
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert store.get_user("alice")["external_ids"] == []
        assert store.get_user("alice2")["external_ids"] == []

    async def test_a_banned_user_is_refused(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        record = store.get_user("alice")
        record["status"] = "banned"
        store.save_user(record)
        cookie, sent = await oidc_login(server)
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert store.get_user("alice")["external_ids"] == []

    async def test_a_user_banned_after_the_link_is_refused(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        await oidc_login(server)
        store = auth_store(server)
        record = store.get_user("alice")
        record["status"] = "banned"
        store.save_user(record)
        cookie, sent = await oidc_login(server)
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None

    async def test_a_system_user_is_refused_and_not_written(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        store = auth_store(server)
        store.save_user(user("robot", system=True, email="robot@example.com"))
        cookie, sent = await oidc_login(server, email="robot@example.com", sub="robot-sub")
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert store.get_user("robot")["external_ids"] == []


class TestUnknownUser:
    async def test_without_self_registration_an_unknown_user_is_refused(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server, email="bob@example.com", sub="bob-sub")
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert auth_store(server).get_user("bob@example.com") is None

    @pytest.mark.parametrize(
        "setting",
        [{"tags": ["GUEST"]}, {"status": "active", "tags": ["GUEST"]}],
    )
    async def test_a_self_registration_with_a_wrong_status_is_not_applied(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, setting: dict[str, Any]
    ) -> None:
        with caplog.at_level(logging.WARNING):
            server = await started(tmp_path, self_registration=setting)
        assert "self_registration" in caplog.text
        cookie, sent = await oidc_login(server, email="bob@example.com", sub="bob-sub")
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert auth_store(server).get_user("bob@example.com") is None

    async def test_with_status_waiting_the_record_is_born_and_the_user_does_not_enter(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path, self_registration={"status": "waiting", "tags": ["GUEST"]})
        cookie, sent = await oidc_login(server, email="bob@example.com", sub="bob-sub")
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert auth_store(server).get_user("bob@example.com") == user(
            "bob@example.com",
            status="waiting",
            tags=["GUEST"],
            email="bob@example.com",
            external_ids=[{"iss": ISSUER, "sub": "bob-sub"}],
        )

    async def test_with_status_confirmed_the_record_is_born_with_the_tags_and_enters(
        self, tmp_path: Path
    ) -> None:
        server = await started(
            tmp_path, self_registration={"status": "confirmed", "tags": ["GUEST"]}
        )
        cookie, sent = await oidc_login(server, email="bob@example.com", sub="bob-sub")
        assert status(sent) == 302
        avatar = avatar_of(server, cookie)
        assert (avatar.identity, avatar.group, avatar.tags) == ("bob@example.com", None, ["GUEST"])
        record = auth_store(server).get_user("bob@example.com")
        assert (record["status"], record["tags"]) == ("confirmed", ["GUEST"])

    async def test_a_registered_user_is_found_again_by_iss_and_sub(self, tmp_path: Path) -> None:
        server = await started(tmp_path, self_registration={"status": "confirmed", "tags": []})
        await oidc_login(server, email="bob@example.com", sub="bob-sub")
        cookie, sent = await oidc_login(server, email="bob.new@example.com", sub="bob-sub")
        assert status(sent) == 302
        assert avatar_of(server, cookie).identity == "bob@example.com"
        assert auth_store(server).get_user("bob.new@example.com") is None

    async def test_an_identity_already_taken_is_refused_and_nothing_is_written(
        self, tmp_path: Path
    ) -> None:
        server = await started(tmp_path, self_registration={"status": "confirmed", "tags": []})
        store = auth_store(server)
        taken = user("bob@example.com", email="someone@example.com")
        store.save_user(taken)
        cookie, sent = await oidc_login(server, email="bob@example.com", sub="bob-sub")
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert store.get_user("bob@example.com") == taken

    async def test_without_a_verified_email_nothing_is_registered(self, tmp_path: Path) -> None:
        server = await started(tmp_path, self_registration={"status": "confirmed", "tags": []})
        cookie, sent = await oidc_login(
            server, email="bob@example.com", sub="bob-sub", email_verified=None
        )
        assert status(sent) == 403
        assert avatar_of(server, cookie) is None
        assert auth_store(server).get_user("bob@example.com") is None


class TestFlowErrors:
    async def test_a_state_different_from_the_session_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, _ = await begin_flow(server)
        sent = await drive(server, f"{CALLBACK}?state=forged&code=c", cookie=cookie)
        assert status(sent) == 400
        assert avatar_of(server, cookie) is None

    async def test_a_missing_state_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, _ = await begin_flow(server)
        assert status(await drive(server, f"{CALLBACK}?code=c", cookie=cookie)) == 400

    async def test_a_callback_without_start_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        session = server.session_store.create()
        cookie = f"session_id={session.id}"
        assert status(await drive(server, f"{CALLBACK}?state=x&code=c", cookie=cookie)) == 400

    async def test_a_token_answer_without_id_token_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        FakeAsyncClient.token_response = {"access_token": "opaque"}
        cookie, state = await begin_flow(server)
        sent = await drive(server, f"{CALLBACK}?state={state}&code=c", cookie=cookie)
        assert status(sent) == 400

    async def test_an_id_token_with_a_wrong_signature_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cookie, sent = await oidc_login(server, private_key=other_key)
        assert status(sent) == 400
        assert avatar_of(server, cookie) is None

    async def test_an_id_token_for_another_audience_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server, aud="someone-else")
        assert status(sent) == 400
        assert avatar_of(server, cookie) is None

    async def test_an_id_token_from_another_issuer_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server, iss="https://evil.example")
        assert status(sent) == 400
        assert avatar_of(server, cookie) is None

    async def test_an_id_token_without_sub_answers_400(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        cookie, sent = await oidc_login(server, sub=None)
        assert status(sent) == 400
        assert avatar_of(server, cookie) is None

    async def test_the_jwks_client_is_built_once(self, tmp_path: Path) -> None:
        server = await started(tmp_path)
        for _ in range(2):
            _, sent = await oidc_login(server)
            assert status(sent) == 302
        assert FakePyJWKClient.instances == 1

    async def test_the_signing_key_is_resolved_off_the_event_loop(self, tmp_path: Path) -> None:
        # The real client fetches the key set with a blocking stdlib call.
        server = await started(tmp_path)
        _, sent = await oidc_login(server)
        assert status(sent) == 302
        assert FakePyJWKClient.resolving_threads
        assert threading.get_ident() not in FakePyJWKClient.resolving_threads


class TestConfiguration:
    async def test_without_external_url_the_oidc_routes_are_not_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The server boots; start and callback answer 404 (kajenn's mapping
        # of not_available) and the provider is never contacted.
        monkeypatch.delenv("KAJENN_EXTERNAL_URL", raising=False)
        server = make_server(tmp_path, external_url=None)
        await start_server(server)
        assert server.external_url is None
        session = server.session_store.create()
        cookie = f"session_id={session.id}"
        assert status(await drive(server, f"{START}?next=/app/page", cookie=cookie)) == 404
        assert status(await drive(server, f"{CALLBACK}?state=x&code=y", cookie=cookie)) == 404
        assert FakeAsyncClient.fetched == []

    async def test_without_external_url_login_methods_leaves_the_providers_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KAJENN_EXTERNAL_URL", raising=False)
        server = make_server(tmp_path, external_url=None)
        await start_server(server)
        methods = payload(await drive(server, "/_auth/login_methods"))["methods"]
        assert [method["kind"] for method in methods] == ["form"]

    async def test_the_boot_does_not_fetch_the_discovery(self, tmp_path: Path) -> None:
        # An unreachable issuer does not stop the boot: the metadata is fetched
        # at the first start, not when the server is built or started.
        server = make_server(
            tmp_path, oidc={"google": {"issuer": "https://unreachable.invalid", "client_id": "x"}}
        )
        await start_server(server)
        assert FakeAsyncClient.fetched == []
        session = server.session_store.create()
        await drive(server, START, cookie=f"session_id={session.id}")
        assert FakeAsyncClient.fetched == [
            "https://unreachable.invalid/.well-known/openid-configuration"
        ]

    def test_a_site_without_providers_needs_no_external_url(self, tmp_path: Path) -> None:
        server = make_server(tmp_path, external_url=None, oidc={})
        assert "_auth" in server.applications

    async def test_the_recipe_declares_provider_and_self_registration(self, tmp_path: Path) -> None:
        class SiteWithOidc(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                apps.application(code="home", mount="", app_class=BaseApplication)
                auth_app = apps.application(code="_auth", app_class=AuthApplicationBase)
                auth_app.oidc().provider(code="google", issuer=ISSUER, client_id=CLIENT_ID)
                auth_app.self_registration(status="confirmed", tags=["GUEST"])

        server = AsgiServer(
            SiteWithOidc,
            storage=site_mounts(tmp_path),
            storage_key=Fernet.generate_key().decode(),
            external_url=EXTERNAL_URL,
        )
        await start_server(server)
        methods = payload(await drive(server, "/_auth/login_methods"))["methods"]
        assert [m["id"] for m in methods] == ["password", "oidc:google"]
        cookie, sent = await oidc_login(server, email="bob@example.com", sub="bob-sub")
        assert status(sent) == 302
        avatar = avatar_of(server, cookie)
        assert (avatar.identity, avatar.tags) == ("bob@example.com", ["GUEST"])

    async def test_a_recipe_provider_without_external_url_is_not_available(
        self, tmp_path: Path
    ) -> None:
        class SiteWithOidc(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                apps.application(code="home", mount="", app_class=BaseApplication)
                auth_app = apps.application(code="_auth", app_class=AuthApplicationBase)
                auth_app.oidc().provider(code="google", issuer=ISSUER, client_id=CLIENT_ID)

        server = AsgiServer(
            SiteWithOidc,
            storage=site_mounts(tmp_path),
            storage_key=Fernet.generate_key().decode(),
        )
        await start_server(server)
        session = server.session_store.create()
        sent = await drive(server, START, cookie=f"session_id={session.id}")
        assert status(sent) == 404
        assert FakeAsyncClient.fetched == []
