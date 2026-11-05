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

"""The well-known documents of ``_server``: security.txt, robots.txt, favicon.ico.

``security.txt`` and ``robots.txt`` are generated from words of the server
application's grammar and validated at boot; ``favicon.ico`` is the file
``static/favicon.ico`` of the site home. Each path is declared only when its
words are written or its file exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder

UNIMPLEMENTED = b"unimplemented well-known URL"
ICON = b"\x00\x00\x01\x00fake-icon"


class Site(RoutedApplication):
    @route()
    def index(self) -> str:
        return "site"


class IconSite(RoutedApplication):
    well_known = {"/favicon.ico": "icon"}

    @route()
    def icon(self) -> str:
        return "site-icon"


def recipe(server_words=None, site_class: type = Site) -> type:
    """A recipe with one site application and ``server_words(server_app_node)`` applied."""

    class Recipe(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=0)
            apps = cfg.applications()
            apps.application(code="site", app_class=site_class)
            if server_words is not None:
                server_words(apps.application(code="_server"))

    return Recipe


def security_words(node: Any) -> None:
    node.security_txt(
        contact=["mailto:security@example.com", "https://example.com/security"],
        expires="2027-06-30T00:00:00Z",
        preferred_languages=["en", "it"],
        policy="https://example.com/policy",
    )


def robots_words(node: Any) -> None:
    robots = node.robots(sitemaps="https://example.com/sitemap.xml")
    robots.agent(user_agent="*", disallow=["/admin/", "/private/"])
    robots.agent(user_agent="Googlebot", allow="/", disallow="/drafts/")


class TestSecurityTxt:
    async def test_the_words_generate_the_document(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        # wf:contract: security_txt(...) under _server answers /.well-known/security.txt as
        # wf:contract: text/plain, one "Field: value" line per value (a list gives one line
        # wf:contract: per item, preferred_languages is joined with ", "), ending in "\n".
        server = AsgiServer(config=recipe(security_words))
        sent = await http_request(server, "/.well-known/security.txt")
        assert response_status(sent) == 200
        assert response_headers(sent)[b"content-type"].startswith(b"text/plain")
        body = response_body(sent).decode()
        assert body.endswith("\n")
        assert set(body.splitlines()) == {
            "Contact: mailto:security@example.com",
            "Contact: https://example.com/security",
            "Expires: 2027-06-30T00:00:00Z",
            "Preferred-Languages: en, it",
            "Policy: https://example.com/policy",
        }

    def test_an_expires_that_is_not_iso_8601_stops_the_boot(self) -> None:
        # wf:contract: an expires value that is not an ISO 8601 date-time raises ValueError
        # wf:contract: naming expires when the server is built.
        def words(node: Any) -> None:
            node.security_txt(contact="mailto:a@example.com", expires="next year")

        with pytest.raises(ValueError, match="expires"):
            AsgiServer(config=recipe(words))

    def test_a_missing_contact_stops_the_boot(self) -> None:
        # wf:contract: security_txt without contact raises ValueError naming contact when
        # wf:contract: the server is built.
        def words(node: Any) -> None:
            node.security_txt(expires="2027-06-30T00:00:00Z")

        with pytest.raises(ValueError, match="contact"):
            AsgiServer(config=recipe(words))

    async def test_without_the_words_the_path_is_undeclared(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: with no security_txt word, /.well-known/security.txt answers 404
        # wf:contract: "unimplemented well-known URL".
        sent = await http_request(AsgiServer(config=recipe()), "/.well-known/security.txt")
        assert response_status(sent) == 404
        assert UNIMPLEMENTED in response_body(sent)


class TestRobotsTxt:
    async def test_the_words_generate_the_document(
        self, http_request, response_status, response_headers, response_body
    ) -> None:
        # wf:contract: robots(sitemaps=...) with agent(...) children answers /robots.txt as
        # wf:contract: text/plain: one group per agent in declaration order, Allow lines
        # wf:contract: before Disallow lines, groups separated by a blank line, Sitemap lines last.
        server = AsgiServer(config=recipe(robots_words))
        sent = await http_request(server, "/robots.txt")
        assert response_status(sent) == 200
        assert response_headers(sent)[b"content-type"].startswith(b"text/plain")
        assert response_body(sent).decode() == (
            "User-agent: *\n"
            "Disallow: /admin/\n"
            "Disallow: /private/\n"
            "\n"
            "User-agent: Googlebot\n"
            "Allow: /\n"
            "Disallow: /drafts/\n"
            "\n"
            "Sitemap: https://example.com/sitemap.xml\n"
        )

    def test_robots_without_an_agent_stops_the_boot(self) -> None:
        # wf:contract: robots() with no agent child raises ValueError naming agent.
        def words(node: Any) -> None:
            node.robots()

        with pytest.raises(ValueError, match="agent"):
            AsgiServer(config=recipe(words))

    def test_a_rule_without_a_leading_slash_or_star_stops_the_boot(self) -> None:
        # wf:contract: an allow or disallow rule not starting with "/" or "*" raises
        # wf:contract: ValueError naming the rule.
        def words(node: Any) -> None:
            node.robots().agent(user_agent="*", disallow="admin/")

        with pytest.raises(ValueError, match="admin/"):
            AsgiServer(config=recipe(words))

    def test_a_relative_sitemap_stops_the_boot(self) -> None:
        # wf:contract: a sitemap that is not an absolute URL raises ValueError naming it.
        def words(node: Any) -> None:
            node.robots(sitemaps="/sitemap.xml").agent(user_agent="*", disallow="/x/")

        with pytest.raises(ValueError, match="/sitemap.xml"):
            AsgiServer(config=recipe(words))

    async def test_without_the_words_the_path_is_undeclared(
        self, http_request, response_status, response_body
    ) -> None:
        # wf:contract: with no robots word, /robots.txt answers 404 "unimplemented
        # wf:contract: well-known URL", and the site application never answers it.
        sent = await http_request(AsgiServer(config=recipe()), "/robots.txt")
        assert response_status(sent) == 404
        assert UNIMPLEMENTED in response_body(sent)


class TestFavicon:
    def home_with_icon(self, tmp_path: Path) -> str:
        static = tmp_path / "home" / "static"
        static.mkdir(parents=True)
        (static / "favicon.ico").write_bytes(ICON)
        return str(tmp_path / "home")

    async def test_the_static_file_is_served(
        self, http_request, response_status, response_headers, response_body, tmp_path
    ) -> None:
        # wf:contract: with static/favicon.ico in the site home, /favicon.ico answers its
        # wf:contract: bytes as image/x-icon.
        server = AsgiServer(config=recipe(), site_home=self.home_with_icon(tmp_path))
        sent = await http_request(server, "/favicon.ico")
        assert response_status(sent) == 200
        assert response_headers(sent)[b"content-type"] == b"image/x-icon"
        assert response_body(sent) == ICON

    async def test_without_the_file_the_path_is_undeclared(
        self, http_request, response_status, response_body, tmp_path
    ) -> None:
        # wf:contract: a site home without static/favicon.ico leaves /favicon.ico undeclared.
        (tmp_path / "home").mkdir()
        server = AsgiServer(config=recipe(), site_home=str(tmp_path / "home"))
        sent = await http_request(server, "/favicon.ico")
        assert response_status(sent) == 404
        assert UNIMPLEMENTED in response_body(sent)

    async def test_an_application_may_answer_it_when_server_does_not(
        self, http_request, response_body
    ) -> None:
        # wf:contract: an application declaring /favicon.ico answers it when _server has no
        # wf:contract: file to serve.
        server = AsgiServer(config=recipe(site_class=IconSite))
        assert response_body(await http_request(server, "/favicon.ico")) == b"site-icon"

    async def test_an_application_declaring_it_wins_over_the_file(
        self, http_request, response_body, tmp_path: Path
    ) -> None:
        # An application declaring /favicon.ico answers it even when
        # static/favicon.ico exists: _server gives it only as a default.
        server = AsgiServer(config=recipe(site_class=IconSite), site_home=self.home_with_icon(tmp_path))
        assert response_body(await http_request(server, "/favicon.ico")) == b"site-icon"
