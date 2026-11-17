# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""``plugin_configuration``: the recipe gives plugin options to the routes of an application.

Every test below drives a real ``AsgiServer`` through HTTP. A ``configure``
line replaces what ``@route(...)`` wrote for the routes it names; a route the
recipe does not name keeps the value of the code. A line that cannot be applied
is left out and the server starts. The ``plugin_configuration`` hook of the
application applies the recipe: a subclass that does not call it applies
nothing, and one that reads its router first sees the values of the code. The
same holds for ``_server``, for an application added to a running server, for
a block or a line written or changed on a running server, and in the process of
an application declared with ``spawner``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from genro_routes import RoutingClass, route

from kajenn import AsgiServer, AuthApplicationBase, RoutedApplication
from kajenn.asgi_endpoint import BufferedAsgiEndpoint
from kajenn.config import AsgiConfigBuilder
from kajenn.server_app.server_app import ServerApplication
from tests.conftest import start_server
from tests.storage_support import MemoryAuthStore

STAFF = [(b"authorization", b"Bearer staff_token")]
BOSS = [(b"authorization", b"Bearer boss_token")]

#: The fixed bearers ``Tokens`` verifies: identity and tag of each.
BEARERS = {"Bearer staff_token": ("staff_user", "staff"), "Bearer boss_token": ("boss_user", "boss")}


class Tokens(AuthApplicationBase):
    """An authentication application verifying the two fixed bearers of ``BEARERS``."""

    def verify_credential(self, credential: str) -> dict[str, Any]:
        if credential not in BEARERS:
            return super().verify_credential(credential)
        identity, tag = BEARERS[credential]
        return {"identity": identity, "group": None, "tags": [tag], "data": {},
                "valid_until": None}


class Products(RoutingClass):
    @route()
    def list(self) -> list[str]:
        return ["chair"]

    @route(auth_rule="shop_admin")
    def insert(self) -> str:
        return "inserted"

    @route(auth_rule="shop_admin")
    def delete(self) -> str:
        return "deleted"


