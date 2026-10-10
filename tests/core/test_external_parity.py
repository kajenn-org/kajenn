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

"""An application receives a request identically in-process and in an external process.

Every test sends the same request to ``billing`` declared in-process and declared
with ``spawner="subprocess"`` (the bench of ``tests/core/test_external_application.py``)
and compares what the handler saw and what the caller got. Driven end to end through
the public surface only: HTTP, WSX, ``server.kbus_call``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from kajenn import AsgiServer, Avatar
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.kbus import KBusCallError
from kajenn.wsx import WsxEnvelope

from .test_external_application import running

RECIPE = '''
import hashlib
import os
from typing import Any

from genro_routes import route

from kajenn import McpOpenApiApplication, RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from kajenn.streaming import StreamingResponse


def seen_by(request):
    scope = request.scope
    avatar = request.avatar()
    return {
        "method": scope["method"],
        "path": scope["path"],
        "query_string": scope["query_string"].decode("latin-1"),
        "headers": [[n.decode("latin-1"), v.decode("latin-1")] for n, v in scope["headers"]],
        "channel": scope.get("kajenn.channel"),
        "identity": avatar.identity if avatar is not None else None,
        "tags": sorted(avatar.tags) if avatar is not None else None,
        "kbus": "kajenn.kbus" in scope,
        "page_id": scope.get("genro.page_id"),
        "reply_path": scope.get("genro.reply_path"),
    }


class Billing(McpOpenApiApplication):
    @route()
    def prepared_state(self):
        return {"pid": os.getpid()}

    @route(channel_channels="mcp,rest,wsx,telegram")
    def seen(self, _request=None, **kwargs: Any):
        return seen_by(_request)

    @route(channel_channels="mcp,rest,wsx", auth_rule="admin")
    def secret(self, _request=None):
        return seen_by(_request)

    @route()
    def body(self, body_raw=None):
        data = body_raw or b""
        return {"size": len(data), "sha": hashlib.sha256(data).hexdigest()}

    @route()
    def answer(self, code, _request=None):
        response = _request.response
        code = int(code)
        response.status_code = code
        response.set_cookie("first", "1")
        response.set_cookie("second", "2")
        if code == 302:
            response.set_header("location", "/elsewhere")
        if code == 204:
            return None
        return bytes(range(256))

    @route()
    def fail(self):
        raise RuntimeError("billing failed")

    @route(channel_channels="rest,wsx")
    def lost(self):
        raise LookupError("billing lost")

    @route()
    def ticks(self):
        async def chunks():
            for n in range(3):
                yield f"tick-{n}\\n".encode()
        return StreamingResponse(chunks(), media_type="text/plain")


class Idp(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.channels_seen = []

    @route()
    def check(self, credential: str = "", channel: str = ""):
        self.channels_seen.append(channel)
        return {"identity": "idp-user", "tags": ["admin"]}


class Local(RoutedApplication):
    @route()
    def ping(self):
        return {"pong": True}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def main(self, root):
        cfg = root.configuration()
        self.site_section(cfg)
        self.server_section(cfg)
        self.storage_section(cfg)
        self.applications_section(cfg)
        cfg.plugins(openapi=True)
        credentials = cfg.authentication().credentials()
        credentials.bearer_token(identity="svc", token="sk_live_xyz", tags="admin")
        CHANNELS

    def applications_section(self, cfg):
        apps = cfg.applications()
        billing = apps.application(code="billing", app_class=Billing, DECLARATION)
        BODY
        apps.application(code="idp", app_class=Idp)
        apps.application(code="local", app_class=Local)
'''

PLACEMENTS = {"inprocess": 'mount="billing"', "external": 'spawner="subprocess", request_timeout=5'}
MCP_CHANNEL = 'cfg.channels().channel(name="mcp", authentication_route="/idp/check")'
RAW_BODY = 'billing.request(body="raw")'

Scenario = Callable[[AsgiServer], Awaitable[Any]]


async def in_both(tmp_path, scenario: Scenario, *, channels: str = "pass",
                  body: str = "pass") -> tuple[Any, Any]:
    """What ``scenario`` returns against billing in-process, then spawned."""
    results = []
    for name, declaration in PLACEMENTS.items():
        folder = tmp_path / name
        folder.mkdir()
        path = folder / "config.py"
        path.write_text(RECIPE.replace("DECLARATION", declaration)
                        .replace("CHANNELS", channels).replace("BODY", body))
        async with running(str(path)) as server:
            results.append(await scenario(server))
    return results[0], results[1]


async def call(server: AsgiServer, path: str, *, method: str = "GET", query: bytes = b"",
               headers: list[tuple[bytes, bytes]] | None = None,
               body: bytes = b"") -> tuple[int, list[list[str]], bytes]:
    """The status, the headers and the body; a fresh session's id is masked."""
    result = await BufferedAsgiEndpoint(server).serve({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": query, "headers": headers or [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }, body)
    headers = [[name, re.sub(r"^session_id=[^;]*", "session_id=*", value)]
               for name, value in result["headers"]]
    return result["status"], headers, result["body"]


def bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


async def wsx(server: AsgiServer, envelope: WsxEnvelope,
              headers: list[tuple[bytes, bytes]]) -> WsxEnvelope:
    """Send one WSX message on a socket opened on ``local`` and return its answer."""
    inbox: asyncio.Queue = asyncio.Queue()
    sent: list[dict[str, Any]] = []
    answered = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "websocket.send":
            answered.set()

    await inbox.put({"type": "websocket.connect"})
    await inbox.put({"type": "websocket.receive", "text": envelope.encode()})
    socket = asyncio.create_task(server({
        "type": "websocket", "asgi": {"version": "3.0"}, "path": "/local/_wsx",
        "raw_path": b"/local/_wsx", "root_path": "", "query_string": b"",
        "headers": [(b"host", b"127.0.0.1"), *headers], "subprotocols": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }, inbox.get, send))
    await asyncio.wait_for(answered.wait(), 10)
    await inbox.put({"type": "websocket.disconnect", "code": 1000})
    await socket
    return WsxEnvelope(next(m["text"] for m in sent if m["type"] == "websocket.send"))


class TestTheRequestArrivesUnchanged:
    async def test_method_path_query_and_headers(self, tmp_path) -> None:
        # wf:contract: GET, POST, PUT, DELETE with a query string carrying repeated keys and
        # wf:contract: percent-encoded characters, and headers repeated or in mixed case: the
        # wf:contract: handler sees the same method, path, query string and headers in both placements.
        query = b"a=1&a=2&name=caf%C3%A9%20bar&sym=%26%3D"
        headers = [(b"X-Trace", b"one"), (b"x-trace", b"two"), (b"Accept-Language", b"it")]

        async def scenario(server: AsgiServer) -> list[Any]:
            seen = []
            for method in ("GET", "POST", "PUT", "DELETE"):
                status, _, body = await call(server, "/billing/seen", method=method,
                                             query=query, headers=headers)
                seen.append((status, json.loads(body)))
            return seen

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        assert [status for status, _ in external] == [200] * 4
        assert [seen["method"] for _, seen in external] == ["GET", "POST", "PUT", "DELETE"]
        assert {seen["query_string"] for _, seen in external} == {query.decode()}

    async def test_bodies_empty_binary_and_large(self, tmp_path) -> None:
        # wf:contract: an empty body, a body holding every byte value 0-255, and a body of 1 MiB
        # wf:contract: reach the handler byte-identical in both placements.
        bodies = [b"", bytes(range(256)), bytes(range(256)) * 4096]

        async def scenario(server: AsgiServer) -> list[Any]:
            seen = []
            for body in bodies:
                status, _, answer = await call(
                    server, "/billing/body", method="POST", body=body,
                    headers=[(b"content-type", b"application/octet-stream")])
                seen.append((status, json.loads(answer)))
            return seen

        inprocess, external = await in_both(tmp_path, scenario, body=RAW_BODY)
        assert inprocess == external == [
            (200, {"size": len(body), "sha": hashlib.sha256(body).hexdigest()})
            for body in bodies]

    async def test_a_plain_http_request_carries_no_kbus_flag(self, tmp_path) -> None:
        # wf:contract: a plain HTTP request sees no "kajenn.kbus" in either placement; a
        # wf:contract: server.kbus_call sees it in both.
        async def scenario(server: AsgiServer) -> tuple[bool, bool]:
            _, _, body = await call(server, "/billing/seen")
            called = await server.kbus_call("/billing/seen", {})
            return json.loads(body)["kbus"], called["kbus"]

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external == (False, True)


class TestTheChannelTravels:
    async def test_the_channel_of_a_kbus_call(self, tmp_path) -> None:
        # wf:contract: server.kbus_call(..., channel="mcp") and channel="telegram" reach the
        # wf:contract: handler with that channel in both placements; without a channel both see
        # wf:contract: the same default.
        async def scenario(server: AsgiServer) -> list[Any]:
            return [(await server.kbus_call("/billing/seen", {}, channel=channel))["channel"]
                    for channel in ("mcp", "telegram", None)]

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        assert external[:2] == ["mcp", "telegram"]

    async def test_a_wsx_message(self, tmp_path) -> None:
        # wf:contract: a WSX message carrying page_id and reply_path reaches the handler with
        # wf:contract: channel "wsx", the connection's identity, and the same genro.page_id and
        # wf:contract: genro.reply_path in both placements.
        envelope = WsxEnvelope(id="m1", method="WSK", path="/billing/seen", data={},
                               page_id="page-7", reply_path="/local/ping")

        async def scenario(server: AsgiServer) -> tuple[Any, Any]:
            reply = await wsx(server, envelope, bearer("sk_live_xyz"))
            return reply.status, reply.data

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        status, seen = external
        assert status == 200
        assert (seen["channel"], seen["identity"], seen["page_id"], seen["reply_path"]) == (
            "wsx", "svc", "page-7", "/local/ping")


class TestTheIdentityIsTheSame:
    async def test_a_valid_bearer(self, tmp_path) -> None:
        # wf:contract: a valid Bearer reaches a ruled route with the same identity and tags in
        # wf:contract: both placements.
        async def scenario(server: AsgiServer) -> tuple[int, Any, Any]:
            status, _, body = await call(server, "/billing/secret", headers=bearer("sk_live_xyz"))
            seen = json.loads(body)
            return status, seen["identity"], seen["tags"]

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external == (200, "svc", ["admin"])

    async def test_an_invalid_bearer(self, tmp_path) -> None:
        # wf:contract: an invalid Bearer answers the same 401 with the same WWW-Authenticate
        # wf:contract: header in both placements.
        async def scenario(server: AsgiServer) -> tuple[int, Any]:
            status, headers, _ = await call(server, "/billing/seen", headers=bearer("nope"))
            return status, dict(headers).get("www-authenticate")

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external == (401, "Bearer")

    async def test_no_credential(self, tmp_path) -> None:
        # wf:contract: without a credential a public route sees no avatar and a ruled route
        # wf:contract: answers the same status in both placements.
        async def scenario(server: AsgiServer) -> tuple[Any, int]:
            _, _, body = await call(server, "/billing/seen")
            ruled, _, _ = await call(server, "/billing/secret")
            return json.loads(body)["identity"], ruled

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        assert inprocess[0] is None

    async def test_the_session_identity(self, tmp_path) -> None:
        # wf:contract: a request carrying the server's session cookie (a session with an avatar)
        # wf:contract: reaches the handler with that identity in both placements.
        async def scenario(server: AsgiServer) -> tuple[int, Any, Any]:
            session = server.session_store.create(Avatar("sess", ["admin"]))
            cookie = [(b"cookie", f"session_id={session.id}".encode())]
            status, _, body = await call(server, "/billing/secret", headers=cookie)
            seen = json.loads(body)
            return status, seen["identity"], seen["tags"]

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external == (200, "sess", ["admin"])

    async def test_an_mcp_face_on_its_own_route(self, tmp_path) -> None:
        # wf:contract: with channels().channel(name="mcp", authentication_route=...) configured,
        # wf:contract: a tools/call with a Bearer to the application's MCP face is verified on that
        # wf:contract: route (it records channel "mcp") and answers the same in both placements.
        envelope = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "secret", "arguments": {}},
        }

        async def scenario(server: AsgiServer) -> tuple[int, Any, list[str]]:
            status, _, body = await call(
                server, "/billing/mcp", method="POST", body=json.dumps(envelope).encode(),
                headers=[*bearer("tok"), (b"content-type", b"application/json")])
            seen = json.loads(body)["result"]["structuredContent"]
            return status, (seen["identity"], seen["channel"]), server.applications["idp"].channels_seen

        inprocess, external = await in_both(tmp_path, scenario, channels=MCP_CHANNEL)
        assert inprocess == external == (200, ("idp-user", "mcp"), ["mcp"])


