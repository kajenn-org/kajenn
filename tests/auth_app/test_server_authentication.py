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

"""Contract tests: the server asks the application mounted at ``/_auth``.

Every test drives a real ``AsgiServer`` at the ASGI level. ``AuthApplicationBase``
is mounted over a ``MemoryAuthStore`` and users, groups and tokens are written
through its public ``store``. The tests read what a caller sees: HTTP status
and JSON answers, MCP answers, WSX answers, the session store.

- The same avatar, with its group, reaches a handler from REST, MCP, WSX, the
  bus and a session.
- A server without ``_auth`` refuses every presented credential with 401, a
  ruled route answers 401, a session gives no avatar.
- A user whose status stops authenticating loses token and session within the
  cache time; emptying the cache makes it immediate; the session is deleted.
- The session keeps identity and group; the tags come from ``_auth``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from genro_routes import route

from kajenn import AsgiConfigBuilder, AsgiServer, AuthApplicationBase, Avatar, BaseApplication
from kajenn import RoutedApplication
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.wsx import WsxEnvelope
from tests.conftest import start_server
from tests.storage_support import add_user, auth_application, issue_token

ALICE = {"identity": "alice", "group": "staff", "tags": ["admin", "reports"]}


class Tools(RoutedApplication):
    """Handlers that answer the avatar they see."""

    @route(channel_channels="mcp,api,wsx")
    def whoami(self, _request=None) -> dict[str, Any] | None:
        avatar = _request.avatar()
        if avatar is None:
            return None
        return {"identity": avatar.identity, "group": avatar.group, "tags": sorted(avatar.tags)}

    @route(channel_channels="mcp,api,wsx", auth_rule="admin")
    def secret(self) -> dict[str, bool]:
        return {"secret": True}

    @route()
    async def relay(self, _request=None) -> Any:
        return await self.server.kbus_call("/tools/whoami", {}, auth=_request.avatar())

    @route(openapi_method="post")
    def note(self, value: str = "", _request=None) -> dict[str, Any]:
        """Answer the ``note`` found on the avatar's data, then write ``value`` there."""
        data = _request.avatar().data
        found = data["note"]
        data["note"] = value
        return {"found": found}


def make_server(*, with_auth: bool = True, **auth_params: Any) -> AsgiServer:
    """Tools at ``/tools``, the site root, and ``_auth`` unless ``with_auth`` is False."""
    applications: list[Any] = [(BaseApplication, {"mount": ""}), (Tools, {"code": "tools"})]
    if with_auth:
        applications.insert(0, auth_application(**auth_params))
    return AsgiServer(applications=applications, plugins={"openapi": True})


async def seeded(**auth_params: Any) -> AsgiServer:
    """A started server whose ``_auth`` has group ``staff`` (tag admin) and alice in it."""
    server = make_server(**auth_params)
    await start_server(server)
    server.applications["_auth"].store.save_group({"code": "staff", "tags": ["admin"]})
    add_user(
        server, "alice", ["reports"], password="wonder", groups=["staff"], primary_group="staff"
    )
    return server


async def call(
    server: AsgiServer,
    path: str,
    *,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """Status, decoded JSON body and headers of one request."""
    raw = json.dumps(body).encode() if body is not None else b""
    all_headers = list(headers or [])
    if body is not None:
        all_headers.append((b"content-type", b"application/json"))
    await start_server(server)
    result = await BufferedAsgiEndpoint(server).serve(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": all_headers,
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 80),
        },
        raw,
    )
    answer_headers = dict(result["headers"])
    is_json = answer_headers.get("content-type", "").startswith("application/json")
    payload = json.loads(result["body"]) if result["body"] and is_json else None
    return result["status"], payload, answer_headers


def bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


def basic(identity: str, password: str) -> list[tuple[bytes, bytes]]:
    value = base64.b64encode(f"{identity}:{password}".encode()).decode()
    return [(b"authorization", f"Basic {value}".encode())]


def cookie(session_id: str) -> list[tuple[bytes, bytes]]:
    return [(b"cookie", f"session_id={session_id}".encode())]


async def login(server: AsgiServer) -> str:
    """Log alice in through ``/_auth/login``; return the session id."""
    status, answer, _ = await call(
        server, "/_auth/login", method="POST", body={"identity": "alice", "password": "wonder"}
    )
    assert status == 200 and "session_id" in answer, answer
    session_id: str = answer["session_id"]
    return session_id


async def tools_call(server: AsgiServer, name: str, headers: list[tuple[bytes, bytes]]) -> Any:
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }
    _, answer, _ = await call(
        server, "/tools/_meta/mcp", method="POST", headers=headers, body=envelope
    )
    return answer


