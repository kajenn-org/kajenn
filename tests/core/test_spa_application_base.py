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

"""Contract: an application extending SpaApplicationBase is served like a RoutedApplication."""

from __future__ import annotations

from genro_routes import route

from kajenn import AsgiServer
from kajenn.spa_application import SpaApplicationBase


class GreetingSpa(SpaApplicationBase):
    """An SPA application in one process with one route."""

    @route()
    def hello(self) -> dict[str, str]:
        return {"hello": "spa"}


async def test_spa_application_answers_its_route(
    http_request, response_status, response_body
) -> None:
    server = AsgiServer(applications=[(GreetingSpa, {"mount": ""})])
    sent = await http_request(server, "/hello")
    assert response_status(sent) == 200
    assert response_body(sent) == b'{"hello":"spa"}'
