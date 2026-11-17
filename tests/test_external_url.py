"""Contract: ``server.external_url`` is optional and drives ``external_links``.

A server with a public address has ``external_url`` set (trailing slash
stripped) and its applications have the ``external_links`` capability: a route
declaring ``env_requires="external_links"`` answers. Without one (not given,
or a resolver that yields nothing) ``external_url`` is ``None`` and the route
answers 404. The ``default`` template reads ``KAJENN_EXTERNAL_URL``.
``kajenn configure`` writes ``--external-url`` literally when given and the
``KAJENN_EXTERNAL_URL`` resolver when absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from genro_bag.resolvers import EnvResolver
from genro_routes import route

from tests.conftest import start_server

from kajenn import AsgiServer
from kajenn.__main__ import Cli, ServerLauncher, SitesRegistry
from kajenn.config import AsgiConfigBuilder
from kajenn.routed_application import RoutedApplication
from kajenn.types import Message

PUBLIC = "https://shop.example.com"
VARIABLE = "KAJENN_EXTERNAL_URL"


class Links(RoutedApplication):
    """One route that hands out an absolute link of the server."""

    @route(media_type="text/plain", env_requires="external_links")
    def share(self) -> str:
        return f"{self.server.external_url}/shop/item"


async def share_status(server: AsgiServer) -> int:
    """The status of ``GET /shop/share`` on a started ``server``."""
    await start_server(server)
    scope = {"type": "http", "method": "GET", "path": "/shop/share",
             "query_string": b"", "headers": []}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def recipe(**server_options: Any) -> type[AsgiConfigBuilder]:
    """A recipe mounting ``Links`` at ``shop`` with ``server_options`` on its server element."""

    class Recipe(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=8000, **server_options)
            cfg.applications().application(code="shop", mount="shop", app_class=Links)

    return Recipe


@pytest.fixture(autouse=True)
def no_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with ``KAJENN_EXTERNAL_URL`` unset."""
    monkeypatch.delenv(VARIABLE, raising=False)


class TestARecipe:
    async def test_without_external_url_it_builds_and_the_link_route_is_not_available(
        self,
    ) -> None:
        server = AsgiServer(config=recipe())
        assert server.external_url is None
        assert await share_status(server) == 404

    async def test_a_declared_value_is_the_public_address_and_the_route_answers(self) -> None:
        server = AsgiServer(config=recipe(external_url=f"{PUBLIC}/"))
        assert server.external_url == PUBLIC
        assert await share_status(server) == 200

    async def test_a_resolver_on_a_set_variable_gives_the_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VARIABLE, PUBLIC)
        server = AsgiServer(config=recipe(external_url=EnvResolver(VARIABLE)))
        assert server.external_url == PUBLIC
        assert await share_status(server) == 200

    async def test_a_resolver_on_an_unset_variable_gives_none(self) -> None:
        server = AsgiServer(config=recipe(external_url=EnvResolver(VARIABLE)))
        assert server.external_url is None
        assert await share_status(server) == 404


class TestAServerBuiltByHand:
    async def test_without_external_url_it_is_none(self) -> None:
        server = AsgiServer(applications=[(Links, {"mount": "shop"})], port=9000)
        assert server.external_url is None
        assert await share_status(server) == 404

    async def test_a_given_value_is_the_public_address(self) -> None:
        server = AsgiServer(applications=[(Links, {"mount": "shop"})], external_url=f"{PUBLIC}/")
        assert server.external_url == PUBLIC
        assert await share_status(server) == 200


class TestTheDefaultTemplate:
    def test_it_reads_the_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(VARIABLE, PUBLIC)
        assert AsgiServer(config="default").external_url == PUBLIC

    def test_with_the_variable_unset_there_is_no_public_address(self) -> None:
        assert AsgiServer(config="default").external_url is None

    def test_with_the_variable_unset_the_render_writes_an_empty_value(self) -> None:
        rendered = AsgiServer(config="default").config.builder.render()
        assert '<server external_url="">' in rendered


def configure(tmp_path: Path, *extra: str) -> Cli:
    """Run ``kajenn configure shop`` with every question answered by an option."""
    cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
    assert cli.run(["configure", "shop", "--home", str(tmp_path / "shop"),
                    "--template", "default", "--applications", "",
                    "--host", "127.0.0.1", "--port", "8000", *extra]) == 0
    return cli


def served(cli: Cli) -> AsgiServer:
    """The server ``kajenn serve shop`` builds from the card."""
    return ServerLauncher(cli.parser().parse_args(["serve", "shop"]), cli.registry).build_server()


class TestConfigure:
    def test_a_given_value_is_written_literally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cli = configure(tmp_path, "--external-url", PUBLIC)
        monkeypatch.setenv(VARIABLE, "https://other.example.com")
        assert served(cli).external_url == PUBLIC

    def test_without_the_option_the_recipe_reads_the_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cli = configure(tmp_path)
        assert 'EnvResolver("KAJENN_EXTERNAL_URL", default="")' in (tmp_path / "shop" / "config.py").read_text()
        assert served(cli).external_url is None
        monkeypatch.setenv(VARIABLE, PUBLIC)
        assert served(cli).external_url == PUBLIC
