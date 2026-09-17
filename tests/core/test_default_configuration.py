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

"""The configuration always exists: a server composed in code has a tree too.

``AsgiServer(applications=[...], host=..., port=...)`` is a SHORTCUT: it takes a
ready-made configuration (``DefaultConfiguration``, the template named
``default``), writes the kwargs it received into a top layer of its own
(``ShortcutConfiguration``) and from there runs the ordinary road — the same
``ConfigurationHandler`` a ``config.py`` recipe produces. There is no second way
for a server to be born and no second place an option is read from.
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn import AsgiServer, BaseApplication, ConfigError, ConfigurationHandler
from kajenn.config.templates import (
    CONFIGURATION_TEMPLATES,
    DEFAULT_TEMPLATE,
    DefaultConfiguration,
    ShortcutConfiguration,
)
from kajenn.middleware.base import BaseMiddleware
from kajenn.session.store import MemorySessionStore
from kajenn.middleware.cors import CORSMiddleware
from kajenn.types import Receive, Scope, Send


class ShopApp(BaseApplication):
    """Plain application, used as the site root."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"shop"})


class StampMiddleware(BaseMiddleware):
    """A middleware the core's registry does not know, registered in code."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.next_app(scope, receive, send)


class ApiApp(BaseApplication):
    """Second application, on its own mount."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"api"})


BASIC_ADA = {"headers": [(b"authorization", b"Basic YWRhOmxvdmVsYWNl")]}


class TestTheConfigurationAlwaysExists:
    """A server built with kwargs reads through a handler like every other."""

    def test_a_server_composed_in_code_has_a_read_door(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})])
        assert isinstance(server.config, ConfigurationHandler)

    def test_the_default_template_is_the_base_of_the_shortcut(self) -> None:
        assert CONFIGURATION_TEMPLATES[DEFAULT_TEMPLATE] is DefaultConfiguration

    def test_the_template_brings_the_shipped_storage_layout(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})])
        mounts = list(server.config.node("storage").value)
        assert [child.node_tag for child in mounts] == ["local", "local"]
        assert [child.attr["name"] for child in mounts] == ["site", "home"]

    def test_the_applications_reach_the_tree(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""}), ApiApp])
        assert server.config("applications.shopapp.mount") == ""
        assert server.config("applications.apiapp.mount", default=None) is None
        assert server.config.node("applications.shopapp").attr["app_class"] is ShopApp

    def test_the_server_instantiates_from_the_tree(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})])
        assert isinstance(server.applications["shopapp"], ShopApp)
        assert server.applications["shopapp"].mount == ""

    def test_an_instance_is_refused_by_the_grammar(self) -> None:
        with pytest.raises(ValueError, match="app_class"):
            AsgiServer(applications=[ShopApp(mount="")])  # the refused form

    def test_the_listener_kwargs_reach_the_tree(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})], host="0.0.0.0", port=9101)
        assert server.config("server.host") == "0.0.0.0"
        assert server.config("server.port") == 9101
        assert server.config_host == "0.0.0.0"
        assert server.config_port == 9101

    def test_the_public_address_reaches_the_tree(self) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})], external_url="https://shop.example.com/"
        )
        assert server.config("server.external_url") == "https://shop.example.com/"
        assert server.external_url == "https://shop.example.com"

    def test_the_session_ttl_reaches_the_tree(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})], session_ttl=120)
        assert server.config("server.session.ttl") == 120
        assert server.session_store._default_ttl == 120

    def test_the_default_application_reaches_the_tree(self) -> None:
        server = AsgiServer(applications=[ShopApp, ApiApp], default="apiapp")
        assert server.config("applications.default") == "apiapp"
        assert server.default_application is server.applications["apiapp"]

    def test_the_middleware_switches_reach_the_tree(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})], middleware={"cors": True})
        assert server.config("middleware.cors") is True
        assert server.get_middleware(CORSMiddleware) is not None

    def test_a_switch_named_outside_the_grammar_reaches_the_tree(self) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})],
            middleware={"stamp": True},
            middleware_registry={"stamp": StampMiddleware},
        )
        assert server.config("middleware.stamp") is True
        assert server.get_middleware(StampMiddleware) is not None

    def test_the_session_snapshot_path_reaches_the_tree(self, tmp_path: Any) -> None:
        snapshot = tmp_path / "sessions.pickle"
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})], save_session=str(snapshot)
        )
        assert server.config("server.session.save_path") == str(snapshot)
        assert server.save_session == snapshot

    def test_the_handshake_options_reach_the_tree(self) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})],
            websocket={"origins": ["https://app.example.org"], "max_concurrent": 4},
        )
        assert server.config("server.websocket.origins") == "https://app.example.org"
        assert server.websocket_origins == ["https://app.example.org"]

    def test_the_declared_usage_mode_reaches_the_tree(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})], debug="sql")
        assert server.config("server.debug") == "sql"
        assert server.debug == "sql"


