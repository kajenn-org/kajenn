<!-- generated from plugin-configuration.xml by xmldocs: do not edit, edit the XML -->

## Plugin configuration of routes in the recipe
*Proposal · proposal/plugin-configuration · 2026-11-15 · draft*

### Problem

The code of an application sets the plugin options of each route in `@route(...)`: the `auth` rule, the `channel` channels, the `openapi` options, the `env` requirements. Every installation of that application gets the same values.

An installation cannot change them. The `plugins` section of the recipe switches plugins on for all the applications together and does not address a route. Changing an option at runtime would need a management application that does not exist.

Each installation must be able to decide the rules of its routes in its recipe, without a management application.

### Current state

#### kajenn today

- `READ` `src/kajenn/routed_application.py:180` · 2026-11-15 — `RoutedApplication.__init__` plugs `auth`, `channel`, `pydantic` and `openapi` on the application router. The only option it sets is `channels` at router level. Nothing is read from the recipe.
- `READ` `src/kajenn/config/elements.py:440` · 2026-11-15 — The `plugins` section of the recipe has `plugin` children. Each one switches a plugin on and carries the options handed to `router.plug(code, **options)`. The section belongs to the server and applies to every routed application.
- `READ` `src/kajenn/routed_application.py:228` · 2026-11-15 — The plugins of the `plugins` section are plugged by `server.arm_router` on the first access to `route` made once a server owns the application.
- `READ` `src/kajenn/application.py:133` · 2026-11-15 — `ApplicationGrammar` is the grammar every application inherits. Its elements are `parameters`, `request` and `databases`.
- `READ` `src/kajenn/application.py:258` · 2026-11-15 — An application reads its own part of the recipe at `applications.<code>.<path>`.
- `READ` `src/kajenn/config/handler.py:299` · 2026-11-15 — The recipe can declare an `application` element with code `_server`. It customises the management application and its class must be `ServerApplication` or a subclass.
- `READ` `src/kajenn/server_app/server_app.py:210` · 2026-11-15 — `ServerApplication` is a `RoutedApplication` with code `_server`. Its sections (`users`, `tokens`, `tasks`, `monitor`, `databases`, `k8s`, `applications`) are attached with `add_branches`.
- `READ` `src/kajenn/server_app/server_app.py:252` · 2026-11-15 — The `auth` section of `_server` is attached inside the constructor, when the first login method is registered.
- `READ` `src/kajenn/lifespan.py:164` · 2026-11-15 — The lifespan startup runs `on_startup` of the applications in registration order. The activation of an application is `on_startup`, then `ready`.
- `READ` `src/kajenn/asgi_server.py:361` · 2026-11-15 — In the main process only `_server` answers at once; every other application waits for its `ready`. In the dedicated process of an application the applications answer at once.
- `READ` `src/kajenn/remote_application.py:153` · 2026-11-15 — An application declared with `spawner` is a `RemoteApplication` in the main process. The real application class is built in the spawned process, from the same recipe.
- `READ` `src/kajenn/asgi_server.py:190` · 2026-11-15 — An application added to a running server is built from its `application` node, registered and then activated.
- `READ` `src/kajenn/asgi_server.py:181` · 2026-11-15 — The configuration of a running server is a genro-bag tree. The server subscribes to its `applications` branch with `subscribe(id, insert=..., delete=...)`: a node written there adds an application, a node taken away removes it. genro-bag `subscribe` also takes `update` and `any` callbacks.
- `READ` `src/kajenn/kbus_mixin.py:202` · 2026-11-15 — A spawned process loads its configuration from a file or a template when it starts. In `kbus_mixin.py`, `spawner.py` and `remote_application.py` no code sends it a later change of the configuration.
- `READ` `src/kajenn/server.py:276` · 2026-11-15 — `register_application` is a sync method.
- `READ` `src/kajenn/routed_application.py:401` · 2026-11-15 — `discover` returns the `nodes()` tree filtered by the channel of the request and by the tags of the caller: an entry the caller may not execute is not listed.
- `READ` `src/kajenn/plugins/openapi/translator.py:230` · 2026-11-15 — The OpenAPI document derives `security` of an operation from the `auth` rule in force on the entry.
- `RUN` `src/kajenn/config/elements.py:451` · 2026-11-15 — A value written without a name in a recipe element becomes the value of its node; the keywords become its attributes. `shop.parameters("x", currency="EUR")` gives a node with value `"x"` and attribute `currency`. A node that receives children loses that value: after `cfg.plugins("x").plugin(code="logging")` the value of the `plugins` node is its children and `"x"` is gone. `plugins.plugin("logging")` raises `ValueError`: the collection key needs the attribute `code`.

