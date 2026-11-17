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

"""Auth tests: header verification and §5.5 identity precedence, asked to ``_auth``.

Header verification (Basic and ``gak_`` Bearer, wrong credentials → 401) is
driven at the ASGI level through an ``AsgiServer`` that mounts
``AuthApplicationBase`` at ``/_auth``: the execution point verifies the header
through ``/_auth/authenticate``. The precedence — the header first, an invalid
header a 401, then the session's root identity asked to ``/_auth/avatar`` — is
exercised both by awaiting ``server.authenticate(scope)`` and END-TO-END
through the session middleware and the execution point.
"""

from __future__ import annotations

import base64

import pytest
from genro_routes import route

from tests.storage_support import add_user, auth_application, issue_token

from kajenn import (
    AsgiServer,
    Avatar,
    BaseApplication,
    BaseServer,
    RoutedApplication,
    Session,
    SessionMixin,
)
from kajenn.exceptions import HTTPUnauthorized
from kajenn.middleware import MiddlewareMixin
from kajenn.types import Message, Scope
from tests.conftest import start_server


def basic_header(username: str, password: str) -> str:
    """The value of a Basic ``Authorization`` header for these credentials."""
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {raw}"


class EchoAuthApp(RoutedApplication):
    """Echoes the identity the execution point resolved for the request."""

    @route(media_type="text/plain")
    def whoami(self, _request=None) -> str:
        avatar = _request.avatar()
        return avatar.identity if avatar is not None else "anonymous"


async def auth_server() -> AsgiServer:
    """A started server: ``_auth`` verifies the header, sessions armed.

    The store holds alice (password ``wonderland``, tags admin and ops), the
    service user svc (tag api) and sessionuser (tag member).
    """
    server = AsgiServer(applications=[auth_application(), (EchoAuthApp, {"code": "echo"})])
    await start_server(server)
    add_user(server, "alice", ["admin", "ops"], password="wonderland")
    add_user(server, "svc", ["api"], system=True)
    add_user(server, "sessionuser", ["member"])
    return server


async def http_get(
    server: BaseServer, authorization: str | None = None, cookie: str | None = None
) -> tuple[Scope, list[Message]]:
    """Drive one GET through ``server`` at the ASGI level; return the scope and what it sent."""
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    scope: Scope = {"type": "http", "method": "GET", "path": "/echo/whoami", "headers": headers}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await start_server(server)
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
        _, sent = await http_get(await auth_server(), basic_header("alice", "wonderland"))
        assert response_status(sent) == 200
        assert response_body(sent) == b"alice"

    async def test_bearer_ok(self) -> None:
        server = await auth_server()
        _, sent = await http_get(server, f"Bearer {issue_token(server, 'svc')}")
        assert response_body(sent) == b"svc"

    async def test_wrong_password_yields_401(self) -> None:
        _, sent = await http_get(await auth_server(), basic_header("alice", "nope"))
        assert response_status(sent) == 401

    async def test_invalid_credentials_401_carries_www_authenticate(self) -> None:
        _, sent = await http_get(await auth_server(), basic_header("alice", "nope"))
        assert response_status(sent) == 401
        assert header_value(sent, b"www-authenticate") == b"Bearer"

    async def test_malformed_credentials_401_carries_www_authenticate(self) -> None:
        _, sent = await http_get(await auth_server(), "Basicabc123")
        assert response_status(sent) == 401
        assert header_value(sent, b"www-authenticate") == b"Bearer"

    async def test_no_header_is_anonymous(self) -> None:
        _, sent = await http_get(await auth_server())
        assert response_body(sent) == b"anonymous"


# --- identity precedence (server.authenticate, awaited by the execution point) ---


def scope_with(session: Session | None, authorization: str | None) -> Scope:
    """A REST scope carrying an attached session and/or an Authorization header."""
    headers = [(b"authorization", authorization.encode())] if authorization is not None else []
    scope: Scope = {
        "type": "http", "method": "GET", "path": "/", "headers": headers, "kajenn.channel": "api",
    }
    if session is not None:
        scope["session"] = session
    return scope


