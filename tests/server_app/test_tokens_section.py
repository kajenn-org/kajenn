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

"""TokensSection tests (Macro 5b Phase 6).

Requests drive a REAL ``AsgiServer`` at the ASGI level: the ``_server`` app is
auto-mounted, so the section lives at ``/_server/tokens/...``. A test middleware
(order 500) stamps a SUPERADMIN identity so the ruled routes are reachable; the
api-key store is wired with ``tokens=`` and a symmetric JWT verifier via
``auth=``. Every route is ``auth_rule="SUPERADMIN"``: an anonymous request
answers 401 and a wrong-tag one 403, and a server with no store answers
``{"error": ...}``.
"""

from __future__ import annotations

import json
from typing import Any

from kajenn import ApiKeyStore, AsgiServer, Avatar, BaseApplication
from kajenn_server_app import ServerApplication
from kajenn.middleware.base import BaseMiddleware
from kajenn.types import Message, Scope

JWT_SECRET = "sign-me-please"


class MemoryApiKeyStore(ApiKeyStore):
    """In-memory ``ApiKeyStore`` backend: the shared issue/verify logic over a dict."""

    __slots__ = ("_records",)

    def __init__(self, storage: object = None) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def load_all(self) -> list[dict[str, Any]]:
        return list(self._records.values())

    def get(self, key_id: str) -> dict[str, Any] | None:
        return self._records.get(key_id)

    def save(self, record: dict[str, Any]) -> None:
        self._records[record["key_id"]] = record

    def delete(self, key_id: str) -> bool:
        return self._records.pop(key_id, None) is not None


class StampAuthMiddleware(BaseMiddleware):
    """Test middleware (order 500): stamps a fixed identity on ``scope["auth"]``."""

    middleware_order = 500

    def __init__(self, app: Any, server: Any, *, avatar: Avatar | None = None, **options: Any):
        self._avatar = avatar
        super().__init__(app, server, **options)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope["auth"] = self._avatar
        await self.app(scope, receive, send)


SUPERADMIN = Avatar("root", ["SUPERADMIN"])
_DEFAULT = object()  # sentinel: "declare a plain MemoryApiKeyStore"


def make_server(
    avatar: Avatar | None = SUPERADMIN, store: Any = _DEFAULT, with_jwt: bool = True
) -> AsgiServer:
    """A server whose chain stamps ``avatar``; ``store`` names the api-key store CLASS."""
    if store is _DEFAULT:
        store = MemoryApiKeyStore
    kwargs: dict[str, Any] = {
        "applications": [ServerApplication, (BaseApplication, {"mount": ""})],
        "middleware": {"stamp": {"avatar": avatar}},
        "middleware_registry": {"stamp": StampAuthMiddleware},
    }
    if store is not None:
        kwargs["tokens"] = {"store_class": store}
    if with_jwt:
        kwargs["auth"] = {"jwt": [{"secret": JWT_SECRET, "algorithm": "HS256", "name": "main"}]}
    return AsgiServer(**kwargs)


async def drive(
    server: AsgiServer, path: str, method: str = "GET", body: dict[str, Any] | None = None
) -> list[Message]:
    """Drive one request through ``server`` at the ASGI level (JSON body when given)."""
    headers: list[tuple[bytes, bytes]] = []
    raw = b""
    if body is not None:
        headers.append((b"content-type", b"application/json"))
        raw = json.dumps(body).encode()
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


class TestAuthGate:
    async def test_anonymous_is_challenged(self) -> None:
        assert status(await drive(make_server(avatar=None), "/_server/tokens/list")) == 401

    async def test_wrong_tags_are_forbidden(self) -> None:
        sent = await drive(make_server(avatar=Avatar("bob", ["staff"])), "/_server/tokens/list")
        assert status(sent) == 403

    async def test_superadmin_is_allowed(self) -> None:
        sent = await drive(make_server(), "/_server/tokens/list")
        assert status(sent) == 200
        assert payload(sent) == {"tokens": []}


class TestApiKeys:
    async def test_issue_returns_the_full_key_once_and_it_verifies(self) -> None:
        server = make_server()
        sent = await drive(
            server, "/_server/tokens/issue", "POST", body={"label": "ci-bot", "tags": ["ci"]}
        )
        assert status(sent) == 200
        body = payload(sent)
        assert body["key"].startswith("gak_")
        # the issued key actually verifies against the wired store
        assert server.api_key_store.verify(body["key"]) is not None

    async def test_issue_requires_a_label(self) -> None:
        sent = await drive(make_server(), "/_server/tokens/issue", "POST", body={"tags": []})
        assert "error" in payload(sent)

    async def test_list_never_exposes_the_secret_hash(self) -> None:
        server = make_server()
        await drive(server, "/_server/tokens/issue", "POST", body={"label": "k1"})
        listed = payload(await drive(server, "/_server/tokens/list"))["tokens"]
        assert listed and all("secret_hash" not in r for r in listed)

    async def test_revoke_disables_the_key(self) -> None:
        server = make_server()
        issued = payload(
            await drive(server, "/_server/tokens/issue", "POST", body={"label": "k"})
        )
        key_id = payload(await drive(server, "/_server/tokens/list"))["tokens"][0]["key_id"]
        sent = await drive(server, f"/_server/tokens/revoke?key_id={key_id}", "POST")
        assert payload(sent)["revoked"] is True
        # a revoked key no longer verifies
        assert server.api_key_store.verify(issued["key"]) is None

    async def test_delete_removes_the_record(self) -> None:
        server = make_server()
        await drive(server, "/_server/tokens/issue", "POST", body={"label": "k"})
        key_id = payload(await drive(server, "/_server/tokens/list"))["tokens"][0]["key_id"]
        sent = await drive(server, f"/_server/tokens/delete?key_id={key_id}", "POST")
        assert payload(sent)["deleted"] is True
        assert payload(await drive(server, "/_server/tokens/list"))["tokens"] == []


class TestCreateJwt:
    async def test_minted_jwt_verifies_against_the_same_config(self) -> None:
        server = make_server()
        sent = await drive(
            server,
            "/_server/tokens/create_jwt",
            "POST",
            body={"sub": "robot", "tags": ["worker"], "expires_in": 60},
        )
        assert status(sent) == 200
        token = payload(sent)["token"]
        # the token authenticates through the server's own AuthCore (same secret)
        avatar = server.auth_core.authenticate(
            {"headers": [(b"authorization", f"Bearer {token}".encode())]}
        )
        assert avatar is not None
        assert avatar.identity == "robot"
        assert avatar.tags == ["worker"]

    async def test_create_jwt_requires_a_subject(self) -> None:
        sent = await drive(make_server(), "/_server/tokens/create_jwt", "POST", body={"tags": []})
        assert "error" in payload(sent)

    async def test_create_jwt_without_a_symmetric_verifier_is_an_error(self) -> None:
        server = make_server(with_jwt=False)
        sent = await drive(
            server, "/_server/tokens/create_jwt", "POST", body={"sub": "robot"}
        )
        assert "error" in payload(sent)


class TestNoStore:
    async def test_handlers_answer_the_error_shape_without_a_store(self) -> None:
        sent = await drive(make_server(store=None), "/_server/tokens/list")
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_issue_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(store=None), "/_server/tokens/issue", "POST", body={"label": "ci"}
        )
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_revoke_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(store=None), "/_server/tokens/revoke?key_id=abc", "POST"
        )
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_delete_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(store=None), "/_server/tokens/delete?key_id=abc", "POST"
        )
        assert status(sent) == 200
        assert "error" in payload(sent)