#### genro-routes 0.30.1

- `READ` `genro_routes/core/routing.py:453` · 2026-11-15 — `routing.configure(target, **options)` acts on the router of one routing class. `target` is `"<plugin>"` for the router level or `"<plugin>/<selector>"` for its entries. It is also a dict with the key `target`, or a list of such dicts. `options` are the parameters of the `configure` method of that plugin.
- `RUN` `genro_routes/core/routing.py:420` · 2026-11-15 — The selector is a glob matched against the entry names of that router, several separated by commas. It does not descend into the sections. A selector that matches no entry raises `KeyError`.
- `RUN` `genro_routes/core/routing.py:488` · 2026-11-15 — A plugin that is not on the router raises `AttributeError`. An option the plugin does not know raises a pydantic `ValidationError`.
- `RUN` `genro_routes/plugins/auth.py:139` · 2026-11-15 — `routing.configure("auth/<name>", rule="")` opens an entry whose code declares an `auth_rule`. A non-empty `rule` replaces the one of the code.
- `RUN` `genro_routes/plugins/_base_plugin.py:202` · 2026-11-15 — A value written at router level (`routing.configure("auth", rule=...)`) applies to the entries that declare none. An entry that declares its own value in the code keeps it.
- `RUN` `genro_routes/core/routing.py:431` · 2026-11-15 — `routing.configure("?")` returns the description of the router: for each plugin the router-level `config` and the value in force on each entry (`overrides`), then the sections under `routers`. It reports the values in force and does not say where a value comes from. Its shape is not the shape `routing.configure` takes as input.
- `READ` `genro_routes/plugins/auth.py:112` · 2026-11-15 — Parameters of `configure`: `auth` takes `rule`, `channel` takes `channels`, `env` takes `requires`, `pydantic` takes `disabled`. The kajenn `openapi` plugin takes `method`, `tags`, `summary`, `description`, `deprecated`, `security_scheme`, `security`.
- `RUN` `genro-org/genro-routes#74` · 2026-11-15 — When the routes are resolved before a plugin class is registered, the options written in `@route(...)` for that plugin are lost. A `routing.configure` call made after the plugin is plugged writes the option and the plugin applies it.

#### applications_broadcast

- `READ` `src/kajenn/server.py:304` · 2026-11-15 — On `origin/develop` f9487ec (PR 147, issue 142) `applications_broadcast(method_name, *args, on_error=None, reverse=False, **kwargs)` is an async method of the server. It calls the `@broadcast_hook` of that name on every application, in registration order, and returns `[(code, result)]`. An application whose class has no such hook is skipped. An application that raises goes to `on_error(code, exception)`; with `on_error=None` the first exception reaches the caller.
- `READ` `src/kajenn/application.py:116` · 2026-11-15 — On `origin/develop` f9487ec a method is a broadcast hook when any definition of its name along the MRO carries `@broadcast_hook`. A route whose method has the name of a broadcast hook raises `ValueError` when the application is registered (`routed_application.py:239`).
- `READ` `src/kajenn/remote_application.py:196` · 2026-11-15 — On `origin/develop` f9487ec the `RemoteApplication` of an application declared with `spawner` forwards every `@broadcast_hook` of the real class to the spawned process with `remote_call`, a kbus call with a timeout. The names `RemoteApplication` defines itself stay local: `on_startup` waits for `joined`, `on_shutdown` does nothing, because the spawned process runs both in its own lifespan.

