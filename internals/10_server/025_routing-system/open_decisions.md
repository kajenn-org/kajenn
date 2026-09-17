# Routing system — open decisions

**Version**: 0.1 · **Last Updated**: 2026-09-17 · **Status**: 🔴 DA REVISIONARE

Source report: `kajenn-meta/verification/10_server/025_routing-system.md`.

Rows of that report whose verdict is `DIVERGE` or `SILENT`, with the matching `Divergences` blocks. Both are verbatim from the report.

## Table

| # | Entry in decisions.md | Implementation (code file:line, test) | Docstring (file:line, text) | Handoff/finaldoc (file, date, text) | Transcript (uuid, date, role, text) | Verdict |
|---|---|---|---|---|---|---|
| 1 | §1, owner 2026-08-24: "The tree, the walk, the filters and the plugins come from genro-routes; the core composes them and adds one dialect. They are explained **here**, after applications rather than before" | `src/genro_asgi/plugin_mixin.py:45` "from genro_routes import Router"; `src/genro_asgi/routed_application.py:115` "class RoutedApplication(BaseApplication, RoutingClass):"; the one dialect is `src/genro_asgi/plugin_mixin.py:71` "return {\"openapi\": OpenAPIPlugin}" · `tests/core/test_plugins.py::test_default_registry_holds_openapi`. The reading order of the documentation is not a code matter | `src/genro_asgi/plugins/openapi/__init__.py:15-24` "genro-routes exposes a dialect-neutral description of each endpoint via ``router.nodes()`` … This package is the OpenAPI *reader* of that description … It lives here, not in the routing core, because OpenAPI is one transport dialect among peers (alongside MCP), not a routing concern." | SILENT | SILENT | SILENT |
| 2 | §1: "An application page that also explained trees would be two subjects in one, which is what it was until this entry took the second." | SILENT (documentation organization, not a code matter) | SILENT (not a docstring matter) | SILENT | SILENT | SILENT |
| 3 | §2: "A method carrying the route marker becomes a node named after the method. There is no table of paths, no registration call, no file to keep in step … **a route cannot drift from its handler**" | `src/genro_asgi/routed_application.py:115` "class RoutedApplication(BaseApplication, RoutingClass):" — the tree is the class; resolution is by path against the class tree, `src/genro_asgi/routed_application.py:222` "node = self.route.node(request.path, errors=errors, **self.auth_filters(scope))"; no path table exists in the package · `tests/core/test_routed_application.py::test_sync_route_answers_json`, `tests/core/test_routed_application.py::test_attached_instance_reachable_under_its_name` | `src/genro_asgi/routed_application.py:17-19` "handlers are ``@route``-decorated methods on subclasses, and external ``RoutingClass`` instances mount as sub-trees via ``add_branches``" | SILENT | SILENT | SILENT |
| 4 | §3: "An option written at a route is named for the plugin that reads it — the plugin's code, an underscore, the option. A prefix nobody armed is ignored rather than refused." | The prefix convention is used at the call sites: `tests/core/test_plugins.py:80` "@route(openapi_method=\"delete\")", `src/genro_asgi/applications/configuration_profiles.py:95` "@route(openapi_method=\"post\", channel_channels=\"mcp,rest\")", `src/genro_asgi_server_app/server_sections/tokens_section.py:91` "@route(auth_rule=\"SUPERADMIN\", openapi_method=\"post\")" · `tests/core/test_plugins.py::test_method_override_from_handler_config`. The parsing of the prefix and the ignoring of an unarmed one belong to genro-routes, outside this repository: no code here reads or refuses a prefix | `src/genro_asgi/plugins/openapi/plugin.py:21-30` lists the accepted config keys (``enabled``, ``method``, ``tags``, ``summary``, ``description``, ``deprecated``, ``security_scheme``, ``security``); `src/genro_asgi/routed_application.py:22-23` "entries declaring ``auth_rule`` are filtered by the request's authorization tags". No docstring states that an unarmed prefix is ignored rather than refused | `temp/handoff_2026-07-22.md` (2026-07-22) "`@route(openapi_method=\"post\")` così la…" | SILENT | SILENT |
| 5 | §3: "A handler stays **pure**: the options are about the route, not about the computation, and no handler body reads its own options. And a tree remains readable by a consumer that does not exist yet" | `src/genro_asgi/plugins/openapi/translator.py:164` "openapi_config = metadata.get(\"plugin_config\", {}).get(\"openapi\", {})" — the reader takes the options from the description, never from the handler body · `tests/core/test_plugins.py::test_translator_module_imports_no_pydantic`, `tests/core/test_plugins.py::test_method_override_from_handler_config` | `src/genro_asgi/plugins/openapi/translator.py:17-18` "Reads the dialect-neutral description produced by ``router.nodes()`` and NEVER re-derives anything from the callables." | SILENT | SILENT | SILENT |
| 6 | §4: "tags (`auth_rule`) — who is calling; capabilities (`env_requires`) — what this installation is able to do, **accumulated down the tree** …; channel (`channel_channels`) — the surface the request arrived through … **all three are answered during the walk rather than after it**" | `src/genro_asgi/routed_application.py:246-257` `auth_filters` returns `{"auth_tags": ",".join(avatar.tags)}` and it is passed into the walk at `src/genro_asgi/routed_application.py:222`; the channel axis is passed by the MCP face (`src/genro_asgi/applications/mcp.py:34-36`). The `env_requires` axis appears in this repository only as published schema metadata, `src/genro_asgi/plugins/openapi/translator.py:245-247` "env_requires = env_config.get(\"requires\", \"\") … operation[\"x-requires\"] = env_requires"; no code here supplies an env filter to the walk, and the accumulation down the tree is genro-routes' · `tests/core/test_routed_application.py::test_anonymous_is_401_on_ruled_entry`, `tests/core/test_routed_application.py::test_wrong_tags_are_403`, `tests/core/test_mcp_application.py::test_rest_only_absent_from_tools_list`. No test in this repository exercises `env_requires` as a filter | `src/genro_asgi/routed_application.py:246-257` "Auth filters for node resolution, from the scope identity. An ``Avatar`` on ``scope[\"auth\"]`` becomes the comma-separated ``auth_tags`` the auth plugin evaluates entry rules against."; `src/genro_asgi/applications/mcp.py:27-31` "The ``channel`` plugin drives per-face visibility: a method is an MCP tool only on channel ``\"mcp\"``". The accumulation of `env_requires` down the tree is documented in no docstring of `src/` | SILENT | SILENT | SILENT |
| 7 | §4, Invariant 3: "A node that exists but is withheld answers with its own status and never falls through to something else." | `src/genro_asgi/routed_application.py:132-137` "ROUTER_ERRORS: dict[str, type[Exception]] = {\"not_found\": HTTPNotFound, \"not_available\": HTTPNotFound, \"not_authorized\": HTTPForbidden, \"not_authenticated\": HTTPUnauthorized}" — the resolution error code becomes the answer, with no fall-through branch · `tests/core/test_routed_application.py::test_anonymous_is_401_on_ruled_entry`, `tests/core/test_routed_application.py::test_wrong_tags_are_403`, `tests/core/test_routed_application.py::test_ruled_entry_denied_without_middleware` | `src/genro_asgi/routed_application.py:37-41` "a ruled entry denied with no identity → ``HTTPUnauthorized``, with an identity whose tags do not match → ``HTTPForbidden``" | SILENT | SILENT | SILENT |
| 8 | §4: "**Channel is what makes one tree serve several consumers** … the tool face walks the tree with its own channel set, so a route marked for one surface does not appear on the other." | `src/genro_asgi/applications/mcp.py:334` "class McpOpenApiApplication(OpenApiApplication):" with `src/genro_asgi/applications/mcp.py:348-349` "mcp_channel: ClassVar[str] = \"mcp\"" and "rest_channel: ClassVar[str] = \"rest\"", and the REST face's own filter at `src/genro_asgi/applications/mcp.py:402` "return {\"channel_channel\": self.rest_channel}" — one router, two faces; the declarations are written beside the routes, `src/genro_asgi/applications/configuration_profiles.py:67` "@route(channel_channels=\"mcp,rest\")" · `tests/core/test_mcp_application.py::test_dual_method_same_result_on_both_faces`, `tests/core/test_mcp_application.py::test_rest_only_absent_from_tools_list`, `tests/core/test_mcp_application.py::test_mounted_router_tools_and_rest` | `src/genro_asgi/applications/mcp.py:27-31` "Visibility is the ``channel`` plugin's job: the MCP face lists only channel-``\"mcp\"`` entries; undeclared methods default to REST-only (``channels=rest_channel``)." | `temp/cronologia_2026-08-23_2026-09-12.md` (2026-08-23) "#64 (09-05): il metodo JSON-RPC di MCP si risolve su un albero genro-routes." | SILENT | SILENT |
| 9 | §4: "the **HTTP dispatch passes no channel at all**. Every route is reachable over HTTP unless its tags say otherwise" | `src/genro_asgi/routed_application.py:222` "node = self.route.node(request.path, errors=errors, **self.auth_filters(scope))" and `src/genro_asgi/routed_application.py:254-257` "avatar = scope.get(\"auth\") / if avatar is None: return {} / return {\"auth_tags\": \",\".join(avatar.tags)}" — the only filter reaching the HTTP walk is `auth_tags` · `tests/core/test_mcp_application.py::test_openapi_schema_still_serves`. No test asserts that a channel-declared route stays reachable over HTTP | SILENT (no docstring states the HTTP walk passes no channel; `src/genro_asgi/routed_application.py:246-249` documents only the auth filter) | SILENT | SILENT | SILENT |
| 10 | §5, D22: "A tree describes itself — nodes, declared parameters, options beside them, branches below — in terms that name no protocol … a new face is a new reader and never a new obligation on the routes." | Two readers of one description exist: `src/genro_asgi/plugins/openapi/translator.py:164` reads `metadata["plugin_config"]` from `router.nodes()`, and `src/genro_asgi/mcp/engine.py:105` "class McpEngine:" builds tools from the same tree · `tests/core/test_plugins.py::test_flat_paths_carry_operations_and_schemas`, `tests/core/test_plugins.py::test_translator_module_imports_no_pydantic`, `tests/core/test_mcp_engine.py::test_input_schema_fallback_assembles_from_fields` | `src/genro_asgi/plugins/openapi/translator.py:17-31` "Reads the dialect-neutral description produced by ``router.nodes()`` … this translator only reads those neutral blocks"; `src/genro_asgi/mcp/engine.py:18-20` "The engine turns a Router's ``@route`` entries into MCP tools and serves the protocol methods. It is transport- and app-agnostic" | SILENT | SILENT | SILENT |
| 11 | §6, D17/D2: "The capability arrives as a **mixin composed before the server class** … a composition without the mixin **exposes no arming surface at all**, and a routed application on such a server runs with only what it armed for itself. That is a working server, not a degraded one." | `src/genro_asgi/plugin_mixin.py:74` "class PluginMixin:" with the cooperative `__init__` at `src/genro_asgi/plugin_mixin.py:86-94` and `arm_router` at `src/genro_asgi/plugin_mixin.py:130`; the app arms its own `auth` at `src/genro_asgi/routed_application.py:143` "self.route.plug(\"auth\")" · `tests/core/test_plugins.py::test_arms_enabled_plugin_on_router`, `tests/core/test_plugins.py::test_unmounted_app_arms_nothing_extra` ("app = ApiApp()  # no server → only the auth plug from __init__") | `src/genro_asgi/plugin_mixin.py:17-19` "The base server knows nothing about router plugins. This mixin adds them as a capability, composed BEFORE the server class"; `src/genro_asgi/plugin_mixin.py:32-33` "A composition WITHOUT this mixin exposes no ``arm_router`` and arms nothing — ``RoutedApplication`` degrades silently." | SILENT | SILENT | DIVERGE |
| 12 | §7, owner 2026-08-23: "A **middleware** wraps the dispatch … A **plugin** is armed on the routing tree of one application: it sees the tree's own description and no traffic at all." | `src/genro_asgi/plugin_mixin.py:130-148` `arm_router(self, router: Router)` takes a router and ends at "router.plug(specs)" — it never sees a request; the arming call site is the app's own router, `src/genro_asgi/routed_application.py:143` "self.route.plug(\"auth\")" · `tests/core/test_plugins.py::test_arms_enabled_plugin_on_router`, `tests/core/test_plugins.py::test_bundled_plugin_plugged_by_name` | `src/genro_asgi/plugin_mixin.py:25-27` "Unlike the middleware chain — assembled once around the base dispatch — plugins are armed onto the ROUTER of each routed application: ``arm_router`` is called by ``RoutedApplication`` on first ``route`` access." | SILENT | SILENT | SILENT |
| 13 | §8: "Importing this package registers **no plugin** against the routing library. Registration is an explicit act performed while arming, and the default mapping of codes to classes is produced by a call rather than held at module level" | `src/genro_asgi/plugin_mixin.py:64-71` "def default_plugin_registry() -> dict[str, type[BasePlugin]]: … return {\"openapi\": OpenAPIPlugin}" — a function, no module-level dict; registration happens inside `arm_router`, `src/genro_asgi/plugin_mixin.py:143-144` "if cls is not None and name not in Router.available_plugins(): Router.register_plugin(cls)" · `tests/core/test_plugins.py::test_importing_the_package_does_not_register_openapi` (a subprocess asserting "'openapi' not in Router.available_plugins()"), `tests/core/test_plugins.py::test_default_registry_is_fresh_each_call`, `tests/core/test_plugins.py::test_arming_registers_the_plugin_in_the_router_registry` | `src/genro_asgi/plugin_mixin.py:35-38` "``default_plugin_registry()`` returns a FRESH dict per call … a function, so no module-level mutable registry exists; and importing this module never registers a plugin against genro-routes (no import side effect)."; `src/genro_asgi/plugins/openapi/plugin.py:35-37` "this file does NOT register itself against genro-routes at import time (the no-global-state / no-import-side-effect rule)" | SILENT | SILENT | SILENT |
| 14 | §8: "What it does not decide is **which class a code resolves to** once armed: that is the routing library's own registry, it is process-wide, and the first arming wins it." | `src/genro_asgi/plugin_mixin.py:143-144` "if cls is not None and name not in Router.available_plugins(): Router.register_plugin(cls)" — a name already in the process-wide `Router` registry is left as it is, and the second class is dropped without a word · `tests/core/test_plugins.py::test_arming_registers_the_plugin_in_the_router_registry`. No test exercises two classes under one code | `src/genro_asgi/plugin_mixin.py:133-135` "A plugin carrying a class in the registry is registered with genro-routes first (guarded — never re-registered)"; the docstring states neither that the registry is process-wide nor that the first arming wins | SILENT | SILENT | SILENT |
| 15 | §9, D26: "`pydantic` and `openapi` became FIXED server structure (armed on every router), so per-entry OpenAPI controls always apply … asking to disable one of the two is an **error**, not an opt-out." | `src/genro_asgi/plugin_mixin.py:61` "FIXED_PLUGINS: tuple[str, ...] = (\"pydantic\", \"openapi\")"; `src/genro_asgi/plugin_mixin.py:108` "resolved: dict[str, dict[str, Any]] = {name: {} for name in FIXED_PLUGINS}"; `src/genro_asgi/plugin_mixin.py:110-111` "if not value and name in FIXED_PLUGINS: raise ValueError(f\"Plugin '{name}' is fixed structure and cannot be disabled\")" · `tests/core/test_plugins.py::test_disabling_a_fixed_plugin_is_a_config_error`, `tests/core/test_plugins.py::test_no_plugins_config_still_arms_the_fixed_base`, `tests/core/test_plugins.py::test_an_extra_name_is_retained_beside_the_fixed_base` | `src/genro_asgi/plugin_mixin.py:55-60` "The plugins every server arms unconditionally — the fixed structure of a routed core, not a config choice … The ``plugins`` config section only ADDS extras over this base; disabling a fixed plugin is a config error, not an opt-out." | SILENT | SILENT | SILENT |
| 16 | §10, owner 2026-08-23: "Arming therefore happens at the **first look at the tree after installation** … Repeating it is safe, and by design rather than by luck: a tree already carrying a plugin does not receive it a second time, and a class the routing library already knows is not registered again." | `src/genro_asgi/plugin_mixin.py:139` "plugged = {plugin.name for plugin in router.iter_plugins()}" with `src/genro_asgi/plugin_mixin.py:145-146` "if name not in plugged: specs.append({\"name\": name, **options})", and the registration guard at `src/genro_asgi/plugin_mixin.py:143` · `tests/core/test_plugins.py::test_arming_twice_is_a_no_op` ("assert names.count(\"openapi\") == 1"), `tests/core/test_plugins.py::test_mounted_app_arms_on_first_route_access` | `src/genro_asgi/plugin_mixin.py:131-137` "Register and plug every enabled plugin onto ``router`` (idempotent). A plugin carrying a class in the registry is registered with genro-routes first (guarded — never re-registered); the not-yet-attached plugins are then armed with a single batch ``router.plug([...])`` call, so arming twice is safe"; `src/genro_asgi/routed_application.py:26-28` "The config-driven plugins … are armed LAZILY: on the first ``route`` access made after the app is attached to a server" | SILENT | SILENT | SILENT |
| 17 | §10: "**a server that has finished booting has armed nothing yet.** The set arrives when something first looks." | `src/genro_asgi/routed_application.py:140` "self._armed: bool = False" set in `__init__`, the arming deferred to the first `route` access · `tests/core/test_plugins.py::test_unmounted_app_arms_nothing_extra`, `tests/core/test_plugins.py::test_mounted_app_arms_on_first_route_access`. No test inspects a booted server before the first look | `src/genro_asgi/routed_application.py:26-29` "armed LAZILY: on the first ``route`` access made after the app is attached to a server, the app calls ``server.arm_router(self.route)`` (once, guarded)" | SILENT | SILENT | SILENT |
| 18 | §11, owner 2026-08-23: "A code nobody can resolve **stops the arming with an error naming it and listing what is available**. It is never a silent no-op" | `src/genro_asgi/plugin_mixin.py:147-148` "if specs: router.plug(specs)" — an unknown code reaches `router.plug` and raises; the text of the error is genro-routes', outside this repository · `tests/core/test_plugins.py::test_unknown_plugin_name_raises` asserts `pytest.raises(ValueError)` only, not that the message lists the available codes | `src/genro_asgi/plugin_mixin.py:136-137` "Unknown plugin names surface as ``router.plug`` errors, not silent no-ops." The docstring does not state that the error lists the available codes | SILENT | SILENT | SILENT |
| 19 | §12, D22: "The routing library ships the five plugins that are about *routing* — authorization, signature reading, logging, and two more. A **dialect** … lives here … There is one dialect plugin today, and a second face reads the same neutral description without being a plugin at all." | `src/genro_asgi/plugin_mixin.py:71` "return {\"openapi\": OpenAPIPlugin}" — one dialect plugin; the second face is `src/genro_asgi/mcp/engine.py:105` "class McpEngine:", which is no plugin and is absent from the registry · `tests/core/test_plugins.py::test_default_registry_holds_openapi`, `tests/core/test_plugins.py::test_bundled_plugin_plugged_by_name`, `tests/core/test_mcp_engine.py::test_input_schema_fallback_assembles_from_fields` | `src/genro_asgi/plugin_mixin.py:30-32` "bundled genro-routes plugins (auth/channel/env/logging/pydantic) carry no class in the registry and are plugged by name alone"; `src/genro_asgi/plugin_mixin.py:35-36` "``{\"openapi\": OpenAPIPlugin}`` as of Phase 5"; `src/genro_asgi/mcp/engine.py:19-20` "It is transport- and app-agnostic" | SILENT | SILENT | SILENT |
| 20 | friction (follow-up 2026-09-08): "The current status explains the server fixed pair versus application auth arming and the two OpenAPI metadata paths. The documentation gap is addressed; design choices about registry collisions, per-application plugin scope and HTTP method enforcement are not decided by this editorial clarification." | The three arming moments are in the code: `src/genro_asgi/plugin_mixin.py:61` "FIXED_PLUGINS: tuple[str, ...] = (\"pydantic\", \"openapi\")" (server), `src/genro_asgi/routed_application.py:143` "self.route.plug(\"auth\")" (application), `src/genro_asgi/plugin_mixin.py:130` `arm_router` (extras). The two OpenAPI metadata paths are `src/genro_asgi/plugins/openapi/plugin.py:93` "return {\"openapi\": metadata} if metadata else {}" and `src/genro_asgi/plugins/openapi/translator.py:164` "metadata.get(\"plugin_config\", {}).get(\"openapi\", {})" · `tests/core/test_plugins.py::test_no_plugins_config_still_arms_the_fixed_base` | `src/genro_asgi/plugin_mixin.py:55-60` (the fixed pair) and `src/genro_asgi/routed_application.py:20-22` "plugs the ``auth`` plugin on the app router" | SILENT | SILENT | SILENT |
| 21 | friction (follow-up 2026-09-08): "Old coverage percentages and fixed call-site counts are archived observations, not new coverage evidence." | SILENT (an editorial statement about earlier measurements, with no code counterpart) | SILENT (not a docstring matter) | SILENT | SILENT | SILENT |
| 22 | friction S2 [silent]: "two servers in one process, one plugin code, two classes: the first one wins, and nobody is told. The registration behind a code is the routing library's, and it is **class-level and process-wide**." | `src/genro_asgi/plugin_mixin.py:141-144` "for name, options in self._plugins_config.items(): cls = self._plugin_registry.get(name) / if cls is not None and name not in Router.available_plugins(): Router.register_plugin(cls)" — the second class is skipped with no branch that warns, logs or raises · `tests/core/test_plugins.py::test_arming_registers_the_plugin_in_the_router_registry` is the only test touching the process-wide registry; no test covers the collision | `src/genro_asgi/plugin_mixin.py:133-135` "A plugin carrying a class in the registry is registered with genro-routes first (guarded — never re-registered)" | SILENT | SILENT | SILENT |
| 23 | friction S3 [undocumented]: "a plugin of one's own cannot be declared in a configuration. The class travels as a construction argument, and the configuration has no counterpart for it: the section names codes only." | `src/genro_asgi/config/elements.py:408-411` "def plugin(self, code: str = None, enabled: bool = True, **options: Any) -> None:" — the grammar takes a code, never a class; the class enters only as a constructor kwarg, `src/genro_asgi/plugin_mixin.py:88` "extra: dict[str, type[BasePlugin]] | None = kwargs.pop(\"plugin_registry\", None)" · `tests/core/test_plugins.py::test_registry_extension_arms_custom_plugin` builds the server in Python; `tests/core/test_plugins.py::test_plugin_options_map_to_a_dict` covers the configuration path, which carries options only | `src/genro_asgi/config/elements.py:409-411` the `plugins` section declares "``enabled`` (set False to leave it unarmed) and arbitrary options handed to ``router.plug(code, **options)``"; `src/genro_asgi/plugin_mixin.py:21-22` "``plugin_registry=`` (extra ``{name: class}`` entries merged over ``default_plugin_registry()``)" stays a constructor kwarg | `.subtasks/config-always/finaldoc.md` (2026-09-12) "`middleware_registry`, `plugin_registry` … they REGISTER classes, they do not switch anything; the verdict's own wording (\"names registered from outside enter as free attributes\") puts the switch in the tree and leaves the registration in code" | SILENT | SILENT |
| 24 | friction S4 [unread]: "the shipped dialect plugin computes a block nobody reads … Its `entry_metadata` repackages the per-route publishing options and the description carries the result under the plugin's own code. The OpenAPI reader does not look there" | The two keys are in the code, and they are different: `src/genro_asgi/plugins/openapi/plugin.py:73-93` "def entry_metadata(self, router: Any, entry: MethodEntry) -> dict[str, Any]: … return {\"openapi\": metadata} if metadata else {}" against `src/genro_asgi/plugins/openapi/translator.py:164` "openapi_config = metadata.get(\"plugin_config\", {}).get(\"openapi\", {})" — the translator reads `plugin_config`, never the `entry_metadata` block · only the method override is exercised, `tests/core/test_plugins.py::test_method_override_from_handler_config`; `tests`-wide there is no test for `tags`, `summary`, `description`, `deprecated`, `security_scheme` or the explicit `security` override | `src/genro_asgi/plugins/openapi/plugin.py:73-74` "Provide OpenAPI-specific metadata for a handler."; `src/genro_asgi/plugins/openapi/translator.py:30-31` "``security`` / ``x-requires`` come from the per-entry ``auth`` / ``env`` plugin config" — the translator names no `entry_metadata` as its source | `temp/handoff_2026-07-21.md` (2026-07-21) "Opzioni plug openapi da config = no-op silenzioso (il translator legge solo la config per-handler del decoratore; `entry_metadata` mai letto) — decidere la precedenza router-config vs decoratore." | SILENT | SILENT |
| 25 | friction S5 [unratified]: "plugins are server-wide, and nothing records the decision. … A site cannot arm one plugin on one application only" | `src/genro_asgi/plugin_mixin.py:87` "plugins: dict[str, bool | dict[str, Any]] | None = kwargs.pop(\"plugins\", None)" — one switch set, held on the server; `src/genro_asgi/plugin_mixin.py:141` "for name, options in self._plugins_config.items():" applies it to every router handed to `arm_router`, with no application key anywhere in the signature `arm_router(self, router: Router)` · `tests/core/test_plugins.py::test_configured_server_arms_the_routed_app`. No test arms a plugin on one application only | `src/genro_asgi/plugin_mixin.py:25-27` "plugins are armed onto the ROUTER of each routed application: ``arm_router`` is called by ``RoutedApplication`` on first ``route`` access" — the configuration read is the server's (`src/genro_asgi/plugin_mixin.py:19-21` "peels ``plugins=``"), with no per-application distinction | SILENT | SILENT | SILENT |
| 26 | friction S6 [cross · unratified]: "the fixed pair is stated in two entries and armed in three moments. … the third always-present plugin — authorization, which the application arms for itself" | Three moments in the code: `src/genro_asgi/plugin_mixin.py:61` "FIXED_PLUGINS: tuple[str, ...] = (\"pydantic\", \"openapi\")", `src/genro_asgi/routed_application.py:143` "self.route.plug(\"auth\")", `src/genro_asgi/plugin_mixin.py:148` "router.plug(specs)" · `tests/core/test_plugins.py::test_no_plugins_config_still_arms_the_fixed_base` asserts the three names together: "{\"auth\", \"pydantic\", \"openapi\"}" | `src/genro_asgi/plugin_mixin.py:55-61` (the fixed pair) and `src/genro_asgi/routed_application.py:20-22` "plugs the ``auth`` plugin on the app router, so entries declaring ``auth_rule`` are filtered by the request's authorization tags". No docstring states the division as a rule | SILENT | SILENT | SILENT |
| 27 | friction S7 [silent]: "a published verb is a description that nothing enforces. A route declaring itself a `DELETE` is published as one, and the dispatch does not read the verb: the same route answers a `GET` that reaches it." | `src/genro_asgi/routed_application.py:222` "node = self.route.node(request.path, errors=errors, **self.auth_filters(scope))" — the dispatch resolves on the path and the auth tags; the word "method" appears nowhere in `src/genro_asgi/routed_application.py` outside the module docstring, so no verb gate exists. The verb is published at `src/genro_asgi/plugins/openapi/translator.py:164` from the per-entry config · `tests/core/test_plugins.py::test_method_override_from_handler_config` proves the publication; no test asserts, in either direction, what the dispatch does with a mismatched verb | `src/genro_asgi/plugins/openapi/translator.py:24-26` "The HTTP method is guessed from the per-parameter schemas (all scalar → GET, else POST)". No docstring declares the verb descriptive | SILENT | SILENT | SILENT |
| 28 | "Settled on 2026-08-24 — where the essentials of routing live. They live here: this entry was `025_plugins` and became the routing system, with the plugins after the routing rather than instead of it, on the owner's decision." | SILENT (a renaming of a documentation entry, with no code counterpart) | SILENT (not a docstring matter) | SILENT | `f8c16f5f-78f5-495a-9eef-1d6bbcfaf77d.jsonl` (2026-08-23, assistant) "i comandi in [090 server-application](internals/10_server/090_server-application/) come settima sezione" — no turn records the renaming of `025_plugins` into `025_routing-system` | SILENT |

