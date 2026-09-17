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

"""Plugin system tests (Macro 4 Phase 5).

Covers the ``PluginMixin`` server capability (default registry, registry
extension, ``arm_router``), the config-driven arming through
``AsgiServer(config=...)``, the no-import-side-effect guarantee (checked in a
fresh subprocess so it is order-independent) and the ported OpenAPI translator +
``router_openapi``.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from typing import Any

import pytest
from genro_routes import Router, RoutingClass, route
from genro_routes.plugins._base_plugin import BasePlugin

from kajenn import AsgiServer, OpenAPIPlugin, OpenAPITranslator, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.plugin_mixin import PluginMixin, default_plugin_registry
from kajenn.plugins import router_openapi


class _MiniBase:
    """Terminal cooperative base: rejects any leftover kwarg."""

    def __init__(self, **kwargs: Any) -> None:
        if kwargs:
            raise TypeError(f"unexpected kwargs: {sorted(kwargs)}")


class _MiniServer(PluginMixin, _MiniBase):
    """A bare PluginMixin composition, no other capability."""


class _CustomPlugin(BasePlugin):
    """A distinct plugin whose code matches its registry key ("customtest")."""

    plugin_code = "customtest"
    plugin_description = "test-only plugin"

    def configure(self, enabled: bool = True, flag: str | None = None) -> None:  # type: ignore[override]
        """No-op configure (storage handled by the wrapper)."""


class _Svc(RoutingClass):
    """A tiny external router used as an arming target."""

    @route()
    def ping(self) -> dict:
        """Ping."""
        return {"ok": True}


class ApiApp(RoutedApplication):
    """Routed app with typed handlers to exercise the schema path."""

    @route()
    def add(self, x: int, y: int = 0) -> dict:
        """Add two numbers."""
        return {"sum": x + y}

    @route(openapi_method="delete")
    def remove(self, item_id: int) -> dict:
        """Remove an item."""
        return {"deleted": item_id}

    @route()
    def make(self, items: list[str]) -> dict:
        """Make from a list."""
        return {"n": len(items)}


class TestDefaultRegistry:
    def test_default_registry_holds_openapi(self) -> None:
        assert default_plugin_registry() == {"openapi": OpenAPIPlugin}

    def test_default_registry_is_fresh_each_call(self) -> None:
        first = default_plugin_registry()
        first["injected"] = OpenAPIPlugin
        assert "injected" not in default_plugin_registry()

    def test_registry_extension_merges_over_default(self) -> None:
        server = _MiniServer(
            plugins={"customtest": True}, plugin_registry={"customtest": _CustomPlugin}
        )
        assert server.plugin_registry["customtest"] is _CustomPlugin
        assert server.plugin_registry["openapi"] is OpenAPIPlugin


class TestArmRouter:
    def test_arms_enabled_plugin_on_router(self) -> None:
        server = _MiniServer(plugins={"openapi": True})
        svc = _Svc()
        server.arm_router(svc.route)
        assert "openapi" in {plugin.name for plugin in svc.route.iter_plugins()}

    def test_dict_value_tunes_the_fixed_plugin_with_options(self) -> None:
        server = _MiniServer(plugins={"openapi": {"security_scheme": "ApiKey"}})
        # openapi is fixed; the dict value tunes it. pydantic stays in the base.
        assert server.plugins == {"openapi": {"security_scheme": "ApiKey"}, "pydantic": {}}
        svc = _Svc()
        server.arm_router(svc.route)  # options reach router.plug without error
        assert "openapi" in {plugin.name for plugin in svc.route.iter_plugins()}

    def test_bundled_plugin_plugged_by_name(self) -> None:
        server = _MiniServer(plugins={"pydantic": True})
        svc = _Svc()
        server.arm_router(svc.route)
        assert "pydantic" in {plugin.name for plugin in svc.route.iter_plugins()}

    def test_registry_extension_arms_custom_plugin(self) -> None:
        server = _MiniServer(
            plugins={"customtest": True}, plugin_registry={"customtest": _CustomPlugin}
        )
        svc = _Svc()
        server.arm_router(svc.route)
        assert "customtest" in {plugin.name for plugin in svc.route.iter_plugins()}

    def test_disabling_a_fixed_plugin_is_a_config_error(self) -> None:
        with pytest.raises(ValueError, match="fixed structure"):
            _MiniServer(plugins={"openapi": False})

    def test_an_extra_name_is_retained_beside_the_fixed_base(self) -> None:
        # Retention is resolution-time; whether the name can be armed is decided
        # later by arm_router (see test_unknown_plugin_name_raises).
        server = _MiniServer(plugins={"logging": True})
        assert set(server.plugins) == {"pydantic", "openapi", "logging"}

    def test_arming_registers_the_plugin_in_the_router_registry(self) -> None:
        # The mirror of the subprocess test below: importing does not register,
        # arming does.
        server = _MiniServer(plugins={"openapi": True})
        server.arm_router(_Svc().route)
        assert "openapi" in Router.available_plugins()

    def test_false_leaves_an_extra_plugin_unarmed(self) -> None:
        server = _MiniServer(
            plugins={"customtest": False}, plugin_registry={"customtest": _CustomPlugin}
        )
        # the extra is dropped; only the fixed base remains.
        assert set(server.plugins) == {"pydantic", "openapi"}
        svc = _Svc()
        server.arm_router(svc.route)
        assert "customtest" not in {plugin.name for plugin in svc.route.iter_plugins()}

    def test_arming_twice_is_a_no_op(self) -> None:
        server = _MiniServer(plugins={"openapi": True, "pydantic": True})
        svc = _Svc()
        server.arm_router(svc.route)
        server.arm_router(svc.route)  # must not raise on the already-plugged names
        names = [plugin.name for plugin in svc.route.iter_plugins()]
        assert names.count("openapi") == 1

    def test_unknown_plugin_name_raises(self) -> None:
        server = _MiniServer(plugins={"does-not-exist": True})
        svc = _Svc()
        with pytest.raises(ValueError):
            server.arm_router(svc.route)


class PluginsConfig(AsgiConfigBuilder):
    """Two plugins armed, one explicitly disabled."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8000)
        cfg.applications(default="api").application(code="api", mount="", app_class=ApiApp)
        self.plugins_section(cfg)

    def plugins_section(self, cfg: Any) -> None:
        """``enabled=False`` leaves a plugin unarmed."""
        plugins = cfg.plugins()
        plugins.plugin(code="openapi")
        plugins.plugin(code="pydantic")
        plugins.plugin(code="logging", enabled=False)


