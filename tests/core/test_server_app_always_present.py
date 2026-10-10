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

"""``_server`` is always mounted; the configuration only customises it.

A server built from kwargs or from a recipe has the management application
whether or not it was named. ``application(code="_server", ...)`` is the way to
customise it (login policy, OIDC, a subclass), never a declaration. The role
process of an external application mounts none: its ``/_server/...`` calls go
to the parent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from genro_routes import route

from kajenn import AsgiServer, BaseApplication, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.exceptions import HTTPUnauthorized
from kajenn.server_app import ServerApplication
from kajenn.types import Message, Scope

BEARER = {"bearer": {"svc": {"token": "sk_live_xyz", "tags": "admin"}}}


class Api(RoutedApplication):
    @route(auth_rule="admin")
    def secret(self, _request=None) -> dict[str, Any]:
        return {"identity": _request.avatar().identity}


class CustomServerApp(ServerApplication):
    """A subclass the configuration may hand as app_class."""


async def drive(
    server: AsgiServer, path: str, headers: list[tuple[bytes, bytes]] | None = None
) -> tuple[int, Any]:
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], (json.loads(raw) if raw and start["status"] < 400 else None)


class TestTheKwargsRoad:
    def test_a_bare_server_has_the_management_application(self) -> None:
        # wf:contract: AsgiServer() mounts ServerApplication under _server with the defaults.
        server = AsgiServer()
        app = server.applications["_server"]
        assert isinstance(app, ServerApplication)
        assert app.mount == "_server"
        assert app.server is server

    async def test_the_descriptor_answers(self) -> None:
        # wf:contract: /_server/ answers 200 with the descriptor on a server that named nothing.
        status, data = await drive(AsgiServer(applications=[(BaseApplication, {"mount": ""})]), "/_server/")
        assert status == 200
        assert "sections" in data

    def test_a_declared_one_is_kept_as_declared(self) -> None:
        # wf:contract: a ServerApplication given in applications= is the one mounted, not a
        # wf:contract: second default; a subclass is kept as well.
        server = AsgiServer(applications=[(CustomServerApp, {"login": {"max_attempts": 3}})])
        app = server.applications["_server"]
        assert type(app) is CustomServerApp
        assert sum(1 for a in server.applications.values() if a.code == "_server") == 1

    async def test_header_credentials_are_verified_without_naming_server(self) -> None:
        # wf:contract: with auth= credentials and no ServerApplication named, a Bearer is
        # wf:contract: verified through the default /_server/auth/authenticate: the ruled route
        # wf:contract: answers 200, an invalid Bearer 401.
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""}), (Api, {"code": "api"})], auth=BEARER
        )
        status, data = await drive(server, "/api/secret", [(b"authorization", b"Bearer sk_live_xyz")])
        assert (status, data) == (200, {"identity": "svc"})
        status, _ = await drive(server, "/api/secret", [(b"authorization", b"Bearer nope")])
        assert status == 401


class PlainRecipe(AsgiConfigBuilder):
    """A recipe that names nothing about _server."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications(default="api")
        apps.application(code="api", app_class=Api)


class CustomisingRecipe(AsgiConfigBuilder):
    """A recipe customising _server without naming its class."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications(default="api")
        apps.application(code="api", app_class=Api)
        apps.application(code="_server").login(max_attempts=3, backoff=7)


class SubclassRecipe(AsgiConfigBuilder):
    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications(default="api")
        apps.application(code="api", app_class=Api)
        apps.application(code="_server", app_class=CustomServerApp)


class WrongClassRecipe(AsgiConfigBuilder):
    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications(default="api")
        apps.application(code="api", app_class=Api)
        apps.application(code="_server", app_class=Api)


class TestTheConfigurationRoad:
    def test_a_recipe_naming_nothing_has_it(self) -> None:
        # wf:contract: a recipe without application(code="_server") still mounts it.
        server = AsgiServer(config=PlainRecipe)
        assert isinstance(server.applications["_server"], ServerApplication)

    def test_the_recipe_customises_without_app_class(self) -> None:
        # wf:contract: application(code="_server") without app_class means ServerApplication,
        # wf:contract: and its login/oidc words reach the instance.
        server = AsgiServer(config=CustomisingRecipe)
        app = server.applications["_server"]
        assert type(app) is ServerApplication
        assert app.login_policy["max_attempts"] == 3
        assert app.login_policy["backoff"] == 7

    def test_the_recipe_may_hand_a_subclass(self) -> None:
        # wf:contract: app_class on code "_server" is accepted when it subclasses ServerApplication.
        server = AsgiServer(config=SubclassRecipe)
        assert type(server.applications["_server"]) is CustomServerApp

    def test_a_class_that_is_not_a_server_application_is_refused(self) -> None:
        # wf:contract: app_class on code "_server" that is not a ServerApplication subclass is a
        # wf:contract: configuration error naming the class.
        with pytest.raises(ValueError, match="Api"):
            AsgiServer(config=WrongClassRecipe)


ROLE_RECIPE = '''
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Billing(RoutedApplication):
    @route()
    def total(self, order):
        return {"total": int(order) * 2}


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    site_name = "billingsite"

    def applications_section(self, cfg):
        apps = cfg.applications()
        apps.application(code="billing", app_class=Billing, spawner="subprocess")
'''


class TestTheRoleProcess:
    def test_a_role_process_mounts_no_server_application(self, tmp_path: Path) -> None:
        # wf:contract: the process of an external application (role=application:<code>) hosts
        # wf:contract: only its application: no _server, so /_server/... goes to the parent.
        recipe = tmp_path / "config.py"
        recipe.write_text(ROLE_RECIPE)
        child = AsgiServer(config=str(recipe), role="application:billing")
        assert "_server" not in child.applications
        assert list(child.applications) == ["billing"]
        parent = AsgiServer(config=str(recipe))
        assert "_server" in parent.applications


class TestTheMissingRouteCase:
    async def test_a_channel_route_pointing_at_nothing_is_401(self) -> None:
        # wf:contract: the 404 branch of authenticate_credential survives for a configured
        # wf:contract: channel route nobody serves: 401 with the challenge, not 500.
        server = AsgiServer(
            applications=[(BaseApplication, {"code": "site"})],
            auth=BEARER,
            channels={"mcp": {"authentication_route": "/idp/check"}},
        )
        with pytest.raises(HTTPUnauthorized) as refused:
            await server.authenticate_credential("Bearer sk_live_xyz", "mcp")
        assert (b"www-authenticate", b"Bearer") in refused.value.headers
