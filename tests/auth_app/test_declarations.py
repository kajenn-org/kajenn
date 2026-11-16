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

"""Contract tests: applications declare their tags and system users.

Every test drives a real ``AsgiServer``. ``AuthApplicationBase`` is mounted
over a ``MemoryAuthStore``; the tests read what a caller sees: the answers of
the management routes of ``/_auth``, the status of ruled routes and the log.

- After the boot the tag list holds the tags of ``_auth``, ``_server`` and of
  every application, each with its owner. A tag is written by its full name,
  as declared: in the record, in ``auth_rule`` and in a rule of the recipe.
- The system users an application declares are written with ``system`` true
  and no token.
- An application added to a running server, and one with a ``spawner``, bring
  their declarations.
- The management refuses to assign a tag that is not in the list.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from genro_routes import RoutingClass, route

from kajenn import AsgiServer, AuthApplicationBase, Avatar, RoutedApplication
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.config import AsgiConfigBuilder
from tests.conftest import start_server
from tests.core.test_external_application import running
from tests.storage_support import MemoryAuthStore, add_user, auth_application, issue_token

from .test_server_authentication import bearer

SYSTEM_TAGS = {
    ("AUTH_ADMIN", "_auth"),
    ("SERVER_ADMIN", "_server"),
    ("SERVER_METRICS", "_server"),
}


class Billing(RoutedApplication):
    """Declares bll_REPORTS and bll_AUDIT (child of bll_REPORTS) and the user ``<code>_bot``."""

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        return [
            {"code": "bll_REPORTS", "description": "Read the reports"},
            {"code": "bll_AUDIT", "parent": "bll_REPORTS", "reserved": True},
        ]

    @property
    def system_users(self) -> list[dict[str, Any]]:
        return [
            {
                "identity": f"{self.code}_bot",
                "status": "confirmed",
                "tags": ["bll_REPORTS", "SERVER_METRICS"],
                "groups": [],
            }
        ]

    @route(auth_rule="bll_REPORTS")
    def report(self) -> dict[str, str]:
        return {"report": self.code}

    @route()
    def audit(self) -> dict[str, str]:
        return {"audit": self.code}


class Sales(RoutedApplication):
    """Declares sls_REPORTS, a tag of another namespace."""

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        return [{"code": "sls_REPORTS"}]

    @route(auth_rule="sls_REPORTS")
    def report(self) -> dict[str, str]:
        return {"report": self.code}


class Reports(RoutingClass):
    """A branch whose route asks bll_REPORTS."""

    @route(auth_rule="bll_REPORTS")
    def detail(self) -> dict[str, str]:
        return {"detail": "branch"}


class LazyBranched(Billing):
    """Billing with the branch ``reports`` built on first use."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.add_branches({"name": "reports", "cls": Reports})


class Malformed(RoutedApplication):
    """Declares one bad and one good entry in each list."""

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        return [{"code": 5}, {"code": "gd_GOOD"}]

    @property
    def system_users(self) -> list[dict[str, Any]]:
        return [
            {"status": "confirmed", "tags": [], "groups": []},
            {"identity": "good_bot", "status": "confirmed", "tags": ["gd_GOOD"], "groups": []},
        ]


class BadCodes(RoutedApplication):
    """Declares a tag named like an operator, one with a character a rule cannot hold, one good."""

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        return [{"code": "not"}, {"code": "REP-X"}, {"code": "bd_GOOD"}]


class SharesReports(RoutedApplication):
    """Declares bll_REPORTS, the tag Billing declares."""

    @property
    def declared_tags(self) -> list[dict[str, Any]]:
        return [{"code": "bll_REPORTS"}]


class Plain(RoutedApplication):
    """Redefines neither property."""

    @route(auth_rule="bll_REPORTS")
    def report(self) -> dict[str, str]:
        return {"report": "plain"}


class Kept(MemoryAuthStore):
    """A store that already holds ``bll_REPORTS`` and ``billing_bot``."""

    def __init__(self, storage: object = None) -> None:
        super().__init__(storage)
        self.save_tag(
            {
                "code": "bll_REPORTS",
                "description": "kept",
                "owner": "",
                "reserved": False,
                "parent": None,
            }
        )
        self.save_user(
            {
                "identity": "billing_bot",
                "groups": [],
                "primary_group": None,
                "tags": [],
                "status": "banned",
                "system": False,
                "password_hash": None,
                "email": None,
                "external_ids": [],
            }
        )


async def call(
    server: AsgiServer,
    path: str,
    *,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any, dict[str, str]]:
    """Status, decoded JSON body and headers of one request; ``path`` may carry a query."""
    path, _, query = path.partition("?")
    all_headers = list(headers or [])
    raw = b""
    if body is not None:
        all_headers.append((b"content-type", b"application/json"))
        raw = json.dumps(body).encode()
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
            "query_string": query.encode(),
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


