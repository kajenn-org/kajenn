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

"""Well-known URLs declared by applications in the class dict ``well_known``.

A well-known path is ``/.well-known/...``, ``/robots.txt`` or ``/favicon.ico``.
A declared one reaches the route of its application; an undeclared one answers
404 "unimplemented well-known URL", whatever the root application is.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from genro_routes import RoutingClass, route

from kajenn import BaseApplication, BaseServer, RoutedApplication
from kajenn.types import Receive, Scope, Send

UNIMPLEMENTED = b"unimplemented well-known URL"


class RecordingApp(BaseApplication):
    """Answers 200 to everything and remembers every path it was handed."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": f"ok:{scope['path']}".encode()})


class RobotsRecorder(RecordingApp):
    well_known = {"/robots.txt": "seo/robots"}


class ProbeApp(RoutedApplication):
    well_known = {"/.well-known/probe": "probe", "/robots.txt": "robots"}

    @route()
    def probe(self) -> dict[str, str]:
        return {"served": "probe"}

    @route()
    def robots(self) -> str:
        return "User-agent: *\n"


class OtherProbeApp(RoutedApplication):
    well_known = {"/.well-known/probe": "probe"}

    @route()
    def probe(self) -> dict[str, str]:
        return {"served": "other"}


class ProbeMixin:
    well_known = {"/.well-known/probe": "probe", "/.well-known/mixin": "mixin_doc"}


class DerivedApp(ProbeMixin, RoutedApplication):
    well_known = {"/.well-known/probe": "derived_probe"}

    @route()
    def probe(self) -> dict[str, str]:
        return {"served": "mixin-probe"}

    @route()
    def derived_probe(self) -> dict[str, str]:
        return {"served": "derived-probe"}

    @route()
    def mixin_doc(self) -> dict[str, str]:
        return {"served": "mixin-doc"}


class ResourceApp(RoutedApplication):
    well_known = {"/.well-known/oauth-protected-resource/{mount}": "resource"}

    @route()
    def resource(self) -> dict[str, str]:
        return {"resource": self.mount}


class MissingRouteApp(RoutedApplication):
    well_known = {"/.well-known/probe": "absent_route"}


class OldBranch(RoutingClass):
    @route()
    def probe(self) -> dict[str, str]:
        return {"served": "old"}


