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

"""The identifiers each mobile platform must write: an empty one stops the boot.

A document built without them would be served and ignored by Apple or Google
with no error anywhere, so the server refuses to start instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.mobile import MobileMixin

FINGERPRINT = "14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5"


class Shop(MobileMixin, RoutedApplication):
    """A routed application with the mobile capability."""


def recipe(mobile_words) -> type:
    class Recipe(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=0)
            mobile_words(cfg.applications().application(code="shop", mount="", app_class=Shop))

    return Recipe


@pytest.mark.parametrize("missing", ["team_id", "bundle_id"])
def test_ios_without_its_identifiers_stops_the_boot(missing: str) -> None:
    words = {"team_id": "ABCDE12345", "bundle_id": "it.example.shop", "paths": "/orders/*"}
    del words[missing]
    with pytest.raises(ValueError, match=missing):
        AsgiServer(config=recipe(lambda node: node.ios(**words)))


@pytest.mark.parametrize("route", ["apple_app_site_association", "assetlinks"])
async def test_an_unconfigured_platform_route_answers_404(
    http_request, response_status, route: str
) -> None:
    server = AsgiServer(config=recipe(lambda node: None))
    assert response_status(await http_request(server, f"/mobile/{route}")) == 404


@pytest.mark.parametrize("missing", ["package_name", "fingerprints"])
def test_android_without_its_identifiers_stops_the_boot(missing: str) -> None:
    words = {"package_name": "it.example.shop", "fingerprints": FINGERPRINT}
    del words[missing]
    with pytest.raises(ValueError, match=missing):
        AsgiServer(config=recipe(lambda node: node.android(**words)))
