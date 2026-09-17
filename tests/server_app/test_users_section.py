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

"""UsersSection tests (Macro 5b Phase 5).

Requests drive a REAL ``AsgiServer`` at the ASGI level: the ``_server`` app is
auto-mounted, so the section lives at ``/_server/users/...``. A test middleware
(order 500, after the real AuthMiddleware) stamps a fixed identity on the scope
so a SUPERADMIN avatar reaches the ruled routes; the user store is wired with
``users=``. Every route is ``auth_rule="SUPERADMIN"``: an anonymous request
answers 401 and a wrong-tag one 403, and a server with no store answers the
``{"error": ...}``
shape.
"""

from __future__ import annotations

import json
from typing import Any

from kajenn import AsgiServer, Avatar, BaseApplication, UserStore
from kajenn_server_app import ServerApplication
from kajenn.middleware.base import BaseMiddleware
from kajenn.types import Message, Scope


class MemoryUserStore(UserStore):
    """In-memory ``UserStore`` backend: the contract suite over a dict."""

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
_DEFAULT_STORE = object()  # sentinel: "declare a plain MemoryUserStore"


def store_holding(store_class: type, *records: dict) -> type:
    """A store CLASS pre-loaded with *records* — the configuration names a class.

    A test that needs seeded data declares the class that carries it instead of
    handing the server a built store (#91).
    """

    class Seeded(store_class):  # type: ignore[misc, valid-type]
        def __init__(self, storage: object = None) -> None:
            super().__init__(storage)
            for record in records:
                self.save(record)

    return Seeded


def make_server(avatar: Avatar | None, store: Any = _DEFAULT_STORE) -> AsgiServer:
    """A server whose chain stamps ``avatar``; ``store`` names the user store CLASS.

    ``store`` defaults to ``MemoryUserStore``; pass ``None`` for a server without
    identity, or a class from ``store_holding`` to start with records.
    """
    if store is _DEFAULT_STORE:
        store = MemoryUserStore
    kwargs: dict[str, Any] = {
        "applications": [ServerApplication, (BaseApplication, {"mount": ""})],
        "middleware": {"stamp": {"avatar": avatar}},
        "middleware_registry": {"stamp": StampAuthMiddleware},
    }
    if store is not None:
        kwargs["users"] = {"store_class": store}
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
        sent = await drive(make_server(None), "/_server/users/list")
        assert status(sent) == 401

    async def test_wrong_tags_are_forbidden(self) -> None:
        sent = await drive(make_server(Avatar("bob", ["staff"])), "/_server/users/list")
        assert status(sent) == 403

    async def test_superadmin_is_allowed(self) -> None:
        sent = await drive(make_server(SUPERADMIN), "/_server/users/list")
        assert status(sent) == 200
        assert payload(sent) == {"users": []}


