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

"""The routes of the ``_server`` documents on their own mount, when not configured.

``/_server/security_txt``, ``/_server/robots_txt`` and ``/_server/favicon_ico``
are the routes the well-known paths forward to. Reached directly on a server
that configures none of them, they answer 404 like the well-known paths do.
"""

from __future__ import annotations

import pytest

from kajenn import AsgiServer


@pytest.mark.parametrize("path", ["/_server/security_txt", "/_server/robots_txt",
                                  "/_server/favicon_ico"])
async def test_an_unconfigured_document_route_answers_404(
    http_request, response_status, path
) -> None:
    assert response_status(await http_request(AsgiServer(), path)) == 404
