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

"""MobileMixin: iOS universal links and Android app links from two words.

An application class that lists ``MobileMixin`` before its base gains two
recipe words, ``ios`` and ``android``, and answers the two association files
Apple and Google read on the site::

    shop = apps.application(code="shop", mount="", app_class=Shop)
    shop.ios(team_id="ABCDE12345", bundle_id="com.example.shop", paths="/orders/*")
    shop.android(package_name="com.example.shop", fingerprints="14:6D:...:E5")

- ``/.well-known/apple-app-site-association`` — from ``ios``: one ``applinks``
  detail for ``<team_id>.<bundle_id>`` with the excluded paths first, then the
  paths, copied as written (Apple's ``*`` and ``?`` syntax); ``paths`` is
  required, there is no ``/*`` default. ``webcredentials`` lists the same app.
- ``/.well-known/assetlinks.json`` — from ``android``: one statement with the
  relations ``handle_all_urls`` and ``get_login_creds`` and the ``android_app``
  target; written paths become ``dynamic_app_link_components``.

Each path is declared only when its word is written (``active_well_known``);
with neither written the application logs a WARNING and declares nothing. The
documents are answered by ``MobileSection``, attached under ``mobile``, built
from the configuration at request time and built once at construction too, so
a missing ``team_id``, ``bundle_id``, ``paths`` (iOS), ``package_name`` or
``fingerprints`` (Android) stops the boot.

Not exported from ``kajenn``: ``from kajenn.mobile import MobileMixin``.
"""

from __future__ import annotations

import logging
from typing import Any

from genro_builders.builder import element
from genro_routes import RoutingClass, route

from kajenn.exceptions import HTTPNotFound
from kajenn.well_known import as_list

__all__ = ["MobileMixin", "MobileMixinGrammar", "MobileSection"]

AASA_PATH = "/.well-known/apple-app-site-association"
ASSETLINKS_PATH = "/.well-known/assetlinks.json"

PLATFORM_WORDS = {
    "ios": ("team_id", "bundle_id", "paths", "excluded_paths"),
    "android": ("package_name", "fingerprints", "paths", "excluded_paths"),
}
"""The attributes each platform word can write."""

REQUIRED_WORDS = {
    "ios": ("team_id", "bundle_id", "paths"),
    "android": ("package_name", "fingerprints"),
}
"""The attributes each platform word must write: an empty one stops the boot."""


class MobileMixinGrammar:
    """The words ``MobileMixin`` brings into a recipe: one per platform.

    Every list parameter takes a string or a list.
    """

    @element(sub_tags="", node_label="ios")
    def ios(
        self,
        team_id: str = None,
        bundle_id: str = None,
        paths: str | list = None,
        excluded_paths: str | list = None,
    ) -> None:
        """The iOS app whose universal links this site answers; ``team_id``,
        ``bundle_id`` and ``paths`` (at least one) are required."""

    @element(sub_tags="", node_label="android")
    def android(
        self,
        package_name: str = None,
        fingerprints: str | list = None,
        paths: str | list = None,
        excluded_paths: str | list = None,
    ) -> None:
        """The Android app whose app links this site answers: its package and
        the SHA-256 fingerprints of its signing certificates, both required."""


def link_components(paths: list, excluded_paths: list) -> list[dict[str, Any]]:
    """The exclusions first, then the paths, in the shape both platforms read."""
    excluded = [{"/": path, "exclude": True} for path in excluded_paths]
    return excluded + [{"/": path} for path in paths]