class TestCrud:
    async def test_create_user_hashes_and_hides_the_hash(self) -> None:
        server = make_server(SUPERADMIN)
        sent = await drive(
            server,
            "/_server/users/create_user?identity=alice",
            "POST",
            body={"password": "pw", "password_confirm": "pw", "tags": ["editor"], "enabled": True},
        )
        assert status(sent) == 200
        body = payload(sent)
        assert body["identity"] == "alice"
        assert body["tags"] == ["editor"]
        assert "password_hash" not in body  # the hash never crosses the wire
        # the stored user actually authenticates with the plaintext password
        assert server.user_store.verify("alice", "pw") is not None

    async def test_create_user_minimal_body_yields_a_working_user(self) -> None:
        # No enabled/tags in the body: defaults must produce a user that can
        # log in (enabled=True) and build an Avatar (tags=[]) — a silently
        # unusable record is the bug this guards against.
        server = make_server(SUPERADMIN)
        sent = await drive(
            server,
            "/_server/users/create_user?identity=bob",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
        )
        assert status(sent) == 200
        record = server.user_store.verify("bob", "pw")
        assert record is not None
        assert record["enabled"] is True
        assert record["tags"] == []

    async def test_create_user_body_can_still_create_disabled(self) -> None:
        server = make_server(SUPERADMIN)
        await drive(
            server,
            "/_server/users/create_user?identity=carl",
            "POST",
            body={"password": "pw", "password_confirm": "pw", "enabled": False},
        )
        assert server.user_store.verify("carl", "pw") is None

    async def test_create_user_rejects_a_mismatched_confirmation(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN),
            "/_server/users/create_user?identity=alice",
            "POST",
            body={"password": "pw", "password_confirm": "different"},
        )
        assert "error" in payload(sent)

    async def test_create_user_rejects_a_duplicate(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "x", "tags": [], "enabled": True})
        sent = await drive(
            make_server(SUPERADMIN, store),
            "/_server/users/create_user?identity=alice",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
        )
        assert "error" in payload(sent)

    async def test_list_and_get_strip_the_hash(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "secret", "tags": ["a"], "enabled": True})
        server = make_server(SUPERADMIN, store)
        listed = payload(await drive(server, "/_server/users/list"))
        assert all("password_hash" not in u for u in listed["users"])
        got = payload(await drive(server, "/_server/users/get?identity=alice"))
        assert got["identity"] == "alice"
        assert "password_hash" not in got

    async def test_save_merges_metadata_and_preserves_the_hash(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "keep", "tags": ["a"], "enabled": True})
        server = make_server(SUPERADMIN, store)
        sent = await drive(
            server,
            "/_server/users/save?identity=alice",
            "POST",
            body={"tags": ["a", "b"], "email": "alice@x.io"},
        )
        assert status(sent) == 200
        stored = server.user_store.get("alice")
        assert stored["tags"] == ["a", "b"]
        assert stored["email"] == "alice@x.io"  # new metadata field, no signature change
        assert stored["password_hash"] == "keep"  # the credential is untouched

    async def test_save_ignores_a_password_hash_on_the_wire(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "keep", "tags": [], "enabled": True})
        server = make_server(SUPERADMIN, store)
        await drive(
            server,
            "/_server/users/save?identity=alice",
            "POST",
            body={"password_hash": "attacker", "tags": ["x"]},
        )
        assert server.user_store.get("alice")["password_hash"] == "keep"  # injection dropped

    async def test_save_on_a_missing_user_is_an_error(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN), "/_server/users/save?identity=ghost", "POST", body={"tags": []}
        )
        assert "error" in payload(sent)

    async def test_set_password_changes_the_credential(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "old", "tags": [], "enabled": True})
        server = make_server(SUPERADMIN, store)
        sent = await drive(
            server,
            "/_server/users/set_password?identity=alice",
            "POST",
            body={"password": "new", "password_confirm": "new"},
        )
        assert status(sent) == 200
        assert server.user_store.verify("alice", "new") is not None

    async def test_set_password_rejects_a_mismatch(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "old", "tags": [], "enabled": True})
        sent = await drive(
            make_server(SUPERADMIN, store),
            "/_server/users/set_password?identity=alice",
            "POST",
            body={"password": "a", "password_confirm": "b"},
        )
        assert "error" in payload(sent)

    async def test_delete_removes_the_record(self) -> None:
        store = store_holding(MemoryUserStore, {"identity": "alice", "password_hash": "x", "tags": [], "enabled": True})
        server = make_server(SUPERADMIN, store)
        sent = await drive(server, "/_server/users/delete?identity=alice", "POST")
        assert payload(sent)["deleted"] is True
        assert server.user_store.get("alice") is None


class TestNoStore:
    async def test_handlers_answer_the_error_shape_without_a_store(self) -> None:
        sent = await drive(make_server(SUPERADMIN, store=None), "/_server/users/list")
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_get_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(make_server(SUPERADMIN, store=None), "/_server/users/get?identity=alice")
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_create_user_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN, store=None),
            "/_server/users/create_user?identity=alice",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
        )
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_save_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN, store=None),
            "/_server/users/save?identity=alice",
            "POST",
            body={"tags": []},
        )
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_set_password_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN, store=None),
            "/_server/users/set_password?identity=alice",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
        )
        assert status(sent) == 200
        assert "error" in payload(sent)

    async def test_delete_without_a_store_is_the_error_shape(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN, store=None), "/_server/users/delete?identity=alice", "POST"
        )
        assert status(sent) == 200
        assert "error" in payload(sent)


class TestValidationErrors:
    async def test_get_missing_user_reports_no_such_user(self) -> None:
        sent = await drive(make_server(SUPERADMIN), "/_server/users/get?identity=ghost")
        assert status(sent) == 200
        assert payload(sent)["error"] == "No such user: ghost"

    async def test_create_user_without_identity_is_rejected(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN),
            "/_server/users/create_user",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
        )
        assert status(sent) == 200
        assert payload(sent)["error"] == "Identity is required"

    async def test_set_password_on_a_missing_user_is_an_error(self) -> None:
        sent = await drive(
            make_server(SUPERADMIN),
            "/_server/users/set_password?identity=ghost",
            "POST",
            body={"password": "pw", "password_confirm": "pw"},
        )
        assert status(sent) == 200
        assert payload(sent)["error"] == "No such user: ghost"