def make_server(*applications: tuple[type, dict[str, Any]], **auth_params: Any) -> AsgiServer:
    """``_auth`` over a ``MemoryAuthStore`` (or ``auth_params``) and ``applications``."""
    return AsgiServer(applications=[auth_application(**auth_params), *applications])


async def admin(server: AsgiServer) -> list[tuple[bytes, bytes]]:
    """The bearer of ``root``, holding ``AUTH_ADMIN``."""
    await start_server(server)
    add_user(server, "root", ["AUTH_ADMIN"])
    return bearer(issue_token(server, "root", "*"))


async def tag_list(server: AsgiServer) -> dict[str, dict[str, Any]]:
    """Every tag record, by code, as ``/_auth/tags/list`` answers it."""
    _, answer, _ = await call(server, "/_auth/tags/list", headers=await admin(server))
    return {record["code"]: record for record in answer["tags"]}


async def owners(server: AsgiServer) -> set[tuple[str, str]]:
    return {(code, record["owner"]) for code, record in (await tag_list(server)).items()}


async def status_with_tags(server: AsgiServer, path: str, tags: list[str]) -> int:
    """The status of ``path`` for a user holding exactly ``tags``."""
    identity = f"user{len(server.applications['_auth'].store.user_list)}"
    add_user(server, identity, tags)
    status, _, _ = await call(server, path, headers=bearer(issue_token(server, identity, "*")))
    return status


async def post(server: AsgiServer, path: str, body: dict[str, Any]) -> Any:
    _, answer, _ = await call(server, path, method="POST", body=body, headers=await admin(server))
    return answer


async def get(server: AsgiServer, path: str) -> Any:
    _, answer, _ = await call(server, path, headers=await admin(server))
    return answer


class TestBoot:
    async def test_the_list_holds_the_tags_of_server_auth_and_an_application(self) -> None:
        server = make_server((Billing, {"code": "billing"}))
        assert await owners(server) == SYSTEM_TAGS | {
            ("bll_REPORTS", "billing"),
            ("bll_AUDIT", "billing"),
        }

    async def test_a_tag_record_carries_its_declaration_as_written(self) -> None:
        server = make_server((Billing, {"code": "billing"}))
        tags = await tag_list(server)
        assert tags["bll_REPORTS"] == {
            "code": "bll_REPORTS",
            "description": "Read the reports",
            "owner": "billing",
            "reserved": False,
            "parent": None,
        }
        assert tags["bll_AUDIT"] == {
            "code": "bll_AUDIT",
            "description": "",
            "owner": "billing",
            "reserved": True,
            "parent": "bll_REPORTS",
        }

    async def test_a_system_user_is_written_complete_with_its_tags_and_no_token(self) -> None:
        server = make_server((Billing, {"code": "billing"}))
        user = await get(server, "/_auth/users/get?identity=billing_bot")
        assert (user["status"], user["system"], user["groups"]) == ("confirmed", True, [])
        assert user["tags"] == ["bll_REPORTS", "SERVER_METRICS"]
        tokens = (await get(server, "/_auth/tokens/list"))["tokens"]
        assert "billing_bot" not in [token["user"] for token in tokens]

    async def test_an_application_that_redefines_nothing_creates_nothing(self) -> None:
        server = make_server((Plain, {"code": "plain"}))
        users = (await get(server, "/_auth/users/list"))["users"]
        assert [user["identity"] for user in users] == ["root"]
        assert await owners(server) == SYSTEM_TAGS

    async def test_an_existing_record_is_left_as_it_is(self) -> None:
        server = make_server((Billing, {"code": "billing"}), store={"store_class": Kept})
        tags = await tag_list(server)
        assert (tags["bll_REPORTS"]["description"], tags["bll_REPORTS"]["owner"]) == (
            "kept",
            "",
        )
        user = await get(server, "/_auth/users/get?identity=billing_bot")
        assert (user["status"], user["system"], user["tags"]) == ("banned", False, [])

    async def test_without_auth_nothing_reads_the_declarations_and_nothing_fails(self) -> None:
        server = AsgiServer(applications=[(Billing, {"code": "billing"})])
        await start_server(server)
        status, _, _ = await call(server, "/billing/audit")
        assert status == 200

    async def test_a_store_that_cannot_write_leaves_auth_enabled(self) -> None:
        # The default store of a server without key material cannot write: the boot
        # goes on and _auth is enabled.
        server = AsgiServer(applications=[(AuthApplicationBase, {"code": "_auth"})])
        async with asyncio.timeout(5):
            await start_server(server)
        assert server.is_enabled("_auth")

    async def test_a_malformed_entry_is_skipped_and_the_others_are_written(self) -> None:
        server = make_server((Malformed, {"code": "good"}), (Billing, {"code": "billing"}))
        found = await owners(server)
        assert {("gd_GOOD", "good"), ("bll_REPORTS", "billing")} <= found
        user = await get(server, "/_auth/users/get?identity=good_bot")
        assert (user["system"], user["tags"]) == (True, ["gd_GOOD"])
        assert (await get(server, "/_auth/users/get?identity=billing_bot"))["system"] is True

    async def test_a_tag_code_a_rule_cannot_hold_is_skipped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            found = await owners(make_server((BadCodes, {"code": "bad"})))
        codes = {code for code, _ in found}
        assert "bd_GOOD" in codes
        assert not codes & {"not", "REP-X"}
        assert "a rule cannot hold the code 'not'" in caplog.text
        assert "a rule cannot hold the code 'REP-X'" in caplog.text