class Shop(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.products = Products()
        self.route.add_branches({"name": "products", "instance": self.products})

    @route()
    def info(self) -> str:
        return "info"


class DeafShop(Shop):
    """Refuses the recipe: its hook does not call the base."""

    def plugin_configuration(self) -> None:
        pass


class WatchingShop(Shop):
    """Reads its router before the recipe is applied."""

    def plugin_configuration(self) -> None:
        self.rule_of_delete_in_code = self.products.route.auth.configuration("delete")["rule"]
        super().plugin_configuration()


class ShopRecipe(AsgiConfigBuilder):
    """One application ``shop`` and ``_auth`` verifying two bearers, tags ``staff`` and ``boss``."""

    default_config = False
    app_class: type = Shop

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications()
        apps.application(code="_auth", app_class=Tokens).store(store_class=MemoryAuthStore)
        self.shop_configuration(apps.application(code="shop", app_class=self.app_class))
        self.other_applications(apps)

    def shop_configuration(self, shop: Any) -> None:
        shop_plugin_auth = shop.plugin_configuration(plugin="auth")
        shop_plugin_auth.configure(route="products/list,products/insert", rule="staff")
        shop_plugin_auth.configure(route="products/delete", rule="")

    def other_applications(self, apps: Any) -> None:
        pass


async def status(http_request: Any, server: AsgiServer, path: str, headers: Any = None,
                 method: str = "GET") -> int:
    sent = await http_request(server, path, method=method, headers=headers)
    return int(sent[0]["status"])


def shop_blocks(server: AsgiServer, code: str = "shop") -> list[Any]:
    """The ``plugin_configuration`` nodes of ``code`` in the live configuration."""
    node = server.config.node(f"applications.{code}")
    return [child for child in node.value if child.node_tag == "plugin_configuration"]


async def settle() -> None:
    """Let the tasks a configuration change started run."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_rule_of_the_recipe_replaces_the_rule_of_the_code(http_request: Any) -> None:
    server = AsgiServer(config=ShopRecipe)
    # No rule in the code: the recipe closes it.
    assert await status(http_request, server, "/shop/products/list") == 401
    assert await status(http_request, server, "/shop/products/list", STAFF) == 200
    assert await status(http_request, server, "/shop/products/list", BOSS) == 403
    # ``shop_admin`` in the code: the recipe replaces it with ``staff``.
    assert await status(http_request, server, "/shop/products/insert", STAFF) == 200


async def test_an_empty_rule_opens_a_route_the_code_closes(http_request: Any) -> None:
    server = AsgiServer(config=ShopRecipe)
    assert await status(http_request, server, "/shop/products/delete") == 200


async def test_a_route_the_recipe_does_not_name_keeps_the_code(http_request: Any) -> None:
    class ListOnly(ShopRecipe):
        def shop_configuration(self, shop: Any) -> None:
            shop.plugin_configuration(plugin="auth").configure(route="products/list", rule="staff")

    server = AsgiServer(config=ListOnly)
    assert await status(http_request, server, "/shop/info") == 200
    assert await status(http_request, server, "/shop/products/delete") == 401
    assert await status(http_request, server, "/shop/products/delete", STAFF) == 403


async def test_a_later_line_wins_on_the_same_route(http_request: Any) -> None:
    class Twice(ShopRecipe):
        def shop_configuration(self, shop: Any) -> None:
            shop_plugin_auth = shop.plugin_configuration(plugin="auth")
            shop_plugin_auth.configure(route="products/list,info", rule="staff")
            shop_plugin_auth.configure(route="info", rule="boss")

    server = AsgiServer(config=Twice)
    assert await status(http_request, server, "/shop/products/list", STAFF) == 200
    assert await status(http_request, server, "/shop/info", STAFF) == 403
    assert await status(http_request, server, "/shop/info", BOSS) == 200


async def test_every_plugin_of_the_router_is_configured_the_same_way(http_request: Any) -> None:
    class Posting(ShopRecipe):
        def shop_configuration(self, shop: Any) -> None:
            shop.plugin_configuration(plugin="openapi").configure(route="info", method="post")

    server = AsgiServer(config=Posting)
    assert await status(http_request, server, "/shop/info") == 405
    assert await status(http_request, server, "/shop/info", method="POST") == 200


async def test_a_line_that_cannot_be_applied_is_left_out_and_the_server_starts(
    http_request: Any, caplog: Any,
) -> None:
    class Wrong(ShopRecipe):
        def shop_configuration(self, shop: Any) -> None:
            shop_plugin_auth = shop.plugin_configuration(plugin="auth")
            shop_plugin_auth.configure(route="products/nothing", rule="staff")
            shop_plugin_auth.configure(route="nowhere/list", rule="staff")
            shop_plugin_auth.configure(route="info", wrong_option="staff")
            shop.plugin_configuration(plugin="nosuchplugin").configure(route="info", rule="staff")
            shop_plugin_auth.configure(route="products/list", rule="staff")

    server = AsgiServer(config=Wrong)
    with caplog.at_level(logging.WARNING, logger="kajenn.routed_application"):
        assert await status(http_request, server, "/shop/products/list") == 401
    assert await status(http_request, server, "/shop/info") == 200
    refused = [record.getMessage() for record in caplog.records]
    for route_path in ("products/nothing", "nowhere/list"):
        assert any(route_path in message for message in refused)
    assert any("nosuchplugin" in message for message in refused)
    assert any("wrong_option" in message for message in refused)


async def test_the_management_application_is_configured_like_any_other(http_request: Any) -> None:
    class Managed(ShopRecipe):
        def other_applications(self, apps: Any) -> None:
            management = apps.application(code="_server", app_class=ServerApplication)
            management.plugin_configuration(plugin="auth").configure(
                route="monitor/snapshot", rule="")

    assert await status(http_request, AsgiServer(config=ShopRecipe), "/_server/monitor/snapshot") == 401
    assert await status(http_request, AsgiServer(config=Managed), "/_server/monitor/snapshot") == 200


async def test_a_hook_that_does_not_call_the_base_applies_nothing(http_request: Any) -> None:
    class Deaf(ShopRecipe):
        app_class = DeafShop

    server = AsgiServer(config=Deaf)
    assert await status(http_request, server, "/shop/products/list") == 200
    assert await status(http_request, server, "/shop/products/delete") == 401


async def test_the_hook_sees_the_values_of_the_code_before_the_recipe(http_request: Any) -> None:
    class Watching(ShopRecipe):
        app_class = WatchingShop

    server = AsgiServer(config=Watching)
    assert await status(http_request, server, "/shop/products/delete") == 200
    shop = server.applications["shop"]
    assert isinstance(shop, WatchingShop)
    assert shop.rule_of_delete_in_code == "shop_admin"


async def test_an_application_added_to_a_running_server_is_configured(http_request: Any) -> None:
    server = await start_server(AsgiServer(config=ShopRecipe))
    late = server.application_nodes.application(code="late", app_class=Shop)
    late.plugin_configuration(plugin="auth").configure(route="products/list", rule="staff")
    async with asyncio.timeout(10):
        while not server.is_enabled("late"):
            await asyncio.sleep(0.005)
    await settle()
    assert await status(http_request, server, "/late/products/list") == 401
    assert await status(http_request, server, "/late/products/list", STAFF) == 200
    assert await status(http_request, server, "/late/info") == 200


async def test_a_line_changed_on_a_running_server_is_applied(http_request: Any) -> None:
    server = await start_server(AsgiServer(config=ShopRecipe))
    assert await status(http_request, server, "/shop/products/list", BOSS) == 403
    [block] = shop_blocks(server)
    first_line = next(iter(block.value))
    first_line.set_attr(rule="boss")
    await settle()
    assert await status(http_request, server, "/shop/products/list", BOSS) == 200
    assert await status(http_request, server, "/shop/products/insert", STAFF) == 403


async def test_a_block_written_on_a_running_server_is_applied(http_request: Any) -> None:
    server = await start_server(AsgiServer(config=ShopRecipe))
    assert await status(http_request, server, "/shop/info") == 200
    shop = server.config.node("applications.shop").value
    shop.plugin_configuration(plugin="auth").configure(route="info", rule="boss")
    await settle()
    assert await status(http_request, server, "/shop/info") == 401
    assert await status(http_request, server, "/shop/info", BOSS) == 200
    assert sorted(server.applications) == ["_auth", "_server", "shop"]


async def test_another_word_written_inside_an_application_adds_no_application(
    http_request: Any,
) -> None:
    server = await start_server(AsgiServer(config=ShopRecipe))
    server.config.node("applications.shop").value.parameters(currency="EUR")
    await settle()
    assert sorted(server.applications) == ["_auth", "_server", "shop"]
    assert server.applications["shop"].config("parameters.currency") == "EUR"
    assert await status(http_request, server, "/shop/products/delete") == 200


SPAWNED_RECIPE = '''
from typing import Any

from genro_routes import RoutingClass, route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES


class Products(RoutingClass):
    @route()
    def list(self):
        return ["chair"]

    @route(auth_rule="shop_admin")
    def delete(self):
        return "deleted"


class Shop(RoutedApplication):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.products = Products()
        self.route.add_branches({"name": "products", "instance": self.products})


class Recipe(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        shop = apps.application(code="shop", app_class=Shop, spawner="subprocess",
                                request_timeout=5)
        shop_plugin_auth = shop.plugin_configuration(plugin="auth")
        shop_plugin_auth.configure(route="products/list", rule="staff")
        shop_plugin_auth.configure(route="products/delete", rule="")
'''


@asynccontextmanager
async def running(config: str) -> AsyncIterator[AsgiServer]:
    server = AsgiServer(config=config)
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    lifespan = asyncio.create_task(server(
        {"type": "lifespan", "asgi": {"version": "3.0"}}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        async with asyncio.timeout(30):
            while not all(server.is_enabled(code) for code in server.applications):
                await asyncio.sleep(0.05)
        yield server
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await lifespan


async def spawned_status(server: AsgiServer, path: str) -> int:
    """The status of a GET that reaches the spawned process through the mount."""
    result = await BufferedAsgiEndpoint(server).serve({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }, b"")
    return int(result["status"])


async def test_a_spawned_application_applies_the_recipe_in_its_own_process(tmp_path: Any) -> None:
    config = tmp_path / "config.py"
    config.write_text(SPAWNED_RECIPE)
    async with running(str(config)) as server:
        assert await spawned_status(server, "/shop/products/list") == 401
        assert await spawned_status(server, "/shop/products/delete") == 200