## Divergences

### #1
- decisions.md says: "They are explained **here**, after applications rather than before, on a reading order the owner chose."
- the implementation does: composes genro-routes and adds one dialect (`src/genro_asgi/plugin_mixin.py:71` "return {\"openapi\": OpenAPIPlugin}"), which agrees; the reading order of the documentation has no code counterpart.
- the docstring says: `src/genro_asgi/plugins/openapi/__init__.py:22-23` "It lives here, not in the routing core, because OpenAPI is one transport dialect among peers (alongside MCP), not a routing concern."
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #2
- decisions.md says: "An application page that also explained trees would be two subjects in one, which is what it was until this entry took the second."
- the implementation is silent: documentation organization has no code counterpart.
- no docstring speaks of it.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #3
- decisions.md says: "a route cannot drift from its handler, because there is no second place where the route is written."
- the implementation does: `src/genro_asgi/routed_application.py:115` "class RoutedApplication(BaseApplication, RoutingClass):" and `src/genro_asgi/routed_application.py:222` "node = self.route.node(request.path, errors=errors, **self.auth_filters(scope))"; no path table exists in the package.
- the docstring says: `src/genro_asgi/routed_application.py:17-19` "handlers are ``@route``-decorated methods on subclasses".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #4
- decisions.md says: "A prefix nobody armed is ignored rather than refused."
- the implementation is silent on this half: the prefix is used at the call sites (`tests/core/test_plugins.py:80` "@route(openapi_method=\"delete\")"), and its parsing belongs to genro-routes, outside this repository.
- the docstring says: `src/genro_asgi/plugins/openapi/plugin.py:21-30` lists the accepted config keys, and states nothing about an unarmed prefix.
- the handoff of 2026-07-22 (`temp/handoff_2026-07-22.md`) says: "`@route(openapi_method=\"post\")` così la…".
- no transcript speaks of it.