### Proposed solution

#### Behaviour

- The recipe of an installation names a route of an application and writes the options of one plugin for it.
- The value of the recipe replaces the value written in `@route(...)` for that route. The two are not combined.
- A route the recipe does not name keeps the value of the code.
- The recipe can close a route the code leaves open and open a route the code closes.
- Two installations of the same code have different rules.
- No management application is needed: the recipe is enough.
- A route named in the recipe that does not exist gives at most a warning. The boot goes on.
- The plugin configuration can change on a running server, like other parts of the configuration. A subscription updates the plugins when it changes.

#### Recipe

The example application `Shop` attaches a section `products` with three routes. In the code `list` has no rule; `insert` and `delete` have `auth_rule="shop_admin"`. Their URLs are `/shop/products/list`, `/shop/products/insert`, `/shop/products/delete`.

```python
shop = cfg.applications().application(code="shop", app_class=Shop)

shop_plugin_auth = shop.plugin_configuration(plugin="auth")
shop_plugin_auth.configure(route="products/list,products/insert", rule="staff")
shop_plugin_auth.configure(route="products/delete", rule="boss")

shop_plugin_channel = shop.plugin_configuration(plugin="channel")
shop_plugin_channel.configure(route="products/list", channels="api,mcp")
```

- `plugin_configuration` is an element of `ApplicationGrammar`: every application has it, `_server` included. It is written only under an `application` element.
- One `plugin_configuration` block names one plugin. The plugins are configured separately.
- Each `configure` line carries `route` and the parameters of the `configure` method of that plugin. `route` is the path of one route, or the paths of several routes separated by commas. Each path is complete, relative to the application, written as in the URL after the mount, without a leading slash.
- Every plugin present on the router of the application can be configured, a plugin registered from outside included.
- `_server` is configured by declaring `application(code="_server", app_class=ServerApplication)` in the recipe and writing `plugin_configuration` under it.

#### What kajenn calls

kajenn adds no syntax of its own for the options: each `configure` line becomes one call of `routing.configure` of genro-routes on the routing class that owns the route.

```python
shop.products.routing.configure("auth/list,insert", rule="staff")
shop.products.routing.configure("auth/delete", rule="boss")
shop.products.routing.configure("channel/list", channels="api,mcp")
```

- The plugin of the block gives the first part of the target. The path gives the routing class and the name of the entry.
- After these calls `/shop/products/list` asks for the tag `staff` and answers on the channel `mcp` too; `/shop/products/delete` asks for `boss` instead of `shop_admin`; `/shop/products/insert` asks for `staff` instead of `shop_admin`.
- A `KeyError` of `routing.configure` (no entry with that name) becomes the warning.
- A route of a section attached after the hook ran does not exist when the hook runs: its line is a line that names a route that does not exist.
- A line that names a plugin that is not on the router, or an option the plugin does not know, is not applied either. Nothing stops.

- **must-not** — kajenn adds no defensive code against errors in the plugin configuration: no boot stop, no restore of earlier values, no handling of a hook that refuses. *(Defensive code prevents only the stupid errors; the dangerous ones pass anyway. An error has consequences for who made it.)*

#### The plugin_configuration hook

