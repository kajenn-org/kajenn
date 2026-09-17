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

"""Hidden paths and the ``.well-known`` exception (issue #88).

A path whose first segment starts with a dot is hidden or of service: the
SERVER answers 404 in its own demux — always, with no middleware and no
switch — and no application is touched. The one exception is
``/.well-known/<name>``, when ``<name>`` is a child of the ``_well_known``
branch of a routed application the server mounts: the request then reaches
that application as ``_well_known/<name>/<rest>``, with its own
authorization rules.

A probe path without a dot (``/favicon.ico``, ``/robots.txt``) is an
ordinary path: the server silences nothing, and the application it is
demuxed to answers it.
"""

from __future__ import annotations

import json
from typing import Any

from genro_routes import RoutingClass, route

from kajenn import (
    AsgiServer,
    Avatar,
    BaseApplication,
    BaseMiddleware,
    BaseServer,
    RoutedApplication,
)
from kajenn.well_known import WELL_KNOWN_ROOT
from kajenn.types import Receive, Scope, Send


class RecordingApp(BaseApplication):
    """Answers 200 to everything and remembers every path it was handed."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.seen.append(scope["path"])
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": f"ok:{scope['path']}".encode()})


class AcmeDiscovery(RoutingClass):
    """A sub-branch of ``_well_known``: a served name with a path under it."""

    @route()
    def detail(self) -> dict[str, str]:
        return {"served": "acme-detail"}


class Discovery(RoutingClass):
    """The ``_well_known`` branch of ``DiscoveryApp``."""

    @route()
    def probe(self) -> dict[str, str]:
        return {"served": "probe"}

    @route(auth_rule="admin")
    def guarded(self) -> dict[str, str]:
        return {"served": "guarded"}


class DiscoveryApp(RoutedApplication):
    """Routed application declaring three discovery names."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        branch = Discovery()
        branch.route.add_branches({"name": "acme", "instance": AcmeDiscovery()})
        self.route.add_branches({"name": WELL_KNOWN_ROOT, "instance": branch})

    @route()
    def index(self) -> dict[str, str]:
        return {"served": "index"}


class OtherDiscovery(RoutingClass):
    """A second ``_well_known`` branch claiming the same ``probe`` name."""

    @route()
    def probe(self) -> dict[str, str]:
        return {"served": "other-probe"}