### #5
- decisions.md says: "no handler body reads its own options. And a tree remains readable by a consumer that does not exist yet".
- the implementation does: `src/genro_asgi/plugins/openapi/translator.py:164` "openapi_config = metadata.get(\"plugin_config\", {}).get(\"openapi\", {})" — the reader takes the options from the description.
- the docstring says: `src/genro_asgi/plugins/openapi/translator.py:17-18` "NEVER re-derives anything from the callables."
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #6
- decisions.md says: "capabilities (`env_requires`) — what this installation is able to do, **accumulated down the tree** … all three are answered during the walk rather than after it".
- the implementation does: it supplies the auth axis at `src/genro_asgi/routed_application.py:222` and the channel axis from the MCP face; `env_requires` appears in this repository only as published metadata, `src/genro_asgi/plugins/openapi/translator.py:245-247` "operation[\"x-requires\"] = env_requires". No test in this repository exercises `env_requires` as a filter.
- the docstring says: `src/genro_asgi/routed_application.py:246-249` documents the auth filter only; the accumulation down the tree is documented in no docstring of `src/`.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #7
- decisions.md says: "A node that exists but is withheld answers with its own status and never falls through to something else."
- the implementation does: `src/genro_asgi/routed_application.py:132-137` maps the resolution codes one to one onto `HTTPNotFound`/`HTTPForbidden`/`HTTPUnauthorized`, with no fall-through branch.
- the docstring says: `src/genro_asgi/routed_application.py:37-41` "a ruled entry denied with no identity → ``HTTPUnauthorized``, with an identity whose tags do not match → ``HTTPForbidden``".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #8
- decisions.md says: "the tool face walks the tree with its own channel set, so a route marked for one surface does not appear on the other."
- the implementation does: `src/genro_asgi/applications/mcp.py:348-349` "mcp_channel: ClassVar[str] = \"mcp\"" beside "rest_channel: ClassVar[str] = \"rest\"", proved by `tests/core/test_mcp_application.py::test_rest_only_absent_from_tools_list`.
- the docstring says: `src/genro_asgi/applications/mcp.py:27-31` "the MCP face lists only channel-``\"mcp\"`` entries".
- the handoff of 2026-08-23 (`temp/cronologia_2026-08-23_2026-09-12.md`) says: "#64 (09-05): il metodo JSON-RPC di MCP si risolve su un albero genro-routes."
- no transcript speaks of it.

