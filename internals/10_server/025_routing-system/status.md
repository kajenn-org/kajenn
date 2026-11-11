# Routing system — current state

**Version**: 0.6 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline), genro-routes
0.30.1. Test references below identify the executable contracts; they are not a
new coverage percentage.

## Routing ownership and filters

`genro-routes` owns `RoutingClass`, the tree, `add_branches`, resolution and
plugins. This package owns `PluginMixin`, the OpenAPI dialect plugin and its
configuration mapping. Dependency versions are declared in `pyproject.toml`.

`RoutedApplication.execute` is the single execution point of every face: REST
and WSX through `__call__`, an MCP `tools/call` through the transport, the
kbus through `__call__`. `RoutedApplication.discover` is the listing every face
reads: the OpenAPI schema and the MCP `tools/list`. Both authenticate the scope
first through `authenticate_scope`. Both resolve with `auth_filters`.

`auth_filters` passes two filters. `channel_channel` is the scope's
`kajenn.channel`, or the class attribute `http_channel` when the scope has none.
`auth_tags` is the comma-separated tags of the avatar on `scope["auth"]`, and is
absent when there is no identity. Every face supplies a channel:

- `RoutedApplication.http_channel` is `"rest"`; `__call__` sets it on a scope that carries none.
- WSX sets `"wsx"` on the connection scope and on every synthetic message scope (`wsx.py:269`, `wsx.py:428`).
- The MCP transport sets `"mcp"` on the MCP POST and on each tool-call scope (`applications/mcp.py:184`, `:204`).
- A kbus frame carries the channel in `meta["channel"]`, and the receiver puts it on `kajenn.channel` (`kbus_mixin.py:489-490`, `:525`).

No code passes a capabilities filter. Nothing in `src/` passes
`env_capabilities` to a walk, nothing arms the `env` plugin, and nothing declares
a `CapabilitiesSet`. The only reader of `env_requires` is the OpenAPI
translator, which publishes it as `x-requires` (`translator.py:240-246`). That
is schema metadata, not a filter.

`method_filters` passes the request's HTTP method as `openapi_method` when the
request is on `http_channel` and did not come through the kbus. The `openapi`
plugin's `deny_reason` refuses an entry declaring another method with
`METHOD_NOT_ALLOWED` followed by the declared method. `execute` answers that
refusal with 405 and an `Allow` header naming the declared method; the handler
does not run. An entry declaring no method answers every method. MCP, WSX and
kbus requests pass no method filter. `discover` does not use it, so a listing
shows the routes of every method. Tests:
`tests/core/test_route_method.py::TestDeclaredMethod::test_get_on_a_post_route_is_405_with_allow_and_never_runs`,
`::TestUndeclaredMethod::test_a_route_without_a_declaration_answers_every_method`,
`::TestNotHttp::test_a_kbus_call_reaches_a_get_route`,
`::TestNotHttp::test_discovery_still_lists_routes_of_every_method`.

No code filters on capabilities. The owner statement of 2026-11-03 (design.md,
header) says routes depend on auth tags and capabilities. The code passes tags
and channel only (`routed_application.py:326-330`), and
nothing produces a capabilities value per request.

