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

"""The ``_server/monitor`` section: the page, the snapshot, the panels.

Requests drive a REAL ``AsgiServer`` at the ASGI level: the ``_server`` app is
declared like any other, and the monitor lives at ``/_server/monitor/...``. A test
middleware (order 500, after the real AuthMiddleware) stamps a fixed identity
on the scope, so the ``SERVER_ADMIN`` gate is exercised with a real avatar.

What the suite pins:

- the gate — an anonymous request is challenged (401) and a wrong-tag one
  refused (403), on every route of the section;
- the aggregate — one entry per mounted application, keyed by mount, the
  ``_server`` app itself absent (the monitor is its face, not a tab);
- the contract — an application that overrides ``app_snapshot``/``app_panel``
  is carried through verbatim, one that overrides nothing still shows up with
  its identity facts and the generic panel;
- the shipped panel — an app declaring ``panel_source`` gets its ``src`` filled
  in and its module served, while an explicit ``src`` is left alone;
- the bootstrap admin — it carries ``SERVER_ADMIN``, so a freshly installed
  server is observable by the identity that configures it.
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


class RichApplication(BaseApplication):
    """An app that declares its own monitor face, panel included."""

    code = "rich"

    @property
    def app_snapshot(self) -> dict[str, Any]:
        return {**super().app_snapshot, "orders": 7}

    @property
    def app_panel(self) -> dict[str, Any]:
        return {"panel": "orders", "src": "./orders_panel.js"}


class ShippingApplication(BaseApplication):
    """An app that ships its panel module instead of serving it itself."""

    code = "shipping"

    @property
    def app_panel(self) -> dict[str, Any]:
        return {"panel": "shipping"}

    @property
    def panel_source(self) -> str:
        return "export default { render(target, context) {} };"


SERVER_ADMIN = Avatar("ops", ["SERVER_ADMIN"])
MONITOR_ROUTES = ("/_server/monitor/snapshot", "/_server/monitor/panels")


def make_server(avatar: Avatar | None, *applications: Any) -> AsgiServer:
    """A server whose chain stamps ``avatar``, mounting ``applications``.

    Each entry is a class or a ``(class, params)`` pair — the one form the
    configuration accepts.
    """
    return AsgiServer(
        applications=[
            ServerApplication,
            *(applications or [(BaseApplication, {"mount": ""})]),
        ],
        middleware={"stamp": {"avatar": avatar}},
        middleware_registry={"stamp": StampAuthMiddleware},
    )


async def drive(server: AsgiServer, path: str, accept: bytes | None = None) -> list[Message]:
    """One GET through ``server``, query string split off the path.

    The ``http_request`` fixture puts the whole string in ``path``; ``panel``
    is addressed by query, so it needs the ASGI split.
    """
    path, _, query = path.partition("?")
    headers = [(b"accept", accept)] if accept else []
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "headers": headers,
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return sent


def payload(sent: list[Message]) -> Any:
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(body)


class TestMonitorGate:
    """``auth_rule="SERVER_ADMIN"`` on the page and both data endpoints."""

    async def test_anonymous_is_challenged(self, http_request, response_status) -> None:
        """No identity: 401, the status ``ErrorMiddleware`` turns into a login."""
        server = make_server(None)
        for path in MONITOR_ROUTES:
            assert response_status(await http_request(server, path)) == 401

    async def test_wrong_tags_are_forbidden(self, http_request, response_status) -> None:
        server = make_server(Avatar("bob", ["SUPERADMIN"]))
        for path in MONITOR_ROUTES:
            assert response_status(await http_request(server, path)) == 403

    async def test_server_admin_is_allowed(self, http_request, response_status) -> None:
        server = make_server(SERVER_ADMIN)
        for path in MONITOR_ROUTES:
            assert response_status(await http_request(server, path)) == 200

    async def test_a_browser_gets_the_same_401(
        self, http_request, response_status, response_headers
    ) -> None:
        """D-SA-4: no redirect to a login page, for a browser either.

        ``ErrorMiddleware`` used to turn this 401 into a 302 to
        ``/_server/login_page``. The core answers the bare status now; pointing
        a browser at a login surface is the business of whoever owns one.
        """
        sent = await http_request(
            make_server(None), "/_server/monitor/snapshot", headers=[(b"accept", b"text/html")]
        )
        assert response_status(sent) == 401
        assert b"location" not in response_headers(sent)


class TestNoMonitorPage:
    """D-SA-3: the monitor page is gramlot's; the section serves its data."""

    async def test_the_section_root_is_gone(self, http_request, response_status) -> None:
        sent = await http_request(make_server(SERVER_ADMIN), "/_server/monitor/")
        assert response_status(sent) == 404