class OtherApp(RoutedApplication):
    """Routed application claiming a name ``DiscoveryApp`` also declares."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.add_branches({"name": WELL_KNOWN_ROOT, "instance": OtherDiscovery()})


class StampAuthMiddleware(BaseMiddleware):
    """Test middleware stamping a fixed identity on ``scope["auth"]`` (order 500)."""

    middleware_order = 500

    def __init__(self, app: Any, server: Any, *, avatar: Avatar | None = None, **options: Any):
        self._avatar = avatar
        super().__init__(app, server, **options)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope["auth"] = self._avatar
        await self.app(scope, receive, send)


def auth_server(entry: tuple[type, dict], avatar: Avatar | None) -> AsgiServer:
    """An ``AsgiServer`` whose chain stamps ``avatar`` as the request identity."""
    return AsgiServer(
        applications=[entry],
        middleware={"stamp": {"avatar": avatar}},
        middleware_registry={"stamp": StampAuthMiddleware},
    )


class TestHiddenPaths:
    """A dotted first segment is hidden: 404, and no application sees it."""

    async def test_dotted_first_segment_answers_404(self, http_request, response_status) -> None:
        server = BaseServer(applications=[RecordingApp(mount="")])
        assert response_status(await http_request(server, "/.git/config")) == 404

    async def test_the_hidden_path_never_reaches_the_application(self, http_request) -> None:
        app = RecordingApp(mount="")
        server = BaseServer(applications=[app])
        await http_request(server, "/.env")
        assert app.seen == []

    async def test_the_default_redirect_is_not_taken_for_a_hidden_path(
        self, http_request, response_status
    ) -> None:
        app = RecordingApp(code="api", mount="api")
        server = BaseServer(applications=[app], default="api")
        assert response_status(await http_request(server, "/.well-known/nothing")) == 404
        assert app.seen == []

    async def test_the_bare_server_applies_the_rule_with_no_middleware_at_all(
        self, http_request, response_status
    ) -> None:
        # A ``BaseServer`` has no middleware chain: the demux answers by itself.
        server = BaseServer(applications=[RecordingApp(mount="")])
        assert response_status(await http_request(server, "/.svn/entries")) == 404

    async def test_the_rule_has_no_switch(self, http_request, response_status) -> None:
        # The assembled server, whose chain is complete, answers the same.
        server = AsgiServer(applications=[(RecordingApp, {"mount": ""})])
        app = server.applications["recordingapp"]
        assert response_status(await http_request(server, "/.git/config")) == 404
        assert app.seen == []

    async def test_the_probes_without_a_dot_reach_the_application(
        self, http_request, response_status, response_body
    ) -> None:
        app = RecordingApp(mount="")
        server = BaseServer(applications=[app])
        for probe in ("/favicon.ico", "/robots.txt", "/sitemap.xml", "/apple-touch-icon.png"):
            sent = await http_request(server, probe)
            assert response_status(sent) == 200
            assert response_body(sent) == f"ok:{probe}".encode()
        assert app.seen == ["/favicon.ico", "/robots.txt", "/sitemap.xml", "/apple-touch-icon.png"]

    async def test_an_ordinary_path_still_reaches_the_application(
        self, http_request, response_status, response_body
    ) -> None:
        server = BaseServer(applications=[RecordingApp(mount="")])
        sent = await http_request(server, "/index")
        assert response_status(sent) == 200
        assert response_body(sent) == b"ok:/index"


class TestWellKnownDiscovery:
    """The served names are read from the applications at mount time."""

    def test_names_are_discovered_at_mount(self) -> None:
        app = DiscoveryApp(code="site", mount="")
        server = BaseServer(applications=[app])
        assert server.well_known_applications == {
            "probe": app,
            "guarded": app,
            "acme": app,
        }

    def test_an_application_without_the_branch_declares_nothing(self) -> None:
        server = BaseServer(applications=[RecordingApp(mount="")])
        assert server.well_known_applications == {}

    def test_the_last_declared_application_wins(self) -> None:
        first = DiscoveryApp(code="site", mount="")
        second = OtherApp(code="other", mount="other")
        server = BaseServer(applications=[first, second])
        assert server.well_known_applications["probe"] is second
        assert server.well_known_applications["acme"] is first


class TestWellKnownRequests:
    """A served name reaches its own application under the ``_well_known`` branch."""

    async def test_the_served_name_reaches_the_owning_application(
        self, http_request, response_status, response_body
    ) -> None:
        server = BaseServer(applications=[DiscoveryApp(code="site", mount="site")])
        sent = await http_request(server, "/.well-known/probe")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"served": "probe"}

    async def test_the_rest_of_the_path_resolves_under_the_branch(
        self, http_request, response_status, response_body
    ) -> None:
        server = BaseServer(applications=[DiscoveryApp(code="site", mount="site")])
        sent = await http_request(server, "/.well-known/acme/detail")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"served": "acme-detail"}

    async def test_an_unserved_name_answers_404(self, http_request, response_status) -> None:
        server = BaseServer(applications=[DiscoveryApp(code="site", mount="")])
        assert response_status(await http_request(server, "/.well-known/absent")) == 404

    async def test_the_last_declared_application_answers_the_shared_name(
        self, http_request, response_body
    ) -> None:
        server = BaseServer(
            applications=[
                DiscoveryApp(code="site", mount="site"),
                OtherApp(code="other", mount="other"),
            ]
        )
        sent = await http_request(server, "/.well-known/probe")
        assert json.loads(response_body(sent)) == {"served": "other-probe"}

    async def test_the_owning_application_authorization_rules_apply(
        self, http_request, response_status, response_body
    ) -> None:
        anonymous = auth_server((DiscoveryApp, {"code": "site", "mount": "site"}), avatar=None)
        assert response_status(await http_request(anonymous, "/.well-known/guarded")) == 401
        viewer = auth_server((DiscoveryApp, {"code": "site", "mount": "site"}), Avatar("bob", ["viewer"]))
        assert response_status(await http_request(viewer, "/.well-known/guarded")) == 403
        admin = auth_server((DiscoveryApp, {"code": "site", "mount": "site"}), Avatar("alice", ["admin"]))
        sent = await http_request(admin, "/.well-known/guarded")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"served": "guarded"}
