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

"""The quality-check corrections of the 0.4.0 execution point, pinned.

A presented credential is verified before the session is read; a verification
that ends after a revocation is not cached; the authentication route's
failures map to 401 with the challenge or to 503; the two ``_server`` auth
routes serve the bus only; MCP gates run before authentication; a handler
raising ``HTTPNotFound`` is not reported as a missing tool; the MCP binding
rule follows ``mcp_channel``; ``run_on_loop`` refuses to run before the loop
is known; a wire value named ``_request`` never replaces the injected one.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

from genro_routes import RoutingClass, route

from kajenn import AsgiServer, Avatar, BaseApplication, McpOpenApiApplication, RoutedApplication
from kajenn.exceptions import HTTPException, HTTPForbidden, HTTPNotFound, HTTPUnauthorized
from kajenn.mcp.engine import McpEngine
from kajenn.mcp.jsonrpc import McpError
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

    @route(channel_channels="mcp,rest")
    def missing(self) -> dict[str, Any]:
        raise HTTPNotFound("record 42 not found")

    @route(openapi_method="post")
    def echo_request(self, _request=None) -> dict[str, Any]:
        return {"is_request": type(_request).__name__ == "Request"}


class SlowIdp(RoutedApplication):
    """An authentication route that waits until the test lets it answer."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gate = asyncio.Event()
        self.calls = 0

    @route()
    async def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
        self.calls += 1
        await self.gate.wait()
        return {"identity": "ann", "tags": ["dev"], "data": {}}


def api_server(**extra: Any) -> AsgiServer:
    apps = extra.pop("applications", [ServerApplication, (BaseApplication, {"mount": ""})])
    return AsgiServer(
        applications=[*apps, (Api, {"code": "api"})], auth=BEARER, plugins={"openapi": True}, **extra
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


def session_cookie(server: AsgiServer, avatar: Avatar) -> list[tuple[bytes, bytes]]:
    session = server.session_store.create(avatar)
    return [(b"cookie", f"session_id={session.id}".encode())]


async def tools_call(
    server: AsgiServer, name: str, headers: list[tuple[bytes, bytes]] | None = None
) -> tuple[int, dict[bytes, bytes], Any]:
    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    return await drive(server, "/api/mcp", method="POST", body=json.dumps(envelope).encode(), headers=headers)


class TestTheHeaderComesFirst:
    async def test_an_invalid_header_is_401_even_with_a_session(self) -> None:
        server = api_server()
        headers = [*session_cookie(server, Avatar("sess", ["admin"])), *bearer("nope")]
        status, response_headers, _ = await drive(server, "/api/open", headers=headers)
        assert status == 401
        assert response_headers.get(b"www-authenticate") == b"Bearer"

    async def test_a_valid_header_wins_over_a_weaker_session(self) -> None:
        server = api_server()
        headers = [*session_cookie(server, Avatar("sess", [])), *bearer("sk_live_xyz")]
        status, _, data = await drive(server, "/api/secret", headers=headers)
        assert (status, data) == (200, {"identity": "svc"})

    async def test_a_scope_without_channel_is_read_as_rest(self) -> None:
        server = api_server()
        scope: Scope = {"type": "http", "headers": bearer("sk_live_xyz")}
        avatar = await server.authenticate(scope)
        assert avatar.identity == "svc"


class TestTheCacheAndTheRoute:
    async def test_a_verification_ending_after_a_revocation_is_not_cached(self) -> None:
        server = api_server(
            applications=[ServerApplication, (BaseApplication, {"mount": ""}), (SlowIdp, {"code": "idp"})],
            channels={"mcp": {"authentication_route": "/idp/check"}},
        )
        idp = server.applications["idp"]
        first = asyncio.create_task(server.authenticate_credential("Bearer good", "mcp"))
        await asyncio.sleep(0)
        server.forget_all_credentials()
        idp.gate.set()
        assert (await first).identity == "ann"
        await server.authenticate_credential("Bearer good", "mcp")
        assert idp.calls == 2

    async def test_forget_credential_tolerates_a_concurrent_write(self) -> None:
        server = api_server()
        await server.authenticate_credential("Bearer sk_live_xyz", "rest")
        cache = server._credential_cache
        stop = threading.Event()

        def writer() -> None:
            n = 0
            while not stop.is_set():
                cache[(f"other{n}", "rest")] = (float("inf"), Avatar("x"))
                n += 1

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(200):
                server.forget_credential("Bearer sk_live_xyz")
        finally:
            stop.set()
            thread.join()

    async def test_no_route_is_401_with_the_challenge(self) -> None:
        server = AsgiServer(applications=[(BaseApplication, {"code": "site"})], auth=BEARER)
        with pytest.raises(HTTPUnauthorized) as refused:
            await server.authenticate_credential("Bearer sk_live_xyz", "rest")
        assert (b"www-authenticate", b"Bearer") in refused.value.headers

    async def test_a_failing_route_is_503(self) -> None:
        class Broken(RoutedApplication):
            @route()
            def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
                raise RuntimeError("db down")

        server = api_server(
            applications=[ServerApplication, (BaseApplication, {"mount": ""}), (Broken, {"code": "idp"})],
            channels={"mcp": {"authentication_route": "/idp/check"}},
        )
        with pytest.raises(HTTPException) as failed:
            await server.authenticate_credential("Bearer x", "mcp")
        assert failed.value.status == 503

    async def test_the_server_auth_routes_serve_the_bus_only(self) -> None:
        server = api_server()
        body = json.dumps({"credential": "Bearer sk_live_xyz", "channel": "rest"}).encode()
        status, _, _ = await drive(server, "/_server/auth/authenticate", method="POST", body=body)
        assert status == 403
        status, _, _ = await drive(
            server, "/_server/auth/forget_credential", method="POST", body=json.dumps({"credential": "x"}).encode()
        )
        assert status == 403
        answer = await server.kbus_call(
            "/_server/auth/authenticate", {"credential": "Bearer sk_live_xyz", "channel": "rest"}
        )
        assert answer["identity"] == "svc"


class TestMcp:
    async def test_the_origin_gate_runs_before_authentication(self) -> None:
        server = AsgiServer(
            applications=[
                ServerApplication,
                (BaseApplication, {"mount": ""}),
                (Api, {"code": "api", "allowed_origins": ["https://ok.example"]}),
            ],
            auth=BEARER,
            plugins={"openapi": True},
        )
        headers = [(b"origin", b"https://evil.example"), *bearer("nope")]
        status, _, _ = await tools_call(server, "open", headers=headers)
        assert status == 403

    async def test_a_handler_raising_not_found_is_not_a_missing_tool(self) -> None:
        server = api_server()
        _, _, data = await tools_call(server, "missing")
        assert data["error"]["code"] == -32603
        assert "record 42" in data["error"]["message"]
        _, _, data = await tools_call(server, "nothing")
        assert data["error"]["code"] == -32601

    async def test_the_binding_rule_follows_mcp_channel(self) -> None:
        class Tools(McpOpenApiApplication):
            mcp_channel = "tools"

            @route(channel_channels="tools,rest", openapi_method="post")
            def save(self, name: str = "", body_data: dict | None = None) -> dict[str, Any]:
                return {"name": name, "body_data": body_data}

        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""}), (Tools, {"code": "t"})], plugins={"openapi": True}
        )
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "save", "arguments": {"name": "cpu50", "body_data": {"cpu": 50}}},
        }
        _, _, data = await drive(server, "/t/mcp", method="POST", body=json.dumps(envelope).encode())
        assert data["result"]["structuredContent"] == {"name": "cpu50", "body_data": {"cpu": 50}}