async def wsx(server: AsgiServer, path: str, headers: list[tuple[bytes, bytes]]) -> WsxEnvelope:
    """Send one WSX message to ``path`` on a socket opened on ``tools``; return its answer."""
    inbox: asyncio.Queue = asyncio.Queue()
    sent: list[dict[str, Any]] = []
    answered = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] in ("websocket.send", "websocket.close"):
            answered.set()

    await start_server(server)
    await inbox.put({"type": "websocket.connect"})
    await inbox.put(
        {
            "type": "websocket.receive",
            "text": WsxEnvelope(id="m1", method="WSK", path=path, data={}).encode(),
        }
    )
    socket = asyncio.create_task(
        server(
            {
                "type": "websocket",
                "asgi": {"version": "3.0"},
                "path": "/tools/_wsx",
                "raw_path": b"/tools/_wsx",
                "root_path": "",
                "query_string": b"",
                "headers": [(b"host", b"127.0.0.1"), *headers],
                "subprotocols": [],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 80),
            },
            inbox.get,
            send,
        )
    )
    await asyncio.wait_for(answered.wait(), 10)
    await inbox.put({"type": "websocket.disconnect", "code": 1000})
    await socket
    return WsxEnvelope(next(m["text"] for m in sent if m["type"] == "websocket.send"))


