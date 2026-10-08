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

"""Authentication on demand in the execution point; ``AuthMiddleware`` retired.

``execute`` resolves the identity before resolving the route: the session's
avatar if any, else the ``Authorization`` header verified through the channel's
route, else nobody. A credential presented is always verified — an invalid one
is a 401 on any route — and absence is not an error: a public route answers
anonymously, a ruled one answers a bare 401.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from genro_routes import route

import kajenn
from kajenn import AsgiServer, Avatar, BaseApplication, BaseServer, McpOpenApiApplication
from kajenn.middleware import default_registry
from kajenn.types import Message, Scope
from kajenn_server_app import ServerApplication

BEARER = {"bearer": {"svc": {"token": "sk_live_xyz", "tags": "admin"}}}


class Api(McpOpenApiApplication):
    @route(channel_channels="mcp,rest")
    def open(self, _request=None) -> dict[str, Any]:
        avatar = _request.avatar()
        return {"identity": avatar.identity if avatar is not None else None}

    @route(channel_channels="mcp,rest", auth_rule="admin")
    def secret(self, _request=None) -> dict[str, Any]:
        return {"identity": _request.avatar().identity}

    @route()
    def on_loop(self) -> dict[str, int]:
        async def answer() -> int:
            return 42

        return {"value": self.server.run_on_loop(answer())}


def api_server(**extra: Any) -> AsgiServer:
    return AsgiServer(
        applications=[ServerApplication, (BaseApplication, {"mount": ""}), (Api, {"code": "api"})],
        auth=BEARER,
        plugins={"openapi": True},
        **extra,
    )


async def drive(
    server: AsgiServer,
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[bytes, bytes], Any]:
    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [*(headers or []), *([(b"content-type", b"application/json")] if body else [])],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    data = json.loads(raw) if raw and start["status"] < 400 else None
    return start["status"], dict(start.get("headers", [])), data


def bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


class TestTheMiddlewareIsGone:
    def test_no_auth_middleware_anywhere(self) -> None:
        # wf:contract: the "auth" middleware is no longer in the default registry, is not
        # wf:contract: exported by kajenn.middleware nor kajenn.auth, and its module is gone.
        assert "auth" not in default_registry()
        assert not hasattr(kajenn.middleware, "AuthMiddleware")
        assert not hasattr(kajenn.auth, "AuthMiddleware")
        with pytest.raises(ImportError):
            __import__("kajenn.middleware.authentication")

    def test_authenticate_is_async_on_the_base_and_the_mixin(self) -> None:
        # wf:contract: server.authenticate(scope) is a coroutine function on BaseServer
        # wf:contract: and on AsgiServer; the base answers None.
        assert inspect.iscoroutinefunction(BaseServer.authenticate)
        assert inspect.iscoroutinefunction(AsgiServer.authenticate)


class TestRest:
    async def test_a_valid_bearer_opens_a_ruled_route(self) -> None:
        # wf:contract: a Bearer the default route (AuthCore via _server) knows gives the
        # wf:contract: avatar to the execution point: the ruled route answers 200.
        status, _, data = await drive(api_server(), "/api/secret", headers=bearer("sk_live_xyz"))
        assert (status, data) == (200, {"identity": "svc"})

    async def test_an_invalid_bearer_is_401_even_on_a_public_route(self) -> None:
        # wf:contract: a credential presented is always verified: an invalid one is a 401
        # wf:contract: with WWW-Authenticate, on a route without rule too.
        status, headers, _ = await drive(api_server(), "/api/open", headers=bearer("nope"))
        assert status == 401
        assert headers.get(b"www-authenticate") == b"Bearer"

    async def test_no_credential_on_a_public_route_is_anonymous(self) -> None:
        # wf:contract: absence of a credential is not an error: the public route answers
        # wf:contract: 200 with no identity.
        status, _, data = await drive(api_server(), "/api/open")
        assert (status, data) == (200, {"identity": None})

    async def test_no_credential_on_a_ruled_route_is_a_bare_401(self) -> None:
        # wf:contract: not_authenticated after the resolution is a 401 with the challenge;
        # wf:contract: there is no second resolution in 0.4.0.
        status, headers, _ = await drive(api_server(), "/api/secret")
        assert status == 401
        assert headers.get(b"www-authenticate") == b"Bearer"

    async def test_the_session_avatar_wins_without_header(self) -> None:
        # wf:contract: a session carrying an avatar (cookie session_id) authenticates the
        # wf:contract: request without any header; the ruled route answers 200.
        server = api_server()
        session = server.session_store.create(Avatar("sess", ["admin"]))
        cookie = [(b"cookie", f"session_id={session.id}".encode())]
        status, _, data = await drive(server, "/api/secret", headers=cookie)
        assert (status, data) == (200, {"identity": "sess"})

    async def test_the_cache_serves_the_second_request(self) -> None:
        # wf:contract: two requests with the same Bearer reach the authentication route
        # wf:contract: once; forget_credential makes the third reach it again.
        class Idp(McpOpenApiApplication):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.calls = 0

            @route()
            def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
                self.calls += 1
                return {"identity": "idp-user", "tags": ["admin"], "data": {"tenant": "t1"}}

        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""}), (Idp, {"code": "idp"}), (Api, {"code": "api"})],
            channels={"rest": {"authentication_route": "/idp/check"}},
        )
        idp = server.applications["idp"]
        for _ in range(2):
            status, _, data = await drive(server, "/api/open", headers=bearer("tok"))
            assert (status, data) == (200, {"identity": "idp-user"})
        assert idp.calls == 1
        server.forget_credential("Bearer tok")
        await drive(server, "/api/open", headers=bearer("tok"))
        assert idp.calls == 2


class TestMcp:
    async def test_a_tool_call_with_bearer_reaches_a_ruled_tool(self) -> None:
        # wf:contract: tools/call with an Authorization header authenticates on channel
        # wf:contract: "mcp" through the execution point; the ruled tool answers.
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "secret", "arguments": {}},
        }
        status, _, data = await drive(
            api_server(),
            "/api/mcp",
            method="POST",
            body=json.dumps(envelope).encode(),
            headers=bearer("sk_live_xyz"),
        )
        assert status == 200
        assert data["result"]["structuredContent"] == {"identity": "svc"}

    async def test_a_channel_route_authenticates_the_mcp_face(self) -> None:
        # wf:contract: channels={"mcp": {"authentication_route": "/idp/check"}} makes a
        # wf:contract: tools/call verify its Bearer through /idp/check, and the avatar's
        # wf:contract: data is readable by the tool through _request.avatar().data.
        class Idp(McpOpenApiApplication):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.calls = 0

            @route()
            def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
                self.calls += 1
                return {"identity": "idp-user", "tags": ["admin"], "data": {"tenant": "t1"}}

        class Tools(McpOpenApiApplication):
            @route(channel_channels="mcp")
            def whoami(self, _request=None) -> dict[str, Any]:
                avatar = _request.avatar()
                return {"identity": avatar.identity, "data": dict(avatar.data)}

        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""}), (Idp, {"code": "idp"}), (Tools, {"code": "api"})],
            channels={"mcp": {"authentication_route": "/idp/check"}},
            plugins={"openapi": True},
        )
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "whoami", "arguments": {}},
        }
        status, _, data = await drive(
            server, "/api/mcp", method="POST", body=json.dumps(envelope).encode(),
            headers=bearer("tok"),
        )
        assert status == 200
        assert data["result"]["structuredContent"] == {
            "identity": "idp-user", "data": {"tenant": "t1"},
        }
        assert server.applications["idp"].calls == 1


class TestTheBus:
    async def test_a_frame_with_auth_is_trusted_as_it_is(self) -> None:
        # wf:contract: a scope already carrying an avatar (info.auth on the bus) is not
        # wf:contract: authenticated again: the ruled route answers with that identity.
        server = api_server()
        answer = await server.kbus_call("/api/secret", {}, auth=Avatar("bus", ["admin"]))
        assert answer == {"identity": "bus"}


class TestWsx:
    async def test_the_handshake_authenticates_on_the_wsx_channel(self) -> None:
        # wf:contract: the websocket handshake awaits server.authenticate with
        # wf:contract: scope["kajenn.channel"] == "wsx"; an invalid Authorization header
        # wf:contract: closes 1008, a valid one gives every message the avatar.
        from kajenn.wsx import WsxConnection, WsxEnvelope

        seen: list[str] = []

        class Watching(AsgiServer):
            async def authenticate(self, scope: Scope) -> Avatar | None:
                if scope["type"] == "websocket":
                    seen.append(scope["kajenn.channel"])
                return await super().authenticate(scope)

        async def handshake(token: str) -> tuple[WsxConnection, list[Message]]:
            server = Watching(
                applications=[ServerApplication, (BaseApplication, {"mount": ""}), (Api, {"code": "api"})],
                auth=BEARER,
            )
            incoming: list[Message] = [
                {"type": "websocket.connect"},
                {"type": "websocket.disconnect", "code": 1000},
            ]
            sent: list[Message] = []

            async def receive() -> Message:
                return incoming.pop(0)

            async def send(message: Message) -> None:
                sent.append(message)

            scope: Scope = {
                "type": "websocket",
                "path": "/api/_wsx",
                "headers": [(b"host", b"example.org"), *bearer(token)],
                "query_string": b"",
                "subprotocols": [],
            }
            connection = WsxConnection(server, scope, receive, send)
            await connection.serve()
            return connection, sent

        _, sent = await handshake("nope")
        assert {"type": "websocket.close", "code": 1008} in [
            {"type": m["type"], "code": m.get("code")} for m in sent
        ]
        connection, _ = await handshake("sk_live_xyz")
        assert seen == ["wsx", "wsx"]
        message_scope = connection._request_scope(WsxEnvelope(WsxEnvelope(method="WSK", path="/api/secret").encode()))
        assert message_scope["auth"].identity == "svc"


class TestRunOnLoop:
    async def test_a_pool_thread_runs_a_coroutine_on_the_loop(self) -> None:
        # wf:contract: server.run_on_loop(coro) runs the coroutine on the server loop
        # wf:contract: from a pool thread and returns its result.
        status, _, data = await drive(api_server(), "/api/on_loop")
        assert (status, data) == (200, {"value": 42})

    async def test_run_on_loop_refuses_the_loop_thread(self) -> None:
        # wf:contract: called on the loop thread itself, run_on_loop raises RuntimeError
        # wf:contract: (it would deadlock); the coroutine is closed, not leaked.
        server = api_server()

        async def answer() -> int:
            return 1

        coro = answer()
        with pytest.raises(RuntimeError):
            server.run_on_loop(coro)
        coro.close()