Claim anchors: [`PluginMixin`](../../../src/kajenn/plugin_mixin.py#PluginMixin), [`RoutedApplication`](../../../src/kajenn/routed_application.py#RoutedApplication), [`execute`](../../../src/kajenn/routed_application.py#RoutedApplication.execute), [`authenticate_scope`](../../../src/kajenn/routed_application.py#RoutedApplication.authenticate_scope), [`discover`](../../../src/kajenn/routed_application.py#RoutedApplication.discover), [`auth_filters`](../../../src/kajenn/routed_application.py#RoutedApplication.auth_filters), [`method_filters`](../../../src/kajenn/routed_application.py#RoutedApplication.method_filters), [`deny_reason`](../../../src/kajenn/plugins/openapi/plugin.py#OpenAPIPlugin.deny_reason).

## Plugin arming and extension

`PluginMixin` always enables `pydantic` and `openapi`; attempting to disable
either raises `ValueError`. Applications plug `auth` themselves in
`RoutedApplication.__init__`. `arm_router` arms every entry of the resolved
plugin configuration — the fixed pair and every extra the `plugins` section
enables — and skips a code already attached to the router.

Plugins are armed once per application, on the first `route` access made after
the application is attached to a server. The `route` property sets `_armed` and
calls `server.arm_router` on that access only (`routed_application.py:171-183`).
The plugin configuration is read at construction: `AsgiServer` passes
`config.plugins_config()` as the `plugins` constructor kwarg
(`asgi_server.py:209`), and `PluginMixin.__init__` resolves it into
`_plugins_config` once (`plugin_mixin.py:94`). No code changes the plugins of an
armed router: nothing in `src/` calls `set_plugin_enabled`, nothing re-reads the
`plugins` section, and every `plug` and `configure` call runs in a constructor
or in `arm_router` — `McpOpenApiApplication.__init__` plugs `channel` and
configures it with `channels="rest"` (`applications/mcp.py:429-430`),
`McpTransport.build_engine` plugs `pydantic` and `auth` when absent
(`applications/mcp.py:131-133`).

No code changes the plugins of an armed tree. The owner statement of 2026-11-03
(design.md, header) says plugins can be configured dynamically. The code arms
once and holds no path to a later change. genro-routes
0.30.1 provides `Router.plug`, `Router.set_plugin_enabled` and
`Router.get_config` (`genro_routes/core/router.py:163`, `:300`, `:231`).

`default_plugin_registry()` returns a fresh mapping. Python construction can
extend it with `plugin_registry`, while the recipe's plugins section provides
enabled/options values. There is no recipe field for a plugin registry class.
The routing library's class registry is process-wide: the first registered
class for a code is retained, even when another server supplies a different
class under that same code. This is a remaining configuration collision risk.

Claim anchors: [`PluginMixin`](../../../src/kajenn/plugin_mixin.py#PluginMixin), [`arm_router`](../../../src/kajenn/plugin_mixin.py#PluginMixin.arm_router), [`default_plugin_registry`](../../../src/kajenn/plugin_mixin.py#default_plugin_registry), [`plugin_registry`](../../../src/kajenn/plugin_mixin.py#PluginMixin.plugin_registry), [`route`](../../../src/kajenn/routed_application.py#RoutedApplication.route).

## OpenAPI metadata and branch forms

`OpenAPIPlugin` declares per-route options such as `openapi_method` and
`openapi_tags`. The translator consumes the neutral description's raw
`metadata.plugin_config.openapi` values; `entry_metadata`'s extra contribution
is not the translator's source. The historical uncovered-line inventory is not
a fresh coverage measurement.

Consumers use `add_branches` with already constructed instances. The retired
`attach_instance` API is absent. The design's declarative branch migration
should not be declared complete from the removal of that old method alone.

No code removes a branch. genro-routes 0.30.1 provides `remove_branch(name)` and
`detach_instance(child)` (`genro_routes/core/base_router.py:665`, `:773`);
nothing in `src/` calls either. The owner statement of 2026-11-03 (design.md,
header) says applications can even be mounted and unmounted.

See [OpenAPI status](../020_applications/openapi/status.md) for the schema and
[MCP status](../020_applications/mcp/status.md) for protocol behavior.

Claim anchors: [`OpenAPIPlugin`](../../../src/kajenn/plugins/openapi/plugin.py#OpenAPIPlugin), [`entry_metadata`](../../../src/kajenn/plugins/openapi/plugin.py#OpenAPIPlugin.entry_metadata).

## Source and test evidence

- [src/kajenn/plugin_mixin.py](../../../src/kajenn/plugin_mixin.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/mcp/engine.py](../../../src/kajenn/mcp/engine.py)
- [src/kajenn/applications/mcp.py](../../../src/kajenn/applications/mcp.py)
- [src/kajenn/plugins/openapi/plugin.py](../../../src/kajenn/plugins/openapi/plugin.py)
- [src/kajenn/plugins/openapi/translator.py](../../../src/kajenn/plugins/openapi/translator.py)
- [src/kajenn/config/handler.py](../../../src/kajenn/config/handler.py)
- [pyproject.toml](../../../pyproject.toml)
- [tests/core/test_plugins.py](../../../tests/core/test_plugins.py)
- [tests/core/test_routed_application.py](../../../tests/core/test_routed_application.py)
- [tests/core/test_route_method.py](../../../tests/core/test_route_method.py)
- [tests/core/test_unified_discovery.py](../../../tests/core/test_unified_discovery.py)
- [tests/core/test_mcp_engine.py](../../../tests/core/test_mcp_engine.py)
