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

"""The route a well-known path forwards to: its own auth rules, its spelling.

kajenn does not check that a well-known route is public: the route's own auth
rules answer the request, 401 or 403 like on its mount. A route is written
without a leading slash, because the demux forwards ``"/" + route`` as written.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, Avatar, BaseMiddleware, BaseServer, RoutedApplication


class GuardedApp(RoutedApplication):
    well_known = {"/.well-known/guarded": "guarded"}

    @route(auth_rule="admin")
    def guarded(self) -> dict[str, str]:
        return {"served": "guarded"}


class StampAuthMiddleware(BaseMiddleware):
    """Test middleware stamping a fixed identity on ``scope["auth"]`` (order 500)."""

    middleware_order = 500

    def __init__(self, app: Any, server: Any, *, avatar: Avatar | None = None, **options: Any):
        self._avatar = avatar
        super().__init__(app, server, **options)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope["auth"] = self._avatar
        await self.app(scope, receive, send)


def auth_server(avatar: Avatar | None) -> AsgiServer:
    """An ``AsgiServer`` mounting ``GuardedApp`` whose chain stamps ``avatar``."""
    return AsgiServer(
        applications=[(GuardedApp, {"code": "site", "mount": "site"})],
        middleware={"stamp": {"avatar": avatar}},
        middleware_registry={"stamp": StampAuthMiddleware},
    )


async def test_the_route_auth_rules_answer_a_well_known_request(
    http_request, response_status, response_body
) -> None:
    anonymous = auth_server(None)
    assert response_status(await http_request(anonymous, "/.well-known/guarded")) == 401
    viewer = auth_server(Avatar("bob", ["viewer"]))
    assert response_status(await http_request(viewer, "/.well-known/guarded")) == 403
    admin = auth_server(Avatar("alice", ["admin"]))
    sent = await http_request(admin, "/.well-known/guarded")
    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {"served": "guarded"}


def test_a_route_with_a_leading_slash_is_refused() -> None:
    class SlashApp(RoutedApplication):
        well_known = {"/robots.txt": "/robots"}

        @route()
        def robots(self) -> str:
            return "User-agent: *\n"

    with pytest.raises(ValueError, match="/robots"):
        BaseServer(applications=[SlashApp(code="site", mount="site")])