- Every application has a hook `plugin_configuration`, marked `@broadcast_hook` ([kajenn-org/kajenn#142](https://github.com/kajenn-org/kajenn/issues/142)).
- The server calls it on every application with `applications_broadcast("plugin_configuration")`.
- The default does nothing. `RoutedApplication` reads its `plugin_configuration` blocks from the recipe and makes the calls above. An application with no block in the recipe does nothing.
- A subclass redefines the hook to refuse a configuration or to take the export of its router before the configuration is applied.
- No hook runs after the configuration is applied.
- For an application declared with `spawner` the hook runs in the spawned process, where the application is built. Who calls it there at boot is open (d-spawned-boot (→ d-spawned-boot)).

```python
class Shop(RoutedApplication):
    def plugin_configuration(self):
        self.code_configuration = self.routing.configure("?")
        super().plugin_configuration()
```

#### Changes on a running server

- The server calls the hook in the lifespan startup, before `on_startup`: no request arrives before the configuration.
- After the boot the `plugin_configuration` blocks of an application can change in the configuration of the running server.
- A change on a running server is a new configuration. There are no actions that insert or delete a single line: the new configuration only says which values there are.
- A subscription updates the plugins of that application with the values of the new configuration.
- To give a route its code value back, the new configuration writes that value.
- An application declared with `spawner` needs nothing specific to plugins. Any change of the configuration, of plugins or of anything else, is carried to the spawned process, which applies it to its own configuration. Its own subscriptions fire there, the one of `plugin_configuration` included.
- The hook runs before any configuration that does not come from the code: the subscription goes through the hook of that application, so the application can refuse a change made on a running server too.

#### Reading what the recipe changed

- kajenn adds no log line, no route and no discovery field for this.
- `nodes()` called with the tags of a user says which routes that user may execute. `discover` already does it.
- `routing.configure("?")` gives the values in force.
- Who wants the differences takes the export in the hook, before the configuration is applied, and keeps it. A method compares that export with the export taken when it is called. After the configuration is applied the router no longer holds the values of the code: the first export cannot be rebuilt later from the running server.

#### JSON form

A JSON form of the plugin configuration, such as the one an administration page would send, is set aside for now (owner, 2026-11-15). Its shape is not decided and it is not part of this proposal.

#### genro-routes#74

The options written by the recipe are not exposed to [genro-org/genro-routes#74](https://github.com/genro-org/genro-routes/issues/74): they are written with `routing.configure` after the plugin is on the router, not read from the decorator. The hook must run after `server.arm_router` has plugged the plugins of the `plugins` section; before that, a block naming one of them meets a router without that plugin.

### Decisions

- **d-place** `decided` — Where does the recipe hold the plugin options of the routes?
   1. **Under each application element** *(example in the full document)*
   2. **One section for the whole server** *(example in the full document)*
   3. **Both: from the server or from the application**
   → Choice: **Under each application element** (GP, 2026-11-15) — Plugin configurations belong to the application only. The form starting from the server was admitted during the discussion and then withdrawn.
- **d-element-name** `decided` — What is the name of the element under `application`?
   1. **plugin**: The server-level `plugins` section already has `plugin` children with another function: switching plugins on.
   2. **plugin_configuration**
   → Choice: **plugin_configuration** (GP, 2026-11-15)
- **d-shape** `decided` — How is a configuration written inside the application?
   1. **From the route: route(path).configure(...)**: All the options changed on one route are read together. Each line must also name the plugin.
   2. **One block per plugin: plugin_configuration('auth'), then configure(path, options)**: All the rules of one plugin are read in one block. The options changed on one route sit in one block per plugin.
   3. **One block per application, each line with the target "plugin/path"**: A list of these lines is already a JSON form.
   → Choice: **One block per plugin: plugin_configuration('auth'), then configure(path, options)** (GP, 2026-11-15) — From a human point of view the plugins are configured separately.
- **d-arguments** `decided` — What does a `configure` line carry?
   1. **What routing.configure asks: the path and the parameters of the plugin (rule, channels)**
   2. **The keywords of @route: auth_rule, channel_channels**
   → Choice: **What routing.configure asks: the path and the parameters of the plugin (rule, channels)** (GP, 2026-11-15) — kajenn calls the `configure` of the routing class: what the recipe holds is what that method asks.
- **d-plugins** `decided` — Which plugins can the recipe configure?
   1. **Every plugin present on the router** *(example in the full document)*
   2. **A closed list: auth, channel, env, openapi**
   → Choice: **Every plugin present on the router** (GP, 2026-11-15)
- **d-missing-route** `decided` — What happens when the recipe names a route that does not exist?
   1. **The boot stops**
   2. **At most a warning; the boot goes on**
   → Choice: **At most a warning; the boot goes on** (GP, 2026-11-15)
- **d-visibility** `decided` — Where does one read which options the recipe changed?
   1. **Log lines when the configuration is applied**
   2. **A route of _server listing the changed options**
   3. **A field in the discovery and in the OpenAPI document**
   4. **Nothing added: nodes() for a user and the export of genro-routes**
   → Choice: **Nothing added: nodes() for a user and the export of genro-routes** (GP, 2026-11-15) — Calling `nodes` as a user says whether that user can or cannot. genro-routes has an export of the configuration.
- **d-after-hook** `decided` — Does a second hook run after the configuration is applied?
   1. **Yes: the pair before and after gives the two exports to compare**
   2. **No: a method compares the export taken before with the current state**
   → Choice: **No: a method compares the export taken before with the current state** (GP, 2026-11-15) — Who wants the differences has a method that takes the saved export and compares it with the current state.
- **d-hook** `decided` — Who applies the configuration, and who can refuse it or take the export before it is applied?
   1. **A plugin_configuration hook of the application, called with applications_broadcast** *(example in the full document)*
   2. **A hook of the server, one for all the applications**: One place takes the export of every application. The author of an application cannot refuse anything.
   3. **Both: the server hook, then the application hook**
   → Choice: **A plugin_configuration hook of the application, called with applications_broadcast** (GP, 2026-11-15) — A hook `plugin_configuration` that does nothing when there is nothing: a broadcast of plugin configuration.
- **d-section-path** `decided` — How does one line give a value to several routes, for example to all the routes of the section `products`?
   1. **A write at router level, as genro-routes does** *(example in the full document)*
   2. **Every route under the section** *(example in the full document)*
   3. **A path must end with the name of an entry; the last segment accepts the glob of the method** *(example in the full document)*
   4. **route holds several complete paths separated by commas**: Every route is written by name. No glob and no path of a section.
   → Choice: **route holds several complete paths separated by commas** (GP, 2026-11-15)
- **d-moment** `decided` — When does the server call the hook?
   1. **In the lifespan startup, before on_startup** *(example in the full document)*
   2. **After on_startup, before ready** *(example in the full document)*
   → Choice: **In the lifespan startup, before on_startup** (GP, 2026-11-15) — Before. The configurations can also change on a running server, like other things: a subscription updates the plugins when the configuration changes.
- **d-runtime-update** `decided` — How does the subscription update the plugins when the configuration of a running server changes?
   1. **Only the values the new configuration holds are applied** *(example in the full document)*
   2. **The values of the code are restored, then the whole block is applied again** *(example in the full document)*
   → Choice: **Only the values the new configuration holds are applied** (GP, 2026-11-15) — A change cannot take a line away. It can only say which values there are: there are no insert or delete actions, only the new configuration.
- **d-runtime-spawned** `decided` — How does a change of the plugin configuration reach the application of a spawned process?
   1. **A message of its own: the main process sends the plugin configuration over the KajennBus**
   2. **It does not: the spawned process reads the new configuration when it is relaunched**
   3. **Nothing specific to plugins: every change of the configuration is carried to the spawned process**
   → Choice: **Nothing specific to plugins: every change of the configuration is carried to the spawned process** (GP, 2026-11-15) — The problem is generic. When a process is remote, any change of the configuration, not only of plugins, must be carried to the remote process, which applies it; doing so fires its own subscriptions.
- **d-added-application** `decided` — How does an application added to a running server receive the hook?
   1. **The hook is called on that application alone, before its on_startup**
   2. **A new broadcast, which calls the hook again on every application**
   → Choice: **The hook is called on that application alone, before its on_startup** (GP, 2026-11-15)
- **d-invalid-line** `decided` — What happens when a line names a plugin that is not on the router, or an option the plugin does not know?
   1. **The boot stops**
   2. **The line is not applied and the boot goes on, as for a route that does not exist**
   → Choice: **The line is not applied and the boot goes on, as for a route that does not exist** (GP, 2026-11-15) — Nothing happens. Whoever wrote the line wrong answers for it.
- **d-refusal** `decided` — How does a hook refuse a configuration, and what does the server do then?
   1. **The subclass does not call the base: nothing is applied and nothing is reported**
   2. **The hook raises: on_error logs it and the broadcast goes on**
   3. **The hook raises: the boot stops**
   → Choice: **The subclass does not call the base: nothing is applied and nothing is reported** (GP, 2026-11-15) — As a rule, errors cannot be prevented with defensive code at every moment. If there are errors there are consequences. Only the stupid errors can be prevented; the dangerous ones pass anyway.
- **d-spawned-boot** `proposed` — At boot, who calls the hook of an application declared with `spawner`? The broadcast of the main process runs before `on_startup`, and `RemoteApplication.on_startup` is what waits for the spawned process to join: a hook forwarded before that has no process to answer it. The spawned process is a server too and has its own lifespan.
   1. **The spawned process, in its own lifespan startup**: Default of this outline. It is settled during the implementation on a branch. *(example in the full document)*
   2. **The main process, with the forwarded hook, once the spawned process has joined** *(example in the full document)*
- **d-diff-method** `proposed` — Who provides the method that compares the saved export with the current state, and what is its name?
   1. **kajenn provides it on the application**: Candidate name from the words of the owner: `config_diff`. kajenn then also takes and keeps the export.
   2. **Each application that wants it writes it in its own hook**: Default of this outline. It is settled during the implementation on a branch. asqueel-migration makes the same comparison of two dicts with the library `dictdiffer`, which is not a dependency of kajenn.
- **d-keywords** `decided` — Which keywords carry the plugin and the path in the recipe? A value written without a name is lost on a node that receives children, so the plugin of `plugin_configuration` needs a keyword.
   1. **plugin_configuration(code="auth") and configure(path="products/list", ...)**: `code` is the keyword of the existing `plugin` element.
   2. **plugin_configuration(plugin="auth") and configure(target="products/list", ...)**: `target` is the word of `routing.configure`.
   3. **plugin_configuration(plugin="auth") and configure(route="products/list", ...)**
   → Choice: **plugin_configuration(plugin="auth") and configure(route="products/list", ...)** (GP, 2026-11-15)

### Consequences

- The hook depends on `applications_broadcast` and `@broadcast_hook` of [kajenn-org/kajenn#142](https://github.com/kajenn-org/kajenn/issues/142), merged in `develop` with PR 147.
- No application can have a route named `plugin_configuration`: issue 142 refuses a route with the name of a `@broadcast_hook`.
- Carrying a change of the configuration to a spawned process is a general mechanism and is not part of this proposal. It does not exist today: until it does, a change made on a running server reaches only the applications of the main process. The live configuration is the subject of [kajenn-org/kajenn#75](https://github.com/kajenn-org/kajenn/issues/75); the requirement is noted there in a comment of 2026-11-15.
- A recipe can open a route its author closed. The author who must prevent it redefines the hook.
- The OpenAPI document follows the recipe with no other change: `security` is derived from the rule in force.
- The configuration guide gets a section on `plugin_configuration` with the `Shop` example.

### Related documents

- `proposal/live-applications` — Applications added, switched off and removed on a running server
