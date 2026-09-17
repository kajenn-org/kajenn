# Routing system — current state

**Version**: 0.5 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Routing ownership and filters

`genro-routes` owns `RoutingClass`, the tree, `add_branches`, resolution and
plugins. This package owns `PluginMixin`, the OpenAPI dialect plugin and its
configuration mapping. Dependency versions are declared in `pyproject.toml`.

`RoutedApplication.auth_filters` supplies avatar tags to HTTP and synthetic WSX
resolution. MCP supplies tags and its `channel_channel` filter when listing or
calling tools. The OpenAPI translator publishes capability requirements as
`x-requires`; this is schema metadata, not a new HTTP capability gate.
HTTP dispatch does not provide a channel filter or enforce an OpenAPI method:
a documented GET route is not thereby inaccessible to POST.

Claim anchors: [`PluginMixin`](../../../src/kajenn/plugin_mixin.py#L74), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L111), [`auth_filters`](../../../src/kajenn/routed_application.py#L220).

## Plugin arming and extension

`PluginMixin` always enables `pydantic` and `openapi`; attempting to disable
either raises `ValueError`. Applications plug `auth` themselves. Configured
extras are armed lazily when an attached application's router is first read.
`arm_router` avoids attaching an already present code twice.

`default_plugin_registry()` returns a fresh mapping. Python construction can
extend it with `plugin_registry`, while the recipe's plugins section provides
enabled/options values. There is no recipe field for a plugin registry class.
The routing library's class registry is process-wide: the first registered
class for a code is retained, even when another server supplies a different
class under that same code. This is a remaining configuration collision risk.

Claim anchors: [`PluginMixin`](../../../src/kajenn/plugin_mixin.py#L74), [`arm_router`](../../../src/kajenn/plugin_mixin.py#L130), [`default_plugin_registry`](../../../src/kajenn/plugin_mixin.py#L64), [`plugin_registry`](../../../src/kajenn/plugin_mixin.py#L126).

## OpenAPI metadata and branch forms

`OpenAPIPlugin` declares per-route options such as `openapi_method` and
`openapi_tags`. The translator consumes the neutral description's raw
`metadata.plugin_config.openapi` values; `entry_metadata`'s extra contribution
is not the translator's source. The historical uncovered-line inventory is not
a fresh coverage measurement.

Consumers use `add_branches` with already constructed instances. The retired
`attach_instance` API is absent. The design's declarative branch migration
should not be declared complete from the removal of that old method alone.

See [OpenAPI status](../020_applications/openapi/status.md) for the schema and
[MCP status](../020_applications/mcp/status.md) for protocol behavior.

Claim anchors: [`OpenAPIPlugin`](../../../src/kajenn/plugins/openapi/plugin.py#L49), [`entry_metadata`](../../../src/kajenn/plugins/openapi/plugin.py#L73).

## Source and test evidence

- [src/kajenn/plugin_mixin.py](../../../src/kajenn/plugin_mixin.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/mcp/engine.py](../../../src/kajenn/mcp/engine.py)
- [src/kajenn/plugins/openapi/plugin.py](../../../src/kajenn/plugins/openapi/plugin.py)
- [src/kajenn/plugins/openapi/translator.py](../../../src/kajenn/plugins/openapi/translator.py)
- [src/kajenn/config/handler.py](../../../src/kajenn/config/handler.py)
- [pyproject.toml](../../../pyproject.toml)
- [tests/core/test_plugins.py](../../../tests/core/test_plugins.py)
- [tests/core/test_routed_application.py](../../../tests/core/test_routed_application.py)
- [tests/core/test_mcp_engine.py](../../../tests/core/test_mcp_engine.py)