class TestRunOnLoop:
    def test_before_the_loop_is_known_it_raises(self) -> None:
        server = api_server()

        async def answer() -> int:
            return 1

        outcome: list[Any] = []

        def call() -> None:
            try:
                server.run_on_loop(answer())
            except RuntimeError as error:
                outcome.append(error)

        thread = threading.Thread(target=call)
        thread.start()
        thread.join()
        assert outcome and "loop" in str(outcome[0])


class TestTheRequestSeam:
    async def test_a_wire_value_named_request_never_replaces_the_injected_one(self) -> None:
        server = api_server()
        body = json.dumps({"_request": "forged"}).encode()
        status, _, data = await drive(server, "/api/echo_request", method="POST", body=body)
        assert (status, data) == (200, {"is_request": True})


class TestCoverageOfTheCorrections:
    async def test_engine_level_mapping_of_handler_exceptions(self) -> None:
        class Tools(RoutingClass):
            @route()
            def missing(self) -> None:
                raise HTTPNotFound("record 42 not found")

            @route()
            def mine(self) -> None:
                raise HTTPForbidden("mine")

        engine = McpEngine(Tools().route, channel="mcp")

        async def call(name: str) -> McpError:
            with pytest.raises(McpError) as refused:
                await engine.dispatch(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name}}
                )
            return refused.value

        assert (await call("missing")).code == -32603
        assert "record 42" in (await call("missing")).message
        assert (await call("mine")).code == -32000
        assert (await call("ghost")).code == -32601

    async def test_an_unreachable_route_is_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = api_server()

        async def lost(*args: Any, **kwargs: Any) -> Any:
            raise TimeoutError("no reply")

        monkeypatch.setattr(server, "kbus_call", lost)
        with pytest.raises(HTTPException) as failed:
            await server.authenticate_credential("Bearer x", "rest")
        assert failed.value.status == 503

    async def test_another_refusal_status_of_the_route_propagates(self) -> None:
        class Picky(RoutedApplication):
            @route()
            def check(self, credential: str = "", channel: str = "") -> dict[str, Any]:
                raise HTTPForbidden("not from here")

        server = api_server(
            applications=[ServerApplication, (BaseApplication, {"mount": ""}), (Picky, {"code": "idp"})],
            channels={"mcp": {"authentication_route": "/idp/check"}},
        )
        with pytest.raises(HTTPException) as refused:
            await server.authenticate_credential("Bearer x", "mcp")
        assert refused.value.status == 403