class OldBranchApp(RoutedApplication):
    """Declares a document the way kajenn 0.7 did: a ``_well_known`` branch."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.route.add_branches({"name": "_well_known", "instance": OldBranch()})


class QuietProbeApp(ProbeApp):
    """Declares the probe on the class and leaves it out when registered."""

    def active_well_known(self) -> dict[str, str]:
        declared = super().active_well_known()
        return {path: target for path, target in declared.items() if path != "/.well-known/probe"}


class TestDeclaredPaths:
    async def test_a_declared_path_reaches_its_route(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: GET /.well-known/probe answers the "probe" route of the application
        # wf:contract: that declared it, whatever its mount.
        server = BaseServer(applications=[ProbeApp(code="site", mount="site")])
        sent = await http_request(server, "/.well-known/probe")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"served": "probe"}

    async def test_robots_txt_is_a_well_known_path(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: /robots.txt declared in well_known reaches its route.
        server = BaseServer(applications=[ProbeApp(code="site", mount="site")])
        sent = await http_request(server, "/robots.txt")
        assert response_status(sent) == 200
        assert response_body(sent) == b"User-agent: *\n"

    async def test_a_plain_application_receives_the_route_path(
        self, http_request, response_status
    ) -> None:
        # wf:contract: an application that is not routed receives the declared route as
        # wf:contract: its path, with a leading slash: "seo/robots" arrives as "/seo/robots".
        app = RobotsRecorder(code="seo", mount="seo")
        server = BaseServer(applications=[app])
        assert response_status(await http_request(server, "/robots.txt")) == 200
        assert app.seen == ["/seo/robots"]

    async def test_the_more_derived_class_wins_and_the_dicts_merge(
        self, http_request, response_body
    ) -> None:
        # wf:contract: well_known dicts merge along the MRO; on the same path the more
        # wf:contract: derived class wins, and the mixin's other paths stay declared.
        server = BaseServer(applications=[DerivedApp(code="site", mount="site")])
        probe = await http_request(server, "/.well-known/probe")
        mixin = await http_request(server, "/.well-known/mixin")
        assert json.loads(response_body(probe)) == {"served": "derived-probe"}
        assert json.loads(response_body(mixin)) == {"served": "mixin-doc"}


class TestUndeclaredPaths:
    @pytest.mark.parametrize("path", ["/.well-known/absent", "/robots.txt", "/favicon.ico"])
    async def test_an_undeclared_well_known_path_answers_404(
        self, http_request, response_status, response_body, path
    ) -> None:
        # wf:contract: a well-known path no application declared answers 404 with the body
        # wf:contract: "unimplemented well-known URL", and the root application never sees it.
        root = RecordingApp(code="root", mount="")
        server = BaseServer(applications=[root])
        sent = await http_request(server, path)
        assert response_status(sent) == 404
        assert UNIMPLEMENTED in response_body(sent)
        assert root.seen == []

    async def test_the_old_branch_declares_nothing(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: a _well_known branch is an ordinary branch: /.well-known/probe is
        # wf:contract: undeclared and answers 404 "unimplemented well-known URL".
        server = BaseServer(applications=[OldBranchApp(code="site", mount="site")])
        sent = await http_request(server, "/.well-known/probe")
        assert response_status(sent) == 404
        assert UNIMPLEMENTED in response_body(sent)

    async def test_other_dotted_segments_stay_404(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: a dotted first segment other than .well-known answers 404 without
        # wf:contract: the well-known message, and reaches no application.
        root = RecordingApp(code="root", mount="")
        server = BaseServer(applications=[root])
        sent = await http_request(server, "/.git/config")
        assert response_status(sent) == 404
        assert UNIMPLEMENTED not in response_body(sent)
        assert root.seen == []

    async def test_ordinary_probes_still_reach_the_root_application(
        self, http_request, response_status
    ) -> None:
        # wf:contract: /sitemap.xml and /apple-touch-icon.png are not well-known: they reach
        # wf:contract: the root application unchanged.
        root = RecordingApp(code="root", mount="")
        server = BaseServer(applications=[root])
        for probe in ("/sitemap.xml", "/apple-touch-icon.png"):
            assert response_status(await http_request(server, probe)) == 200
        assert root.seen == ["/sitemap.xml", "/apple-touch-icon.png"]


class TestMountMarker:
    async def test_the_mount_marker_is_replaced(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: a key with {mount} is declared with the instance's mount in its place.
        server = BaseServer(applications=[ResourceApp(code="api", mount="api")])
        sent = await http_request(server, "/.well-known/oauth-protected-resource/api")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"resource": "api"}

    async def test_the_root_mount_removes_the_segment(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: on the root mount "" the segment "/{mount}" is removed whole.
        server = BaseServer(applications=[ResourceApp(code="api", mount="")])
        sent = await http_request(server, "/.well-known/oauth-protected-resource")
        assert response_status(sent) == 200
        assert json.loads(response_body(sent)) == {"resource": ""}

    async def test_two_mounts_of_one_class_get_one_path_each(
        self, http_request, response_body
    ) -> None:
        # wf:contract: one class mounted twice declares one {mount} path per instance.
        server = BaseServer(applications=[ResourceApp(code="a", mount="a"),
                                          ResourceApp(code="b", mount="b")])
        first = await http_request(server, "/.well-known/oauth-protected-resource/a")
        second = await http_request(server, "/.well-known/oauth-protected-resource/b")
        assert json.loads(response_body(first)) == {"resource": "a"}
        assert json.loads(response_body(second)) == {"resource": "b"}


class TestConstructionErrors:
    def test_a_key_that_is_not_well_known_is_refused(self) -> None:
        # wf:contract: a well_known key outside /.well-known/..., /robots.txt and
        # wf:contract: /favicon.ico raises ValueError naming the key when the app is built.
        class WrongKeyApp(RoutedApplication):
            well_known = {"/sitemap.xml": "sitemap"}

        with pytest.raises(ValueError, match="/sitemap.xml"):
            WrongKeyApp(code="site", mount="site")

    def test_two_applications_declaring_one_path_stop_the_server(self) -> None:
        # wf:contract: two applications declaring the same path raise ValueError naming the
        # wf:contract: path when the server is built.
        with pytest.raises(ValueError, match="/.well-known/probe"):
            BaseServer(applications=[ProbeApp(code="site", mount="site"),
                                     OtherProbeApp(code="other", mount="other")])

    def test_a_route_that_does_not_exist_is_refused(self) -> None:
        # wf:contract: a declared route the routed application does not have raises
        # wf:contract: ValueError naming the route when the server is built.
        with pytest.raises(ValueError, match="absent_route"):
            BaseServer(applications=[MissingRouteApp(code="site", mount="site")])


class TestActiveWellKnown:
    async def test_a_path_left_out_is_not_declared(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: active_well_known() is what the server indexes: a path the
        # wf:contract: application leaves out answers 404 "unimplemented well-known URL".
        server = BaseServer(applications=[QuietProbeApp(code="site", mount="site")])
        sent = await http_request(server, "/.well-known/probe")
        assert response_status(sent) == 404
        assert UNIMPLEMENTED in response_body(sent)

    async def test_a_path_left_out_is_free_for_another_application(
        self, http_request, response_body
    ) -> None:
        # wf:contract: a path one application leaves out of active_well_known() does not
        # wf:contract: conflict with another application declaring it.
        server = BaseServer(applications=[QuietProbeApp(code="site", mount="site"),
                                          OtherProbeApp(code="other", mount="other")])
        sent = await http_request(server, "/.well-known/probe")
        assert json.loads(response_body(sent)) == {"served": "other"}
