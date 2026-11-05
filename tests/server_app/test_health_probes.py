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

"""The health probes ``/healthz``, ``/livez`` and ``/readyz`` (#105).

They are well-known paths: ``_server`` answers them as defaults from its
``k8s`` section, and an application declaring one of them in ``well_known``
answers it instead. While the server is not RUNNING the entry point refuses
every request with 503 before the demux, the probes included, so a balancer
stops sending traffic before the server closes.
"""

from __future__ import annotations

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.lifespan import QUITTING, STOPPING

PROBES = ("/healthz", "/livez", "/readyz")


class Site(RoutedApplication):
    @route()
    def index(self) -> str:
        return "site"


class ReadySite(RoutedApplication):
    well_known = {"/readyz": "ready"}

    @route(media_type="text/plain")
    def ready(self) -> str:
        return "site ready"


@pytest.mark.parametrize("path", PROBES)
async def test_a_running_server_answers_ok(
    path, http_request, response_status, response_headers, response_body
) -> None:
    server = AsgiServer(applications=[(Site, {"mount": ""})])
    sent = await http_request(server, path)
    assert response_status(sent) == 200
    assert response_headers(sent)[b"content-type"].startswith(b"text/plain")
    assert response_body(sent) == b"ok"


@pytest.mark.parametrize("path", PROBES)
async def test_the_probe_needs_no_identity(path, http_request, response_status) -> None:
    # A probe carries no credentials: the default routes declare no rule.
    server = AsgiServer()
    assert response_status(await http_request(server, path)) == 200


@pytest.mark.parametrize("state", [QUITTING, STOPPING])
@pytest.mark.parametrize("path", PROBES)
async def test_a_server_that_is_leaving_answers_503(
    path, state, http_request, response_status
) -> None:
    server = AsgiServer()
    server.state = state
    assert response_status(await http_request(server, path)) == 503


async def test_an_application_declaring_a_probe_answers_it(
    http_request, response_body
) -> None:
    # _server gives the probes only as defaults: a site that knows when it is
    # ready (its own database, its own warm-up) answers /readyz itself.
    server = AsgiServer(applications=[(ReadySite, {"mount": "site"})])
    assert response_body(await http_request(server, "/readyz")) == b"site ready"
    assert response_body(await http_request(server, "/livez")) == b"ok"


async def test_the_defaults_live_under_the_k8s_section(http_request, response_body) -> None:
    server = AsgiServer()
    for name in ("healthz", "livez", "readyz"):
        assert response_body(await http_request(server, f"/_server/k8s/{name}")) == b"ok"