class TestIdentityPrecedence:
    async def test_header_wins_over_session(self) -> None:
        session = Session("sid", avatar=Avatar("sessionuser"), ttl=3600)
        scope = scope_with(session, basic_header("alice", "wonderland"))
        avatar = await (await auth_server()).authenticate(scope)
        assert avatar.identity == "alice"

    async def test_invalid_header_is_401_even_with_a_logged_in_session(self) -> None:
        session = Session("sid", avatar=Avatar("sessionuser"), ttl=3600)
        scope = scope_with(session, basic_header("alice", "nope"))
        with pytest.raises(HTTPUnauthorized):
            await (await auth_server()).authenticate(scope)

    async def test_invalid_header_on_an_anonymous_session_is_401(self) -> None:
        session = Session("sid", avatar=None, ttl=3600)
        scope = scope_with(session, basic_header("alice", "nope"))
        with pytest.raises(HTTPUnauthorized):
            await (await auth_server()).authenticate(scope)

    async def test_header_on_an_anonymous_session(self) -> None:
        session = Session("sid", avatar=None, ttl=3600)
        avatar = await (await auth_server()).authenticate(
            scope_with(session, basic_header("alice", "wonderland"))
        )
        assert avatar.identity == "alice"

    async def test_no_header_falls_back_to_session(self) -> None:
        session = Session("sid", avatar=Avatar("sessionuser", ["member"]), ttl=3600)
        avatar = await (await auth_server()).authenticate(scope_with(session, None))
        assert avatar.identity == "sessionuser"
        assert avatar.tags == ["member"]

    async def test_no_header_anonymous_session_is_none(self) -> None:
        session = Session("sid", avatar=None, ttl=3600)
        assert await (await auth_server()).authenticate(scope_with(session, None)) is None

    async def test_no_header_no_session_is_none(self) -> None:
        assert await (await auth_server()).authenticate(scope_with(None, None)) is None


# --- end-to-end through the chain and the execution point ---


class TestCombinedChainFlow:
    async def test_header_auth_without_cookie_gets_anonymous_session(self) -> None:
        # B1: header identity resolved, anonymous session, Set-Cookie, no 500.
        _, sent = await http_get(await auth_server(), basic_header("alice", "wonderland"))
        assert response_status(sent) == 200
        assert response_body(sent) == b"alice"
        assert set_cookie_value(sent) is not None

    async def test_session_identity_flows_through_the_chain(self) -> None:
        # B2: cookie only — the session identity reaches the handler.
        server = await auth_server()
        session = server.session_store.create(avatar=Avatar("sessionuser", ["member"]))
        _, sent = await http_get(server, cookie=f"session_id={session.id}")
        assert response_status(sent) == 200
        assert response_body(sent) == b"sessionuser"

    async def test_header_wins_over_session_through_the_chain(self) -> None:
        server = await auth_server()
        session = server.session_store.create(avatar=Avatar("sessionuser"))
        _, sent = await http_get(
            server, basic_header("alice", "wonderland"), cookie=f"session_id={session.id}"
        )
        assert response_body(sent) == b"alice"

    async def test_malformed_header_is_401(self) -> None:
        _, sent = await http_get(await auth_server(), "Basicabc123")
        assert response_status(sent) == 401


# --- composition without the auth capability ---


class TestWithoutAuthMixin:
    async def test_base_server_authenticate_is_none(self) -> None:
        server = BaseServer(applications=[EchoAuthApp(mount="")])
        assert await server.authenticate({"headers": []}) is None

    async def test_session_only_composition_authenticate_is_none(self) -> None:
        class SessionOnly(SessionMixin, MiddlewareMixin, BaseServer):
            pass

        server = SessionOnly(applications=[EchoAuthApp(mount="")])
        assert await server.authenticate({"headers": []}) is None




class TestTheServerHoldsNoAuthentication:
    """The server takes no store, no credential and no user: ``_auth`` owns them."""

    @pytest.mark.parametrize("kwarg", ["auth", "users", "tokens", "channels", "admin_password"])
    def test_an_authentication_kwarg_is_refused(self, kwarg: str) -> None:
        with pytest.raises(TypeError, match=kwarg):
            AsgiServer(applications=[(BaseApplication, {"mount": ""})], **{kwarg: {}})
