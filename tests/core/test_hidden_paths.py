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

"""Hidden paths (issue #88).

A path whose first segment starts with a dot is hidden or of service: the
SERVER answers 404 in its own demux — always, with no middleware and no
switch — and no application is touched. The well-known paths
(``/.well-known/...``, ``/robots.txt``, ``/favicon.ico``) have their own rule,
covered in ``test_well_known.py``.

A probe path that is not well-known (``/sitemap.xml``,
``/apple-touch-icon.png``) is an ordinary path: the server silences nothing,
and the application it is demuxed to answers it.
"""

from __future__ import annotations

from typing import Any

from kajenn import AsgiServer, BaseApplication, BaseServer
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
        assert response_status(await http_request(server, "/.env")) == 404
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

    async def test_the_probes_that_are_not_well_known_reach_the_application(
        self, http_request, response_status, response_body
    ) -> None:
        app = RecordingApp(mount="")
        server = BaseServer(applications=[app])
        for probe in ("/sitemap.xml", "/apple-touch-icon.png"):
            sent = await http_request(server, probe)
            assert response_status(sent) == 200
            assert response_body(sent) == f"ok:{probe}".encode()
        assert app.seen == ["/sitemap.xml", "/apple-touch-icon.png"]

    async def test_an_ordinary_path_still_reaches_the_application(
        self, http_request, response_status, response_body
    ) -> None:
        server = BaseServer(applications=[RecordingApp(mount="")])
        sent = await http_request(server, "/index")
        assert response_status(sent) == 200
        assert response_body(sent) == b"ok:/index"