class MobileSection(RoutingClass):
    """The ``mobile`` routes: the two association documents.

    Note:
        Bound to its application (dual relationship: ``self.application``);
        the words are read through ``self.application.config``.
    """

    def __init__(self, application: Any) -> None:
        """Bind the section to its application (dual relationship)."""
        self.application = application

    def words(self, platform: str, *names: str) -> dict[str, Any] | None:
        """The written attributes of ``platform``'s word, ``None`` when it is not written."""
        application = self.application
        handler = getattr(application.server, "config", None)
        path = f"applications.{application.code}.{platform}"
        if handler is None or handler.node(path) is None:
            return None
        return handler.closed_attrs(path, *names)

    def required_words(self, platform: str) -> dict[str, Any]:
        """``platform``'s written attributes (``PLATFORM_WORDS``), each of
        ``REQUIRED_WORDS[platform]`` present.

        Raises ``HTTPNotFound`` when the word is not written, ``ValueError``
        naming the first required attribute left empty.
        """
        words = self.words(platform, *PLATFORM_WORDS[platform])
        if words is None:
            raise HTTPNotFound(f"{platform} is not configured")
        for name in REQUIRED_WORDS[platform]:
            if not as_list(words.get(name)):
                raise ValueError(
                    f"application {self.application.code!r}: {platform} {name} is required"
                )
        return words

    @route(media_type="application/json")
    def apple_app_site_association(self) -> dict[str, Any]:
        """The iOS association file. Route: GET /.well-known/apple-app-site-association

        Raises ``ValueError`` naming the attribute when ``team_id``,
        ``bundle_id`` or ``paths`` is missing or empty.
        """
        words = self.required_words("ios")
        app_id = f"{words['team_id']}.{words['bundle_id']}"
        components = link_components(
            as_list(words.get("paths")), as_list(words.get("excluded_paths"))
        )
        return {
            "applinks": {"details": [{"appIDs": [app_id], "components": components}]},
            "webcredentials": {"apps": [app_id]},
        }

    @route(media_type="application/json")
    def assetlinks(self) -> list[dict[str, Any]]:
        """The Android asset links. Route: GET /.well-known/assetlinks.json

        Raises ``ValueError`` naming the attribute when ``package_name`` or
        ``fingerprints`` is missing or empty.
        """
        words = self.required_words("android")
        statement: dict[str, Any] = {
            "relation": [
                "delegate_permission/common.handle_all_urls",
                "delegate_permission/common.get_login_creds",
            ],
            "target": {
                "namespace": "android_app",
                "package_name": words.get("package_name"),
                "sha256_cert_fingerprints": as_list(words.get("fingerprints")),
            },
        }
        paths = as_list(words.get("paths"))
        excluded_paths = as_list(words.get("excluded_paths"))
        if paths or excluded_paths:
            statement["relation_extensions"] = {
                "delegate_permission/common.handle_all_urls": {
                    "dynamic_app_link_components": link_components(paths, excluded_paths),
                },
            }
        return [statement]


class MobileMixin:
    """The mobile capability of an application: iOS and Android association files.

    Cooperative: listed before the application base, it forwards its kwargs to
    ``super().__init__`` and attaches ``MobileSection`` under ``mobile``.
    """

    grammar = MobileMixinGrammar
    well_known = {
        AASA_PATH: "mobile/apple_app_site_association",
        ASSETLINKS_PATH: "mobile/assetlinks",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mobile_section = MobileSection(self)
        self.route.add_branches({"name": "mobile", "instance": self.mobile_section})

    def active_well_known(self) -> dict[str, str]:
        """The association files whose platform word is written.

        Each configured document is built here, so a missing required attribute
        stops the server at construction. With neither word written, a WARNING names
        the application and neither path is declared.
        """
        active = super().active_well_known()
        section = self.mobile_section
        if section.words("ios") is None:
            active.pop(AASA_PATH)
        else:
            section.apple_app_site_association()
        if section.words("android") is None:
            active.pop(ASSETLINKS_PATH)
        else:
            section.assetlinks()
        if AASA_PATH not in active and ASSETLINKS_PATH not in active:
            logging.getLogger(__name__).warning(
                "application %r has MobileMixin but neither ios nor android is configured",
                self.code,
            )
        return active
