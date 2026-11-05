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

"""Health probes: ``/healthz``, ``/livez`` and ``/readyz`` on every server.

The server answers them itself, ahead of the applications, of the middleware
chain and of the refusal of a server that is leaving. Liveness stays 200 while
the server leaves; readiness turns 503 and names why.
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn import AsgiConfigBuilder, AsgiServer, BaseApplication, BaseServer
from kajenn.lifespan import STOPPING
from kajenn.probes import DEFAULT_PROBES, probe_paths
from kajenn.remote_application import RemoteApplication
from kajenn.types import Receive, Scope, Send
from tests.core.test_external_application import request, running
from tests.core.test_well_known_external import write_recipe


class Root(BaseApplication):
    """Root app: answers ``root`` on every path, counting its calls."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"root"})


class NotReady(Root):
    """An application that is not ready."""

    @property
    def ready(self) -> bool:
        return False


def base_server(**kwargs: Any) -> tuple[BaseServer, Root]:
    root = Root(code="root", mount="")
    return BaseServer(applications=[root], **kwargs), root


@pytest.mark.parametrize("path", ["/healthz", "/livez"])
async def test_liveness_answers_ok(path, http_request, response_status, response_body,
                                   response_headers) -> None:
    server, root = base_server()
    sent = await http_request(server, path)
    assert response_status(sent) == 200
    assert response_body(sent) == b"ok"
    assert response_headers(sent)[b"cache-control"] == b"no-store"
    assert root.calls == 0


async def test_readiness_answers_ready(http_request, response_status, response_body) -> None:
    server, root = base_server()
    sent = await http_request(server, "/readyz")
    assert (response_status(sent), response_body(sent)) == (200, b"ready")
    assert root.calls == 0


async def test_head_has_no_body(http_request, response_status, response_body) -> None:
    server, _ = base_server()
    sent = await http_request(server, "/readyz", method="HEAD")
    assert (response_status(sent), response_body(sent)) == (200, b"")


async def test_other_methods_are_405(http_request, response_status, response_headers) -> None:
    server, _ = base_server()
    sent = await http_request(server, "/healthz", method="POST")
    assert response_status(sent) == 405
    assert response_headers(sent)[b"allow"] == b"GET, HEAD"


async def test_a_leaving_server_is_alive_and_not_ready(http_request, response_status,
                                                       response_body) -> None:
    server, _ = base_server()
    server.state = STOPPING
    assert response_status(await http_request(server, "/healthz")) == 200
    sent = await http_request(server, "/readyz")
    assert response_status(sent) == 503
    assert response_body(sent) == b"not ready\nserver stopping"
    assert response_status(await http_request(server, "/other")) == 503


async def test_an_application_not_ready_is_named(http_request, response_status,
                                                 response_body) -> None:
    server = BaseServer(applications=[Root(code="root", mount=""),
                                      NotReady(code="shop", mount="shop")])
    sent = await http_request(server, "/readyz")
    assert response_status(sent) == 503
    assert response_body(sent) == b"not ready\napplication shop"
    assert server.readiness() == ["application shop"]


async def test_probes_are_not_registered_requests(http_request) -> None:
    server, _ = base_server()
    registered: list[Any] = []
    original = server.requests.register
    server.requests.register = lambda scope: registered.append(scope) or original(scope)
    await http_request(server, "/healthz")
    assert registered == []


async def test_only_exact_paths_are_probes(http_request, response_body) -> None:
    server, root = base_server()
    assert response_body(await http_request(server, "/healthz/x")) == b"root"
    assert root.calls == 1


async def test_probes_can_be_turned_off(http_request, response_body) -> None:
    server, root = base_server(probes=False)
    assert server.probe_paths == {}
    assert response_body(await http_request(server, "/healthz")) == b"root"


async def test_probes_can_be_renamed(http_request, response_body) -> None:
    server, _ = base_server(probes={"liveness": "/_live", "readiness": ["/_ready"]})
    assert server.probe_paths == {"/_live": "liveness", "/_ready": "readiness"}
    assert response_body(await http_request(server, "/_live")) == b"ok"
    assert response_body(await http_request(server, "/healthz")) == b"root"


def test_a_kind_left_out_keeps_its_default() -> None:
    assert probe_paths({"readiness": None}) == {path: "liveness"
                                               for path in DEFAULT_PROBES["liveness"]}


@pytest.mark.parametrize("declared, message", [
    ({"startup": "/s"}, "unknown kind"),
    ({"liveness": "healthz"}, "does not start with"),
    ({"liveness": "/robots.txt"}, "well-known"),
    ({"liveness": "/x", "readiness": "/x"}, "given twice"),
    ("yes", "expected a dict"),
])
def test_bad_declarations_are_refused(declared, message) -> None:
    with pytest.raises(ValueError, match=message):
        probe_paths(declared)


class TestAsgiServer:
    """The shipped composition: the probe skips the chain, sessions and auth."""

    def server(self, **kwargs: Any) -> AsgiServer:
        return AsgiServer(applications=[(Root, {"mount": ""})], **kwargs)

    async def test_liveness_sets_no_cookie(self, http_request, response_status,
                                           response_headers) -> None:
        sent = await http_request(self.server(), "/healthz")
        assert response_status(sent) == 200
        assert b"set-cookie" not in response_headers(sent)

    async def test_readiness_includes_the_server_application(self, http_request,
                                                             response_status) -> None:
        server = self.server()
        assert "_server" in server.applications
        assert response_status(await http_request(server, "/readyz")) == 200

    async def test_the_kwarg_reaches_the_base(self, http_request, response_body) -> None:
        server = self.server(probes=False)
        assert server.probe_paths == {}
        assert response_body(await http_request(server, "/healthz")) == b"root"

    def test_the_recipe_word_renames(self) -> None:
        class ProbesConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server().probes(liveness="/alive,/livez", readiness="")
                cfg.applications().application(code="root", mount="", app_class=Root)

        assert AsgiServer(config=ProbesConfig).probe_paths == {
            "/alive": "liveness", "/livez": "liveness",
        }

    def test_the_recipe_word_turns_off(self) -> None:
        class NoProbesConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server().probes(enabled=False)
                cfg.applications().application(code="root", mount="", app_class=Root)

        assert AsgiServer(config=NoProbesConfig).probe_paths == {}


def test_a_remote_application_is_ready_once_joined() -> None:
    server = AsgiServer(applications=[(Root, {"code": "ext", "spawner": "subprocess"})])
    remote = server.applications["ext"]
    assert isinstance(remote, RemoteApplication)
    assert not remote.ready
    assert server.readiness() == ["application ext"]
    server.kbus_pids["ext"] = 1234
    assert remote.ready
    assert server.readiness() == []


async def test_readiness_turns_200_when_the_process_joined(tmp_path) -> None:
    async with running(write_recipe(tmp_path)) as server:
        status, body = await request(server, "/readyz")
    assert (status, body) == (200, b"ready")