class TestRules:
    async def test_a_holder_of_the_declared_tag_opens_the_route(self) -> None:
        server = make_server((Billing, {"code": "billing"}))
        assert await status_with_tags(server, "/billing/report", ["bll_REPORTS"]) == 200
        assert await status_with_tags(server, "/billing/report", ["SERVER_ADMIN"]) == 403

    async def test_two_namespaces_keep_their_routes_apart(self) -> None:
        server = make_server((Billing, {"code": "billing"}), (Sales, {"code": "sales"}))
        found = await owners(server)
        assert {("bll_REPORTS", "billing"), ("sls_REPORTS", "sales")} <= found
        assert await status_with_tags(server, "/billing/report", ["bll_REPORTS"]) == 200
        assert await status_with_tags(server, "/sales/report", ["bll_REPORTS"]) == 403
        assert await status_with_tags(server, "/sales/report", ["sls_REPORTS"]) == 200
        assert await status_with_tags(server, "/billing/report", ["sls_REPORTS"]) == 403

    async def test_the_same_class_mounted_twice_shares_one_tag(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            server = make_server((Billing, {"code": "billing"}), (Billing, {"code": "billing2"}))
            found = await owners(server)
        assert ("bll_REPORTS", "billing") in found
        assert ("bll_REPORTS", "billing2") not in found
        assert "'bll_REPORTS' already exists with owner 'billing'" in caplog.text
        assert await status_with_tags(server, "/billing/report", ["bll_REPORTS"]) == 200
        assert await status_with_tags(server, "/billing2/report", ["bll_REPORTS"]) == 200

    async def test_a_tag_that_already_has_another_owner_is_reported_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            server = make_server((Billing, {"code": "billing"}), (SharesReports, {"code": "other"}))
            found = await owners(server)
        assert ("bll_REPORTS", "billing") in found
        assert ("bll_REPORTS", "other") not in found
        assert "tag not written" in caplog.text
        assert "'bll_REPORTS' already exists with owner 'billing'" in caplog.text

    async def test_a_tag_declared_once_gives_no_owner_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            await owners(make_server((Billing, {"code": "billing"})))
        assert "already exists with owner" not in caplog.text

    async def test_a_route_of_a_lazy_branch_asks_the_tag_as_written(self) -> None:
        server = make_server((LazyBranched, {"code": "billing"}))
        path = "/billing/reports/detail"
        assert await status_with_tags(server, path, ["bll_REPORTS"]) == 200
        assert await status_with_tags(server, path, ["SERVER_ADMIN"]) == 403

    async def test_a_rule_given_by_the_recipe_is_used_as_written(self) -> None:
        class Recipe(AsgiConfigBuilder):
            default_config = False

            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=0)
                apps = cfg.applications()
                apps.application(code="_auth", app_class=AuthApplicationBase).store(
                    store_class=MemoryAuthStore
                )
                billing = apps.application(code="billing", app_class=Billing)
                billing.plugin_configuration(plugin="auth").configure(
                    route="audit", rule="bll_AUDIT|SERVER_ADMIN"
                )

        server = AsgiServer(config=Recipe)
        assert await status_with_tags(server, "/billing/audit", ["bll_AUDIT"]) == 200
        assert await status_with_tags(server, "/billing/audit", ["SERVER_ADMIN"]) == 200
        assert await status_with_tags(server, "/billing/audit", ["bll_REPORTS"]) == 403