class TestConfigDriven:
    def test_configured_server_carries_the_plugins_config(self) -> None:
        server = AsgiServer(config=PluginsConfig)
        assert server.plugins == {"openapi": {}, "pydantic": {}}

    def test_configured_server_arms_the_routed_app(self) -> None:
        server = AsgiServer(config=PluginsConfig)
        names = {plugin.name for plugin in server.root_application.route.iter_plugins()}
        assert {"auth", "openapi", "pydantic"} <= names

    def test_plugin_options_map_to_a_dict(self) -> None:
        class Recipe(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.applications(default="api").application(
                    code="api", mount="", app_class=ApiApp
                )
                cfg.plugins().plugin(code="openapi", security_scheme="ApiKey")

        server = AsgiServer(config=Recipe)
        # openapi is fixed; the config tunes it. pydantic stays in the base.
        assert server.plugins == {"openapi": {"security_scheme": "ApiKey"}, "pydantic": {}}


class TestNoImportSideEffect:
    def test_importing_the_package_does_not_register_openapi(self) -> None:
        code = (
            "import kajenn\n"
            "import kajenn.plugins.openapi\n"
            "from genro_routes import Router\n"
            "assert 'openapi' not in Router.available_plugins(), Router.available_plugins()\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestLazyArming:
    def test_unmounted_app_arms_nothing_extra(self) -> None:
        app = ApiApp()  # no server → only the auth plug from __init__
        assert {plugin.name for plugin in app.route.iter_plugins()} == {"auth"}

    def test_mounted_app_arms_on_first_route_access(self) -> None:
        server = AsgiServer(applications=[(ApiApp, {"mount": ""})], plugins={"openapi": True})
        app = server.applications["apiapp"]
        names = {plugin.name for plugin in app.route.iter_plugins()}
        assert {"auth", "openapi"} <= names

    def test_no_plugins_config_still_arms_the_fixed_base(self) -> None:
        # A mixin-equipped server always arms the fixed base (pydantic/openapi)
        # on top of the app's own ``auth`` plug, even with no plugins config.
        app = AsgiServer(applications=[(ApiApp, {"mount": ""})]).applications["apiapp"]
        assert {plugin.name for plugin in app.route.iter_plugins()} == {
            "auth",
            "pydantic",
            "openapi",
        }


class TestTranslator:
    def test_translator_module_imports_no_pydantic(self) -> None:
        # The redesigned translator reads genro-routes' cached neutral blocks;
        # it must not import pydantic nor inspect callables (ratified ruling).
        from kajenn.plugins.openapi import translator as translator_module

        source = inspect.getsource(translator_module)
        assert "import pydantic" not in source
        assert "from pydantic" not in source
        assert "create_pydantic_model_for_func" not in source

    def test_guess_get_for_scalar_fields(self) -> None:
        fields = [
            {"name": "x", "schema": {"type": "integer"}, "required": True, "kind": "pk"},
            {"name": "y", "schema": {"type": "string"}, "required": False, "kind": "pk"},
        ]
        assert OpenAPITranslator.guess_http_method(fields) == "get"

    def test_guess_post_for_non_scalar_field(self) -> None:
        fields = [{"name": "items", "schema": {"type": "array"}, "required": True, "kind": "pk"}]
        assert OpenAPITranslator.guess_http_method(fields) == "post"

    def test_guess_get_for_optional_scalar_union(self) -> None:
        fields = [
            {
                "name": "n",
                "schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "required": False,
                "kind": "pk",
            }
        ]
        assert OpenAPITranslator.guess_http_method(fields) == "get"

    def test_guess_ignores_untyped_and_var_fields(self) -> None:
        fields = [
            {"name": "a", "schema": None, "required": True, "kind": "positional_or_keyword"},
            {"name": "kwargs", "schema": None, "required": False, "kind": "var_keyword"},
        ]
        assert OpenAPITranslator.guess_http_method(fields) == "get"

    def test_ref_schema_is_non_scalar(self) -> None:
        fields = [{"name": "body", "schema": {"$ref": "#/$defs/Item"}, "required": True, "kind": "pk"}]
        assert OpenAPITranslator.guess_http_method(fields) == "post"

    def test_schema_to_parameters_marks_required(self) -> None:
        schema = {
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x"],
        }
        params = OpenAPITranslator.schema_to_parameters(schema)
        by_name = {p["name"]: p for p in params}
        assert by_name["x"]["required"] is True
        assert by_name["y"]["required"] is False
        assert by_name["x"]["in"] == "query"


class TestRouterOpenapi:
    def _armed_service(self) -> ApiApp:
        server = _MiniServer(plugins={"openapi": True, "pydantic": True})
        svc = ApiApp()
        server.arm_router(svc.route)
        return svc

    def test_flat_paths_carry_operations_and_schemas(self) -> None:
        spec = router_openapi(self._armed_service().route)
        assert set(spec["paths"]) == {"/add", "/remove", "/make"}
        add_get = spec["paths"]["/add"]["get"]
        assert add_get["operationId"] == "add"
        # Input query params come from the cached request_schema: x required, y not.
        by_name = {p["name"]: p for p in add_get["parameters"]}
        assert by_name["x"]["required"] is True
        assert by_name["y"]["required"] is False
        assert "responses" in add_get

    def test_method_override_from_handler_config(self) -> None:
        spec = router_openapi(self._armed_service().route)
        assert "delete" in spec["paths"]["/remove"]

    def test_complex_param_becomes_post_request_body(self) -> None:
        spec = router_openapi(self._armed_service().route)
        make_post = spec["paths"]["/make"]["post"]
        assert "requestBody" in make_post

    def test_hierarchical_format_preserves_the_tree(self) -> None:
        server = _MiniServer(plugins={"openapi": True, "pydantic": True})
        parent = ApiApp()
        parent.route.add_branches({"name": "sub", "instance": _Svc()})
        server.arm_router(parent.route)
        flat = router_openapi(parent.route)
        assert "/sub/ping" in flat["paths"]
        hierarchical = router_openapi(parent.route, hierarchical=True)
        assert "/add" in hierarchical["paths"]
        assert "sub" in hierarchical["routers"]
        assert "/ping" in hierarchical["routers"]["sub"]["paths"]

    def test_empty_router_yields_empty_paths(self) -> None:
        class Empty(RoutingClass):
            pass

        assert router_openapi(Empty().route) == {"paths": {}}