class TestTheAnswerComesBackUnchanged:
    async def test_status_headers_and_body(self, tmp_path) -> None:
        # wf:contract: answers 200, 201, 204, 302 with Location, 404 and a raised exception
        # wf:contract: (500), with repeated Set-Cookie headers and a binary body: the caller
        # wf:contract: gets the same status, headers and body in both placements.
        async def scenario(server: AsgiServer) -> list[Any]:
            answers = [await call(server, "/billing/answer", query=f"code={code}".encode())
                       for code in ("200", "201", "204", "302")]
            answers.append(await call(server, "/billing/nowhere"))
            answers.append(await call(server, "/billing/fail"))
            return answers

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        assert [status for status, _, _ in external] == [200, 201, 204, 302, 404, 500]
        status, headers, body = external[0]
        assert [value for name, value in headers if name == "set-cookie"][:2] == [
            "first=1; Path=/; SameSite=Lax", "second=2; Path=/; SameSite=Lax"]
        assert body == bytes(range(256))
        assert ["location", "/elsewhere"] in external[3][1]

    async def test_a_streamed_answer(self, tmp_path) -> None:
        # wf:contract: a route answering a stream of chunks delivers the same chunks in the same
        # wf:contract: order in both placements.
        async def scenario(server: AsgiServer) -> list[tuple[bytes, bool]]:
            sent: list[dict[str, Any]] = []
            requested = asyncio.Event()
            never = asyncio.Event()

            async def receive() -> dict[str, Any]:
                if not requested.is_set():
                    requested.set()
                    return {"type": "http.request", "body": b"", "more_body": False}
                await never.wait()
                return {"type": "http.disconnect"}

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            await asyncio.wait_for(server({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": "/billing/ticks",
                "raw_path": b"/billing/ticks", "root_path": "", "query_string": b"",
                "headers": [], "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
            }, receive, send), 30)
            return [(m.get("body", b""), m.get("more_body", False))
                    for m in sent if m["type"] == "http.response.body"]

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        assert b"".join(chunk for chunk, _ in external) == b"tick-0\ntick-1\ntick-2\n"


class TestFailuresComeBackUnchanged:
    async def test_a_kbus_call_to_a_raising_route(self, tmp_path) -> None:
        # wf:contract: server.kbus_call to a route raising LookupError answers the same 500 with
        # wf:contract: the same "LookupError: <message>" text in both placements.
        async def scenario(server: AsgiServer) -> tuple[Any, Any]:
            with pytest.raises(KBusCallError) as raised:
                await server.kbus_call("/billing/lost", {})
            return raised.value.status, raised.value.error

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external == (500, "LookupError: billing lost")

    async def test_a_wsx_message_to_a_raising_route(self, tmp_path) -> None:
        # wf:contract: a WSX message to a route raising LookupError answers the page the same
        # wf:contract: status and data in both placements.
        envelope = WsxEnvelope(id="m2", method="WSK", path="/billing/lost", data={})

        async def scenario(server: AsgiServer) -> tuple[Any, Any]:
            reply = await wsx(server, envelope, bearer("sk_live_xyz"))
            return reply.status, reply.data

        inprocess, external = await in_both(tmp_path, scenario)
        assert inprocess == external
        assert inprocess[0] == 500