class TestTheSameAvatarOnEveryFace:
    async def test_rest_mcp_wsx_bus_and_session_see_the_same_avatar(self) -> None:
        # wf:contract: a token of alice (group staff, tag admin from the group, reports her own)
        # wf:contract: gives {identity, group, tags} == ALICE to the handler through REST,
        # wf:contract: an MCP tools/call, a WSX message, a bus call carrying the avatar, and a
        # wf:contract: session opened by /_auth/login.
        server = await seeded()
        token = issue_token(server, "alice")
        status, rest, _ = await call(server, "/tools/whoami", headers=bearer(token))
        assert (status, rest) == (200, ALICE)
        mcp = await tools_call(server, "whoami", bearer(token))
        assert mcp["result"]["structuredContent"] == ALICE
        answer = await wsx(server, "/tools/whoami", bearer(token))
        assert answer.data == ALICE
        status, relayed, _ = await call(server, "/tools/relay", headers=bearer(token))
        assert (status, relayed) == (200, ALICE)
        session_id = await login(server)
        status, by_session, _ = await call(server, "/tools/whoami", headers=cookie(session_id))
        assert (status, by_session) == (200, ALICE)

    async def test_a_basic_header_is_verified_by_auth(self) -> None:
        # wf:contract: Authorization Basic alice:wonder answers ALICE; a wrong password 401.
        server = await seeded()
        status, answer, _ = await call(server, "/tools/whoami", headers=basic("alice", "wonder"))
        assert (status, answer) == (200, ALICE)
        status, _, _ = await call(server, "/tools/whoami", headers=basic("alice", "wrong"))
        assert status == 401

    async def test_the_cache_ttl_is_read_from_the_auth_application(self) -> None:
        # wf:contract: application(code="_auth", app_class=AuthApplicationBase, cache_ttl=7)
        # wf:contract: in a recipe gives server.credential_cache_ttl == 7; without the
        # wf:contract: attribute the default is 300.

        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.applications().application(
                    code="_auth", app_class=AuthApplicationBase, cache_ttl=7
                )

        assert AsgiServer(config=Recipe).credential_cache_ttl == 7
        assert make_server().credential_cache_ttl == 300

    async def test_a_dedicated_process_reads_the_cache_ttl_of_its_recipe(self) -> None:
        # wf:contract: an application with spawner="subprocess" runs in a process that does
        # wf:contract: not mount _auth; there credential_cache_ttl is the cache_ttl written
        # wf:contract: on the _auth element of the same recipe, and 300 when it is absent.

        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                apps.application(code="_auth", app_class=AuthApplicationBase, cache_ttl=0)
                apps.application(code="tools", app_class=Tools, spawner="subprocess")

        class RecipeWithoutTtl(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                apps.application(code="_auth", app_class=AuthApplicationBase)
                apps.application(code="tools", app_class=Tools, spawner="subprocess")

        dedicated = AsgiServer(config=Recipe, role="application:tools")
        assert "_auth" not in dedicated.applications
        assert dedicated.credential_cache_ttl == 0
        assert (
            AsgiServer(config=RecipeWithoutTtl, role="application:tools").credential_cache_ttl
            == 300
        )


class TestAServerWithoutAuth:
    async def test_a_ruled_route_answers_401(self) -> None:
        # wf:contract: without _auth, GET /tools/secret with no credential answers 401.
        server = make_server(with_auth=False)
        status, _, _ = await call(server, "/tools/secret")
        assert status == 401

    async def test_any_presented_credential_answers_401(self) -> None:
        # wf:contract: without _auth, a Bearer, a gak_ token and a Basic header on the public
        # wf:contract: /tools/whoami all answer 401 with the WWW-Authenticate challenge.
        server = make_server(with_auth=False)
        for headers in (bearer("anything"), bearer("gak_1_2"), basic("alice", "wonder")):
            status, _, answer_headers = await call(server, "/tools/whoami", headers=headers)
            assert status == 401
            assert answer_headers["www-authenticate"] == "Bearer"

    async def test_no_credential_stays_anonymous(self) -> None:
        # wf:contract: without _auth, the public /tools/whoami answers null (no avatar).
        server = make_server(with_auth=False)
        status, answer, _ = await call(server, "/tools/whoami")
        assert (status, answer) == (200, None)

    async def test_a_session_gives_no_avatar(self) -> None:
        # wf:contract: without _auth, a session holding a root avatar gives the handler none.
        server = make_server(with_auth=False)
        session = server.session_store.create(Avatar("alice", ["admin"], group="staff"))
        status, answer, _ = await call(server, "/tools/whoami", headers=cookie(session.id))
        assert (status, answer) == (200, None)


class TestAStatusThatStopsAuthenticating:
    async def test_token_and_session_last_until_the_cache_is_emptied(self) -> None:
        # wf:contract: alice banned in the store keeps her cached token and session (200 on
        # wf:contract: /tools/secret) within cache_ttl; after server.forget_all_credentials()
        # wf:contract: the token answers 401, the session request answers 401 and the
        # wf:contract: session is gone from the store.
        server = await seeded()
        token = issue_token(server, "alice")
        session_id = await login(server)
        assert (await call(server, "/tools/secret", headers=bearer(token)))[0] == 200
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 200
        add_user(
            server, "alice", ["reports"], status="banned", groups=["staff"], primary_group="staff"
        )
        assert (await call(server, "/tools/secret", headers=bearer(token)))[0] == 200
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 200
        server.forget_all_credentials()
        assert (await call(server, "/tools/secret", headers=bearer(token)))[0] == 401
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 401
        assert server.session_store.get(session_id) is None

    async def test_without_cache_the_refusal_is_immediate(self) -> None:
        # wf:contract: with cache_ttl=0 a banned alice is refused at the next request, by
        # wf:contract: token and by session.
        server = await seeded(cache_ttl=0)
        token = issue_token(server, "alice")
        session_id = await login(server)
        assert (await call(server, "/tools/secret", headers=bearer(token)))[0] == 200
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 200
        add_user(
            server, "alice", ["reports"], status="banned", groups=["staff"], primary_group="staff"
        )
        assert (await call(server, "/tools/secret", headers=bearer(token)))[0] == 401
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 401
        assert server.session_store.get(session_id) is None

    async def test_a_tag_taken_from_the_group_leaves_the_live_session(self) -> None:
        # wf:contract: after login, the group staff loses its admin tag: once the cache is
        # wf:contract: emptied the same session answers 403 on /tools/secret and whoami shows
        # wf:contract: the tags without admin; the session is still there.
        server = await seeded()
        session_id = await login(server)
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 200
        server.applications["_auth"].store.save_group({"code": "staff", "tags": []})
        server.forget_all_credentials()
        assert (await call(server, "/tools/secret", headers=cookie(session_id)))[0] == 403
        _, answer, _ = await call(server, "/tools/whoami", headers=cookie(session_id))
        assert answer == {"identity": "alice", "group": "staff", "tags": ["reports"]}
        assert server.session_store.get(session_id) is not None


class TestAvatarDataLivesForTheRequest:
    async def test_data_written_by_a_handler_is_gone_at_the_next_request(self) -> None:
        # wf:contract: a handler writes avatar.data["note"] on a session request; the next
        # wf:contract: request of the same session finds no note.
        server = await seeded()
        session = cookie(await login(server))
        body = {"value": "kept?"}
        _, first, _ = await call(server, "/tools/note", method="POST", headers=session, body=body)
        _, second, _ = await call(server, "/tools/note", method="POST", headers=session, body=body)
        assert (first, second) == ({"found": None}, {"found": None})

    async def test_data_written_in_one_session_is_not_read_in_another(self) -> None:
        # wf:contract: two sessions of alice; a handler writes avatar.data["note"] in one;
        # wf:contract: a request of the other finds no note.
        server = await seeded()
        one, other = cookie(await login(server)), cookie(await login(server))
        body = {"value": "private"}
        await call(server, "/tools/note", method="POST", headers=one, body=body)
        _, answer, _ = await call(server, "/tools/note", method="POST", headers=other, body=body)
        assert answer == {"found": None}


class TestTheSessionKeepsIdentityAndGroup:
    async def test_dump_holds_identity_and_group_and_restore_asks_auth_for_tags(self) -> None:
        # wf:contract: session_store.dump() of alice's session holds {identity, group} and no
        # wf:contract: tags; a fresh server that restores it gives the handler ALICE, the
        # wf:contract: tags read from its _auth.
        server = await seeded()
        session_id = await login(server)
        dumped = server.session_store.dump()
        assert dumped[session_id]["avatars"]["root"] == {"identity": "alice", "group": "staff"}
        restored = await seeded()
        restored.session_store.restore(dumped)
        status, answer, _ = await call(restored, "/tools/whoami", headers=cookie(session_id))
        assert (status, answer) == (200, ALICE)
        assert sorted(restored.session_store.get(session_id).avatar().tags) == ALICE["tags"]