### #9
- decisions.md says: "the **HTTP dispatch passes no channel at all**."
- the implementation does: `src/genro_asgi/routed_application.py:254-257` returns `{}` or `{"auth_tags": …}` and nothing else reaches the walk at `src/genro_asgi/routed_application.py:222`.
- no docstring states it: `src/genro_asgi/routed_application.py:246-249` documents the auth filter alone.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #10
- decisions.md says: "a new face is a new reader and never a new obligation on the routes."
- the implementation does: two readers of one description, `src/genro_asgi/plugins/openapi/translator.py:164` and `src/genro_asgi/mcp/engine.py:105`.
- the docstring says: `src/genro_asgi/mcp/engine.py:19-20` "It is transport- and app-agnostic".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #11
- decisions.md says: "a routed application on such a server runs with only what it armed for itself. That is a working server, not a degraded one."
- the implementation does: `src/genro_asgi/routed_application.py:143` "self.route.plug(\"auth\")" is the whole set on such a composition, and the test that fixes the behaviour names it as a shortfall: `tests/core/test_plugins.py::test_unmounted_app_arms_nothing_extra` — "app = ApiApp()  # no server → only the auth plug from __init__".
- the docstring says: `src/genro_asgi/plugin_mixin.py:32-33` "A composition WITHOUT this mixin exposes no ``arm_router`` and arms nothing — ``RoutedApplication`` degrades silently." and `src/genro_asgi/routed_application.py:28-29` "the app degrades to the ``auth`` plug alone." The code describes the same composition as a silent degradation; `decisions.md` declares it explicitly not degraded.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #12
- decisions.md says: "A **plugin** is armed on the routing tree of one application: it sees the tree's own description and no traffic at all."
- the implementation does: `src/genro_asgi/plugin_mixin.py:130` "def arm_router(self, router: Router) -> None:" takes a router and never a request.
- the docstring says: `src/genro_asgi/plugin_mixin.py:25-27` "Unlike the middleware chain — assembled once around the base dispatch — plugins are armed onto the ROUTER of each routed application".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #13
- decisions.md says: "Importing this package registers **no plugin** against the routing library."
- the implementation does: `src/genro_asgi/plugin_mixin.py:64-71` is a function returning a fresh dict, and `tests/core/test_plugins.py::test_importing_the_package_does_not_register_openapi` asserts it in a subprocess.
- the docstring says: `src/genro_asgi/plugin_mixin.py:35-38` "returns a FRESH dict per call … no module-level mutable registry exists".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #14
- decisions.md says: "that is the routing library's own registry, it is process-wide, and the first arming wins it."
- the implementation does: `src/genro_asgi/plugin_mixin.py:143-144` "if cls is not None and name not in Router.available_plugins(): Router.register_plugin(cls)".
- the docstring says: `src/genro_asgi/plugin_mixin.py:133-135` "(guarded — never re-registered)", and states neither the process-wide scope nor the first-arming rule.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #15
- decisions.md says: "asking to disable one of the two is an **error**, not an opt-out."
- the implementation does: `src/genro_asgi/plugin_mixin.py:110-111` "raise ValueError(f\"Plugin '{name}' is fixed structure and cannot be disabled\")", proved by `tests/core/test_plugins.py::test_disabling_a_fixed_plugin_is_a_config_error`.
- the docstring says: `src/genro_asgi/plugin_mixin.py:58-60` "disabling a fixed plugin is a config error, not an opt-out."
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #16
- decisions.md says: "a tree already carrying a plugin does not receive it a second time, and a class the routing library already knows is not registered again."
- the implementation does: `src/genro_asgi/plugin_mixin.py:139` and `src/genro_asgi/plugin_mixin.py:145-146` skip the already-plugged names, proved by `tests/core/test_plugins.py::test_arming_twice_is_a_no_op`.
- the docstring says: `src/genro_asgi/plugin_mixin.py:131-137` "(idempotent) … so arming twice is safe".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #17
- decisions.md says: "a server that has finished booting has armed nothing yet."
- the implementation does: `src/genro_asgi/routed_application.py:140` "self._armed: bool = False" and the lazy call on first `route` access; no test inspects a booted server before the first look.
- the docstring says: `src/genro_asgi/routed_application.py:26-29` "armed LAZILY: on the first ``route`` access made after the app is attached to a server".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #18
- decisions.md says: "stops the arming with an error naming it and listing what is available."
- the implementation does: `src/genro_asgi/plugin_mixin.py:147-148` hands the unknown name to `router.plug`, which raises; the message is genro-routes'. `tests/core/test_plugins.py::test_unknown_plugin_name_raises` asserts the exception type only.
- the docstring says: `src/genro_asgi/plugin_mixin.py:136-137` "Unknown plugin names surface as ``router.plug`` errors, not silent no-ops." — it does not state the listing.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #19
- decisions.md says: "There is one dialect plugin today, and a second face reads the same neutral description without being a plugin at all."
- the implementation does: `src/genro_asgi/plugin_mixin.py:71` holds one dialect class, and `src/genro_asgi/mcp/engine.py:105` "class McpEngine:" is absent from the registry.
- the docstring says: `src/genro_asgi/plugin_mixin.py:30-32` "bundled genro-routes plugins (auth/channel/env/logging/pydantic) carry no class in the registry and are plugged by name alone".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #20
- decisions.md says: "design choices about registry collisions, per-application plugin scope and HTTP method enforcement are not decided by this editorial clarification."
- the implementation does: it holds the three matters open, at `src/genro_asgi/plugin_mixin.py:143`, `src/genro_asgi/plugin_mixin.py:141` and `src/genro_asgi/routed_application.py:222` respectively.
- the docstring says: `src/genro_asgi/plugin_mixin.py:55-60` and `src/genro_asgi/routed_application.py:20-22` state the arming moments and nothing about the three open choices.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #21
- decisions.md says: "Old coverage percentages and fixed call-site counts are archived observations, not new coverage evidence."
- the implementation is silent: an editorial statement about earlier measurements.
- no docstring speaks of it.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #22
- decisions.md says: "the first one wins, and nobody is told."
- the implementation does: `src/genro_asgi/plugin_mixin.py:143-144` skips the second class with no branch that warns, logs or raises; no test covers the collision.
- the docstring says: `src/genro_asgi/plugin_mixin.py:133-135` "(guarded — never re-registered)".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #23
- decisions.md says: "The class travels as a construction argument, and the configuration has no counterpart for it: the section names codes only."
- the implementation does: `src/genro_asgi/config/elements.py:408` "def plugin(self, code: str = None, enabled: bool = True, **options: Any) -> None:" takes a code, and `src/genro_asgi/plugin_mixin.py:88` takes the class as a constructor kwarg.
- the docstring says: `src/genro_asgi/config/elements.py:409-411` "arbitrary options handed to ``router.plug(code, **options)``".
- the finaldoc of 2026-09-12 (`.subtasks/config-always/finaldoc.md`) says: "`middleware_registry`, `plugin_registry` … they REGISTER classes, they do not switch anything".
- no transcript speaks of it.