class TestRefusal:
    async def test_a_group_with_a_tag_not_in_the_list_is_refused(self) -> None:
        server = make_server((Billing, {"code": "billing"}))
        refused = await post(server, "/_auth/groups/create_group?code=dev", {"tags": ["NOPE"]})
        assert "error" in refused
        assert "error" in await get(server, "/_auth/groups/get?code=dev")
        created = await post(
            server, "/_auth/groups/create_group?code=dev", {"tags": ["bll_REPORTS"]}
        )
        assert created == {"code": "dev", "tags": ["bll_REPORTS"]}
        assert "error" in await post(server, "/_auth/groups/save?code=dev", {"tags": ["NOPE"]})
        assert (await get(server, "/_auth/groups/get?code=dev"))["tags"] == ["bll_REPORTS"]

    @pytest.mark.parametrize("tags", [None, 5, "bll_REPORTS", [["bll_REPORTS"]]])
    async def test_tags_that_are_not_a_list_of_codes_are_refused(self, tags: Any) -> None:
        server = make_server((Billing, {"code": "billing"}))
        body = {"tags": tags}
        assert "error" in await post(server, "/_auth/groups/create_group?code=dev", body)
        assert "error" in await get(server, "/_auth/groups/get?code=dev")
        assert "error" in await post(server, "/_auth/users/create_user?identity=bob", body)
        assert "error" in await get(server, "/_auth/users/get?identity=bob")

    async def test_a_user_with_a_tag_not_in_the_list_is_refused(self) -> None:
        server = make_server()
        assert "error" in await post(
            server, "/_auth/users/create_user?identity=bob", {"tags": ["NOPE"]}
        )
        assert "error" in await get(server, "/_auth/users/get?identity=bob")
        assert "error" not in await post(
            server, "/_auth/users/create_user?identity=bob", {"tags": ["SERVER_ADMIN"]}
        )
        assert "error" in await post(server, "/_auth/users/save?identity=bob", {"tags": ["NOPE"]})
        assert (await get(server, "/_auth/users/get?identity=bob"))["tags"] == ["SERVER_ADMIN"]

    async def test_a_token_with_a_tag_not_in_the_list_is_refused(self) -> None:
        server = make_server()
        add_user(server, "bob", ["SERVER_ADMIN"])
        assert "error" in await post(
            server, "/_auth/tokens/issue", {"user": "bob", "tags": ["NOPE"]}
        )
        assert "key" in await post(
            server, "/_auth/tokens/issue", {"user": "bob", "tags": ["SERVER_ADMIN"]}
        )
        assert "key" in await post(server, "/_auth/tokens/issue", {"user": "bob", "tags": "*"})
        tokens = (await get(server, "/_auth/tokens/list"))["tokens"]
        assert [token["tags"] for token in tokens if token["user"] == "bob"] == [
            ["SERVER_ADMIN"],
            "*",
        ]


class TestLate:
    async def test_an_application_added_to_a_running_server_brings_its_tags(self) -> None:
        server = make_server()
        await start_server(server)
        added = await server.kbus_call(
            "/_server/applications/add",
            {"app_class": "tests.auth_app.test_declarations:Billing", "code": "late"},
            auth=Avatar("ops", ["SERVER_ADMIN"]),
        )
        assert added == {"code": "late", "state": "disabled"}
        async with asyncio.timeout(5):
            while not server.is_enabled("late"):
                await asyncio.sleep(0.005)
        assert ("bll_REPORTS", "late") in await owners(server)
        assert (await get(server, "/_auth/users/get?identity=late_bot"))["system"] is True
        assert await status_with_tags(server, "/late/report", ["bll_REPORTS"]) == 200

    async def test_an_application_with_a_spawner_brings_its_tags_and_system_users(
        self,
        tmp_path: Path,
    ) -> None:
        async with running(write_recipe(tmp_path)) as server:
            assert ("bll_REPORTS", "billing") in await owners(server)
            user = await get(server, "/_auth/users/get?identity=billing_bot")
            assert (user["system"], user["tags"]) == (True, ["bll_REPORTS", "SERVER_METRICS"])
            key = issue_token(server, "billing_bot", "*")
            status, answer, _ = await call(server, "/billing/report", headers=bearer(key))
            assert (status, answer) == (200, {"report": "billing"})
            assert await status_with_tags(server, "/billing/report", ["SERVER_ADMIN"]) == 403


RECIPE = """
import os

from genro_routes import route

from kajenn import AuthApplicationBase
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from tests.auth_app.test_declarations import Billing as Declaring
from tests.storage_support import MemoryAuthStore


class Billing(Declaring):
    @route()
    def prepared_state(self):
        return {"prepared": True, "pid": os.getpid()}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess",
                         request_timeout=5)
        apps.application(code="_auth", app_class=AuthApplicationBase).store(
            store_class=MemoryAuthStore)
"""


def write_recipe(tmp_path: Path) -> str:
    path = tmp_path / "config.py"
    path.write_text(RECIPE)
    return str(path)
