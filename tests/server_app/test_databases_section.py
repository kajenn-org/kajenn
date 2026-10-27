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

"""The ``_server/databases`` section: the registry and the migration routes.

Requests drive a REAL ``AsgiServer`` at the ASGI level, with the stamping
middleware of the monitor suite, so the ``SERVER_ADMIN`` gate runs against a
real avatar. No driver: ``MigratingHandler`` offers both optional operations
and records each call; ``PlainHandler`` offers none.

What the suite pins:

- the gate — 401 anonymous, 403 wrong tags, 200 ``SERVER_ADMIN``, on every route;
- ``list`` — name, owner by the ``owner:db`` rule, handler class, capabilities;
- ``plan`` / ``apply`` — ``{"name", "result"}`` with the handler's result,
  reached with the database name; 422 on a database whose handler lacks the
  operation, 404 on an unknown name, 400 on a missing ``name``.
"""

from __future__ import annotations

from typing import Any

from kajenn import AsgiServer, Avatar, BaseApplication, BaseServer, DbHandlerBase
from kajenn.server_app import ServerApplication

from tests.server_app.test_server_monitor import StampAuthMiddleware, drive, payload

SERVER_ADMIN = Avatar("ops", ["SERVER_ADMIN"])
DATABASES_ROUTES = (
    ("GET", "/_server/databases/list"),
    ("GET", "/_server/databases/plan?name=default"),
    ("POST", "/_server/databases/apply?name=default"),
)


class MigratingHandler(DbHandlerBase):
    """Offers ``migration_plan`` and ``migrate``; records every call."""

    def __init__(self, server: BaseServer) -> None:
        super().__init__(server)
        self.calls: list[tuple[str, str]] = []

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"migration_plan", "migrate"})

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        pass

    def migration_plan(self, name: str) -> dict[str, Any]:
        self.calls.append(("migration_plan", name))
        return {"sql": [f"CREATE TABLE {name}_t ()"], "empty": False}

    def migrate(self, name: str) -> dict[str, Any]:
        self.calls.append(("migrate", name))
        return {"applied": name}


class PlainHandler(DbHandlerBase):
    """Offers no optional operation."""

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        pass


class NoneHandler(MigratingHandler):
    """``migrate`` returns ``None``, as a backend with nothing to report does."""

    def migrate(self, name: str) -> None:
        self.calls.append(("migrate", name))


def make_server(avatar: Avatar | None) -> AsgiServer:
    """A server database and an application database on ``MigratingHandler``, one on ``PlainHandler``."""
    server = AsgiServer(
        applications=[ServerApplication, (BaseApplication, {"code": "chat", "mount": ""})],
        middleware={"stamp": {"avatar": avatar}},
        middleware_registry={"stamp": StampAuthMiddleware},
    )
    server.add_database("default", {}, MigratingHandler)
    server.add_database("chat:archive", {}, MigratingHandler)
    server.add_database("audit", {}, PlainHandler)
    return server


def status(sent: list[Any]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def body_text(sent: list[Any]) -> str:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()


class TestDatabasesGate:
    """``auth_rule="SERVER_ADMIN"`` on every route of the section."""

    async def test_anonymous_is_challenged(self) -> None:
        server = make_server(None)
        for method, path in DATABASES_ROUTES:
            assert status(await drive(server, path, method=method)) == 401

    async def test_wrong_tags_are_forbidden(self) -> None:
        server = make_server(Avatar("bob", ["SUPERADMIN"]))
        for method, path in DATABASES_ROUTES:
            assert status(await drive(server, path, method=method)) == 403

    async def test_server_admin_is_allowed(self) -> None:
        server = make_server(SERVER_ADMIN)
        for method, path in DATABASES_ROUTES:
            assert status(await drive(server, path, method=method)) == 200


class TestDatabasesList:
    async def test_every_database_with_owner_handler_and_capabilities(self) -> None:
        sent = await drive(make_server(SERVER_ADMIN), "/_server/databases/list")
        assert status(sent) == 200
        module = __name__
        assert payload(sent) == [
            {
                "name": "default",
                "owner": None,
                "handler_class": f"{module}.MigratingHandler",
                "capabilities": ["migrate", "migration_plan"],
            },
            {
                "name": "chat:archive",
                "owner": "chat",
                "handler_class": f"{module}.MigratingHandler",
                "capabilities": ["migrate", "migration_plan"],
            },
            {
                "name": "audit",
                "owner": None,
                "handler_class": f"{module}.PlainHandler",
                "capabilities": [],
            },
        ]


class TestMigrationRoutes:
    async def test_plan_returns_the_handler_result_for_that_name(self) -> None:
        server = make_server(SERVER_ADMIN)
        sent = await drive(server, "/_server/databases/plan?name=chat:archive")
        assert status(sent) == 200
        assert payload(sent) == {
            "name": "chat:archive",
            "result": {"sql": ["CREATE TABLE chat:archive_t ()"], "empty": False},
        }
        assert server.databases["chat:archive"].calls == [("migration_plan", "chat:archive")]

    async def test_apply_returns_the_handler_result_for_that_name(self) -> None:
        server = make_server(SERVER_ADMIN)
        sent = await drive(server, "/_server/databases/apply?name=default", method="POST")
        assert status(sent) == 200
        assert payload(sent) == {"name": "default", "result": {"applied": "default"}}
        assert server.databases["default"].calls == [("migrate", "default")]

    async def test_apply_refuses_get_and_does_not_migrate(self) -> None:
        server = make_server(SERVER_ADMIN)
        sent = await drive(server, "/_server/databases/apply?name=default")
        assert status(sent) == 405
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert (b"allow", b"POST") in start["headers"]
        assert server.databases["default"].calls == []

    async def test_an_incapable_database_is_refused_with_the_reason(self) -> None:
        server = make_server(SERVER_ADMIN)
        for operation, method, path in (
            ("migration_plan", "GET", "/_server/databases/plan?name=audit"),
            ("migrate", "POST", "/_server/databases/apply?name=audit"),
        ):
            sent = await drive(server, path, method=method)
            assert status(sent) == 422
            assert f"PlainHandler does not offer {operation}" in body_text(sent)

    async def test_an_unknown_database_is_not_found(self) -> None:
        server = make_server(SERVER_ADMIN)
        for method, path in (
            ("GET", "/_server/databases/plan?name=ghost"),
            ("POST", "/_server/databases/apply?name=ghost"),
        ):
            assert status(await drive(server, path, method=method)) == 404

    async def test_a_none_result_is_still_a_json_object(self) -> None:
        server = make_server(SERVER_ADMIN)
        server.add_database("bare", {}, NoneHandler)
        sent = await drive(server, "/_server/databases/apply?name=bare", method="POST")
        assert status(sent) == 200
        assert payload(sent) == {"name": "bare", "result": None}

    async def test_a_missing_name_is_a_client_error(self) -> None:
        server = make_server(SERVER_ADMIN)
        for method, path in (("GET", "/_server/databases/plan"), ("POST", "/_server/databases/apply")):
            sent = await drive(server, path, method=method)
            assert status(sent) == 400
            assert "missing a required argument: 'name'" in body_text(sent)