class TestSnapshot:
    """The polled aggregate: server facts plus one entry per application."""

    async def test_server_facts(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, (BaseApplication, {"mount": ""}))
        facts = payload(await http_request(server, "/_server/monitor/snapshot"))["server"]
        assert facts["pid"] > 0
        assert "monitor" in facts["sections"]

    async def test_one_entry_per_application(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, (BaseApplication, {"mount": ""}), RichApplication)
        apps = payload(await http_request(server, "/_server/monitor/snapshot"))["apps"]
        assert set(apps) == {"", "rich"}

    async def test_server_app_is_not_a_tab(self, http_request) -> None:
        server = make_server(SERVER_ADMIN)
        apps = payload(await http_request(server, "/_server/monitor/snapshot"))["apps"]
        assert "_server" not in apps

    async def test_identity_facts_by_default(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, (BaseApplication, {"code": "shop", "mount": "shop"}))
        apps = payload(await http_request(server, "/_server/monitor/snapshot"))["apps"]
        assert apps["shop"] == {
            "class": "BaseApplication",
            "code": "shop",
            "mount": "shop",
        }

    async def test_an_app_extends_its_own_entry(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, RichApplication)
        apps = payload(await http_request(server, "/_server/monitor/snapshot"))["apps"]
        assert apps["rich"]["orders"] == 7
        assert apps["rich"]["code"] == "rich"


class TestPanels:
    """The descriptors: who draws what, fetched once."""

    async def test_generic_by_default(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, (BaseApplication, {"code": "shop", "mount": "shop"}))
        panels = payload(await http_request(server, "/_server/monitor/panels"))
        assert panels["shop"] == {"panel": "generic"}

    async def test_a_declared_panel_carries_its_module(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, RichApplication)
        panels = payload(await http_request(server, "/_server/monitor/panels"))
        assert panels["rich"] == {"panel": "orders", "src": "./orders_panel.js"}

    async def test_a_shipped_module_gets_its_src_filled_in(self, http_request) -> None:
        """Declaring ``panel_source`` is enough: the app publishes no route."""
        server = make_server(SERVER_ADMIN, ShippingApplication)
        panels = payload(await http_request(server, "/_server/monitor/panels"))
        assert panels["shipping"] == {
            "panel": "shipping",
            "src": "/_server/monitor/panel?app=shipping",
        }

    async def test_an_explicit_src_wins(self, http_request) -> None:
        """An app free to serve the module itself keeps its own address."""
        server = make_server(SERVER_ADMIN, RichApplication)
        panels = payload(await http_request(server, "/_server/monitor/panels"))
        assert panels["rich"]["src"] == "./orders_panel.js"

    async def test_panels_and_snapshot_agree(self, http_request) -> None:
        server = make_server(SERVER_ADMIN, (BaseApplication, {"mount": ""}), RichApplication)
        panels = payload(await http_request(server, "/_server/monitor/panels"))
        apps = payload(await http_request(server, "/_server/monitor/snapshot"))["apps"]
        assert set(panels) == set(apps)


class TestPanelModule:
    """``panel``: the module a contributor ships, served as an ES module."""

    async def test_the_module_is_served_as_javascript(
        self, response_headers, response_body
    ) -> None:
        server = make_server(SERVER_ADMIN, ShippingApplication)
        sent = await drive(server, "/_server/monitor/panel?app=shipping")
        assert response_headers(sent)[b"content-type"].startswith(b"text/javascript")
        assert b"export default" in response_body(sent)

    async def test_an_app_without_a_module_is_not_found(self, response_status) -> None:
        server = make_server(SERVER_ADMIN, (BaseApplication, {"code": "plain", "mount": "plain"}))
        sent = await drive(server, "/_server/monitor/panel?app=plain")
        assert response_status(sent) == 404

    async def test_an_unknown_app_is_not_found(self, response_status) -> None:
        sent = await drive(make_server(SERVER_ADMIN), "/_server/monitor/panel?app=ghost")
        assert response_status(sent) == 404

    async def test_the_module_is_gated_like_the_rest(self, response_status) -> None:
        server = make_server(None, ShippingApplication)
        sent = await drive(server, "/_server/monitor/panel?app=shipping")
        assert response_status(sent) == 401


class TestTheAdministratorReachesTheMonitor:
    """An administrator declared by the store reaches the monitor (#91: the
    server seeds nobody — the store the configuration names carries him)."""

    async def test_the_declared_admin_carries_the_monitor_tag(self) -> None:
        class AdminStore(MemoryUserStore):
            """The store the configuration names, born with its administrator."""

            def __init__(self, storage: object = None) -> None:
                super().__init__(storage)
                self.save(
                    {
                        "identity": "admin",
                        "password_hash": self.hash_password("opspassword"),
                        "tags": ["SUPERADMIN", "SERVER_ADMIN"],
                        "enabled": True,
                    }
                )

        server = AsgiServer(
            applications=[ServerApplication],
            users={"store_class": AdminStore},
        )
        assert "SERVER_ADMIN" in server.user_store.get("admin")["tags"]
