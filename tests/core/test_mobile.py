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

"""The mobile mixin: iOS universal links and Android app links from two words.

The recipe writes only the real parameters (``ios``, ``android``); the routes
under ``mobile`` build the fixed JSON documents Apple and Google read at
``/.well-known/apple-app-site-association`` and ``/.well-known/assetlinks.json``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.mobile import MobileMixin

AASA = "/.well-known/apple-app-site-association"
ASSETLINKS = "/.well-known/assetlinks.json"
UNIMPLEMENTED = b"unimplemented well-known URL"
FINGERPRINT = "14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5"


class Shop(MobileMixin, RoutedApplication):
    """A routed application with the mobile capability."""


def recipe(mobile_words=None) -> type:
    """A recipe mounting ``Shop`` on the root, with ``mobile_words(shop_node)`` applied."""

    class Recipe(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=0)
            shop = cfg.applications().application(code="shop", mount="", app_class=Shop)
            if mobile_words is not None:
                mobile_words(shop)

    return Recipe


def ios_words(node: Any) -> None:
    node.ios(team_id="ABCDE12345", bundle_id="it.example.shop",
             paths=["/orders/*", "/products/?*"], excluded_paths="/orders/private/*")


def android_words(node: Any) -> None:
    node.android(package_name="it.example.shop", fingerprints=FINGERPRINT,
                 paths="/orders/*", excluded_paths=["/orders/private/*"])


def both_words(node: Any) -> None:
    ios_words(node)
    android_words(node)


async def get_json(server: AsgiServer, path: str, http_request, response_status,
                   response_headers, response_body) -> Any:
    sent = await http_request(server, path)
    assert response_status(sent) == 200
    assert response_headers(sent)[b"content-type"].startswith(b"application/json")
    return json.loads(response_body(sent))


class TestIos:
    async def test_the_association_file_is_built_from_the_words(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        # wf:contract: ios(team_id, bundle_id, paths, excluded_paths) answers the
        # wf:contract: apple-app-site-association JSON: one applinks detail with appIDs
        # wf:contract: "<team_id>.<bundle_id>" and components with the exclusions first
        # wf:contract: ({"/": path, "exclude": true}) then the paths ({"/": path}), copied as
        # wf:contract: written; webcredentials lists the same app id.
        server = AsgiServer(config=recipe(ios_words))
        document = await get_json(server, AASA, http_request, response_status,
                                  response_headers, response_body)
        assert document == {
            "applinks": {"details": [{
                "appIDs": ["ABCDE12345.it.example.shop"],
                "components": [
                    {"/": "/orders/private/*", "exclude": True},
                    {"/": "/orders/*"},
                    {"/": "/products/?*"},
                ],
            }]},
            "webcredentials": {"apps": ["ABCDE12345.it.example.shop"]},
        }

    def test_ios_without_paths_stops_the_boot(self) -> None:
        # wf:contract: ios(...) without paths, or with an empty list, raises ValueError
        # wf:contract: naming paths when the server is built: there is no "/*" default.
        def words(node: Any) -> None:
            node.ios(team_id="ABCDE12345", bundle_id="it.example.shop")

        with pytest.raises(ValueError, match="paths"):
            AsgiServer(config=recipe(words))


class TestAndroid:
    async def test_the_asset_links_are_built_from_the_words(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        # wf:contract: android(package_name, fingerprints, paths, excluded_paths) answers
        # wf:contract: assetlinks.json: one statement with the relations handle_all_urls and
        # wf:contract: get_login_creds, the android_app target, and the paths as
        # wf:contract: relation_extensions -> dynamic_app_link_components, exclusions first.
        server = AsgiServer(config=recipe(android_words))
        document = await get_json(server, ASSETLINKS, http_request, response_status,
                                  response_headers, response_body)
        assert document == [{
            "relation": [
                "delegate_permission/common.handle_all_urls",
                "delegate_permission/common.get_login_creds",
            ],
            "target": {
                "namespace": "android_app",
                "package_name": "it.example.shop",
                "sha256_cert_fingerprints": [FINGERPRINT],
            },
            "relation_extensions": {
                "delegate_permission/common.handle_all_urls": {
                    "dynamic_app_link_components": [
                        {"/": "/orders/private/*", "exclude": True},
                        {"/": "/orders/*"},
                    ],
                },
            },
        }]

    async def test_without_paths_there_are_no_relation_extensions(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        # wf:contract: android(...) without paths and excluded_paths emits the statement
        # wf:contract: with no relation_extensions key.
        def words(node: Any) -> None:
            node.android(package_name="it.example.shop", fingerprints=[FINGERPRINT])

        server = AsgiServer(config=recipe(words))
        document = await get_json(server, ASSETLINKS, http_request, response_status,
                                  response_headers, response_body)
        assert "relation_extensions" not in document[0]
        assert document[0]["target"]["sha256_cert_fingerprints"] == [FINGERPRINT]


class TestPlatformsAreIndependent:
    async def test_both_platforms_answer(
        self, http_request, response_status
    ) -> None:
        # wf:contract: with ios and android both written, both documents answer 200.
        server = AsgiServer(config=recipe(both_words))
        assert response_status(await http_request(server, AASA)) == 200
        assert response_status(await http_request(server, ASSETLINKS)) == 200

    async def test_a_platform_without_words_is_undeclared(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: with only ios written, assetlinks.json answers 404 "unimplemented
        # wf:contract: well-known URL"; with only android written, the association file does.
        ios_only = AsgiServer(config=recipe(ios_words))
        android_only = AsgiServer(config=recipe(android_words))
        missing_android = await http_request(ios_only, ASSETLINKS)
        missing_ios = await http_request(android_only, AASA)
        assert response_status(missing_android) == response_status(missing_ios) == 404
        assert UNIMPLEMENTED in response_body(missing_android)
        assert UNIMPLEMENTED in response_body(missing_ios)

    async def test_no_platform_logs_a_warning_and_declares_nothing(
        self, http_request, response_status, caplog
    ) -> None:
        # wf:contract: an application with the mixin and neither ios nor android written
        # wf:contract: boots, logs a WARNING naming the application code, and declares
        # wf:contract: neither path.
        with caplog.at_level(logging.WARNING):
            server = AsgiServer(config=recipe())
        assert any(record.levelno == logging.WARNING and "shop" in record.getMessage()
                   for record in caplog.records)
        assert response_status(await http_request(server, AASA)) == 404
        assert response_status(await http_request(server, ASSETLINKS)) == 404