### #24
- decisions.md says: "The OpenAPI reader does not look there: it reads the raw configuration from a different key of the same description."
- the implementation does: `src/genro_asgi/plugins/openapi/plugin.py:93` "return {\"openapi\": metadata} if metadata else {}" against `src/genro_asgi/plugins/openapi/translator.py:164` "openapi_config = metadata.get(\"plugin_config\", {}).get(\"openapi\", {})" — two different keys. Only `tests/core/test_plugins.py::test_method_override_from_handler_config` exercises one of the seven options.
- the docstring says: `src/genro_asgi/plugins/openapi/translator.py:30-31` "``security`` / ``x-requires`` come from the per-entry ``auth`` / ``env`` plugin config" — no mention of `entry_metadata`.
- the handoff of 2026-07-21 (`temp/handoff_2026-07-21.md`) says: "Opzioni plug openapi da config = no-op silenzioso (il translator legge solo la config per-handler del decoratore; `entry_metadata` mai letto)".
- no transcript speaks of it.

### #25
- decisions.md says: "A site cannot arm one plugin on one application only".
- the implementation does: `src/genro_asgi/plugin_mixin.py:87` holds one switch set on the server and `src/genro_asgi/plugin_mixin.py:141` applies it to every router; `arm_router(self, router: Router)` carries no application key.
- the docstring says: `src/genro_asgi/plugin_mixin.py:25-27` "plugins are armed onto the ROUTER of each routed application".
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #26
- decisions.md says: "the fixed pair is stated in two entries and armed in three moments."
- the implementation does: `src/genro_asgi/plugin_mixin.py:61`, `src/genro_asgi/routed_application.py:143` and `src/genro_asgi/plugin_mixin.py:148`; `tests/core/test_plugins.py::test_no_plugins_config_still_arms_the_fixed_base` asserts the three names together.
- the docstring says: `src/genro_asgi/routed_application.py:20-22` "plugs the ``auth`` plugin on the app router"; no docstring states the division as a rule.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #27
- decisions.md says: "the dispatch does not read the verb: the same route answers a `GET` that reaches it."
- the implementation does: `src/genro_asgi/routed_application.py:222` resolves on path and auth tags, and no verb gate exists in `src/genro_asgi/routed_application.py`; no test asserts the behaviour in either direction.
- the docstring says: `src/genro_asgi/plugins/openapi/translator.py:24-26` "The HTTP method is guessed from the per-parameter schemas (all scalar → GET, else POST)"; no docstring declares the verb descriptive.
- no handoff or finaldoc speaks of it.
- no transcript speaks of it.

### #28
- decisions.md says: "this entry was `025_plugins` and became the routing system, with the plugins after the routing rather than instead of it, on the owner's decision."
- the implementation is silent: a renaming of a documentation entry.
- no docstring speaks of it.
- no handoff or finaldoc speaks of it.
- the transcript `f8c16f5f-78f5-495a-9eef-1d6bbcfaf77d.jsonl` of 2026-08-23 (assistant) says: "i comandi in [090 server-application](internals/10_server/090_server-application/) come settima sezione" — no turn records the renaming itself.

