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

"""Default well-known paths: ``default_well_known`` answers only what nobody declares.

A path declared in ``well_known`` answers whatever the order of registration;
a default answers a path no application declares. Two applications giving the
same default stop the server, as two declaring the same path do.
"""

from __future__ import annotations

import json

import pytest
from genro_routes import route

from kajenn import BaseServer, RoutedApplication


class DefaultRobots(RoutedApplication):
    default_well_known = {"/robots.txt": "robots"}

    @route()
    def robots(self) -> str:
        return "default"


class DeclaredRobots(RoutedApplication):
    well_known = {"/robots.txt": "robots"}

    @route()
    def robots(self) -> str:
        return "declared"


class ResourceDefault(RoutedApplication):
    default_well_known = {"/.well-known/oauth-protected-resource/{mount}": "resource"}

    @route()
    def resource(self) -> dict[str, str]:
        return {"resource": self.mount}


async def test_a_default_answers_an_undeclared_path(http_request, response_body) -> None:
    server = BaseServer(applications=[DefaultRobots(code="fallback", mount="fallback")])
    assert response_body(await http_request(server, "/robots.txt")) == b"default"


@pytest.mark.parametrize("order", ["default_first", "declared_first"])
async def test_a_declared_path_wins_in_any_order(http_request, response_body, order: str) -> None:
    applications = [DefaultRobots(code="fallback", mount="fallback"),
                    DeclaredRobots(code="site", mount="site")]
    if order == "declared_first":
        applications.reverse()
    server = BaseServer(applications=applications)
    assert response_body(await http_request(server, "/robots.txt")) == b"declared"


def test_two_applications_giving_one_default_stop_the_server() -> None:
    with pytest.raises(ValueError, match="/robots.txt"):
        BaseServer(applications=[DefaultRobots(code="a", mount="a"),
                                 DefaultRobots(code="b", mount="b")])


async def test_a_default_resolves_the_mount_marker(http_request, response_body) -> None:
    server = BaseServer(applications=[ResourceDefault(code="api", mount="api")])
    sent = await http_request(server, "/.well-known/oauth-protected-resource/api")
    assert json.loads(response_body(sent)) == {"resource": "api"}


def test_a_default_route_must_exist() -> None:
    class MissingDefault(RoutedApplication):
        default_well_known = {"/robots.txt": "absent_route"}

    with pytest.raises(ValueError, match="absent_route"):
        BaseServer(applications=[MissingDefault(code="site", mount="site")])