class TestNoLiveObjectReachesTheConstructor:
    """Every store is a CLASS the configuration names; the server builds it."""

    def test_the_session_store_class_reaches_the_tree(self) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})], session_store=MemorySessionStore
        )
        assert server.config("server.session.store_class") is MemorySessionStore
        assert isinstance(server.session_store, MemorySessionStore)

    def test_the_storage_mounts_reach_the_tree(self, tmp_path: Any) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})],
            storage=[{"name": "data", "protocol": "local", "base_path": str(tmp_path)}],
        )
        mounts = list(server.config.node("storage").value)
        # The declared mounts override the shipped ones position by position, so
        # ``home:`` — the second of the layout — survives under them.
        assert mounts[0].attr["name"] == "data"
        assert server.storage.get_mount_names() == ["data", "home"]

    def test_the_identity_store_classes_reach_the_tree(self) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})],
            users={"store_class": MemorySessionStore},
        )
        assert server.config("authentication.users.store_class") is MemorySessionStore

    def test_the_header_credentials_reach_the_tree(self) -> None:
        server = AsgiServer(
            applications=[(ShopApp, {"mount": ""})],
            auth={"basic": {"ada": {"password": "lovelace", "tags": "admin"}}},
        )
        written = list(server.config.node("authentication.credentials").value)
        assert [child.node_tag for child in written] == ["basic_user"]
        assert written[0].attr["username"] == "ada"
        assert server.auth_core.authenticate(BASIC_ADA) is not None


class TestTheShortcutTreeMatchesARecipe:
    """The tree of a kwargs-built server is the one a recipe would produce."""

    def test_the_two_roads_render_the_same_document(self) -> None:
        class HandWritten(DefaultConfiguration):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                self.server_section(cfg)
                self.storage_section(cfg)
                apps = cfg.applications()
                apps.application(code="shopapp", mount="", app_class=ShopApp)

            def server_section(self, cfg: Any) -> None:
                cfg.server(host="0.0.0.0", port=9101)

        by_recipe = AsgiServer(config=HandWritten)
        by_kwargs = AsgiServer(
            applications=[(ShopApp, {"mount": ""})], host="0.0.0.0", port=9101
        )
        assert by_kwargs.config.builder.render() == by_recipe.config.builder.render()


class TestTheTemplateByName:
    """A template name is a configuration source like a ``config.py`` path."""

    def test_the_name_builds_the_template(self) -> None:
        server = AsgiServer(config=DEFAULT_TEMPLATE)
        assert isinstance(server.config, ConfigurationHandler)
        assert server.config.node("storage") is not None

    def test_an_unknown_name_is_not_mistaken_for_a_template(self) -> None:
        with pytest.raises((ConfigError, ImportError, FileNotFoundError)):
            AsgiServer(config="no-such-template")


class TestTheShortcutRecipe:
    """``ShortcutConfiguration`` is a recipe like any other."""

    def test_it_leaves_the_instances_in_the_kwargs(self) -> None:
        kwargs: dict[str, Any] = {
            "applications": [ShopApp],
            "host": "127.0.0.1",
            "default": "shopapp",
            "middleware": {"cors": True},
            "plugins": {"openapi": True},
            "debug": True,
            "parent": "public",
        }
        ShortcutConfiguration(kwargs)
        assert kwargs == {"parent": "public"}

    def test_it_builds_one_configuration_root(self) -> None:
        recipe = ShortcutConfiguration({"applications": [ShopApp]})
        recipe.create()
        assert [node.label for node in recipe.source] == ["configuration"]


class TestTheRequestOptionsComeFromTheGrammarOnly:
    """The class-attribute road of #87 is gone: the recipe is the only door."""

    def test_the_class_attributes_are_no_longer_read(self) -> None:
        assert not hasattr(BaseApplication, "request_body")
        assert not hasattr(BaseApplication, "request_error_codes")

    def test_the_defaults_answer_without_a_request_node(self) -> None:
        app = AsgiServer(applications=[(ShopApp, {"mount": ""})]).applications["shopapp"]
        assert app.raw_body is False
        assert app.validation_error_status == 400

    def test_the_recipe_word_decides(self) -> None:
        class RawConfig(DefaultConfiguration):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                self.server_section(cfg)
                self.storage_section(cfg)
                apps = cfg.applications()
                apps.application(code="shopapp", mount="", app_class=ShopApp).request(
                    body="raw", error_codes="fastapi"
                )

        server = AsgiServer(config=RawConfig)
        app = server.applications["shopapp"]
        assert app.raw_body is True
        assert app.validation_error_status == 422
