<!-- generated from channels-meta-and-server-gui.xml by xmldocs: do not edit, edit the XML -->

## Channel faces on every routed application, one _meta segment, and the server GUI as a separate component
*Proposal · proposal/channels-meta-and-server-gui · 2026-10-28 · review*

### Problem

Three application classes expose the same router on different channels: `OpenApiApplication` (REST and OpenAPI), `McpOpenApiApplication` (REST, OpenAPI and MCP) and `McpApplication` (MCP only). `McpOpenApiApplication` is the most common base, and its name is long.

Each class reserves its own path segments inside the application: `_meta`, `mcp`, `_well_known`. The rule for what an application may name its own routes is not written anywhere.

The server application `_server` extends `OpenApiApplication`, so it has no MCP face. A browser interface for `_server` is being built on gramlot. If it lives in kajenn, gramlot becomes a required dependency of the core.

### Current state

- `READ` `src/kajenn/routed_application.py:128` · 2026-10-28 — `RoutedApplication` is `BaseApplication` plus the genro-routes `RoutingClass`. Its `execute` is the single execution point that REST, MCP, WSX and the KajennBus reach.
- `READ` `src/kajenn/applications/openapi.py:89` · 2026-10-28 — `OpenApiApplication` attaches `_meta` with `schema_json`, `docs` (Swagger UI) and `index`.
- `READ` `src/kajenn/applications/mcp.py:387` · 2026-10-28 — `McpOpenApiApplication` adds the MCP JSON-RPC face under the segment `mcp` (`mcp_name_segment`). The `channel` plugin decides visibility: the MCP face lists only entries declared for channel `mcp`, and an undeclared method is REST only.
- `READ` `src/kajenn/applications/mcp.py:323` · 2026-10-28 — `McpApplication` makes the whole application one JSON-RPC endpoint over an external `RoutingClass`, with no REST face.
- `READ` `src/kajenn/well_known.py:39` · 2026-10-28 — Discovery documents are served from the application branch `_well_known`. The server maps `/.well-known/<name>` onto it.
- `READ` `src/kajenn/server_app/server_app.py:153` · 2026-10-28 — `ServerApplication` extends `OpenApiApplication`.
- `READ` `src/kajenn/asgi_server.py:138` · 2026-10-28 — The server mounts `ServerApplication` under `_server` when no declared application has that code.
- `READ` `src/kajenn/server.py:546` · 2026-10-28 — The server demux picks an application by the first path segment only. A mount is always one segment.

### Proposed solution

#### Channel faces on RoutedApplication

- The REST/OpenAPI face and the MCP face move into `RoutedApplication`. Every application with routes has both, with no switch to turn them off.
- The `channel` plugin keeps deciding visibility. Turning MCP on everywhere exposes nothing that is not declared for channel `mcp`.
- `OpenApiApplication`, `McpOpenApiApplication` and `McpApplication` become deprecated aliases, then are removed. No new class name is needed.
- `BaseApplication` stays for applications without routes: `RemoteApplication` and raw ASGI applications.

#### One reserved segment: _meta

Inside a `RoutedApplication` the only reserved segment is `_meta`. It holds the administrative routes of the application. Application routes are never under it: a route `pippo` is reached as `/<app>/pippo` over REST and as tool `pippo` over MCP.

*_meta layout*

| Path | Content | Today |
|---|---|---|
| `_meta/index` | Application page with links to the channel descriptions | `_meta/index` |
| `_meta/openapi/schema_json` | OpenAPI 3.1 document | `_meta/schema_json` |
| `_meta/openapi/docs` | Swagger UI | `_meta/docs` |
| `POST _meta/mcp` | MCP JSON-RPC entry | `mcp` |
| `GET _meta/mcp/tools` | List of the MCP tools, readable without speaking MCP | none |
| `_meta/well_known/...` | Discovery documents; `/.well-known/` is unchanged | `_well_known/...` |

The names `mcp`, `api` and `_well_known` become free for application routes.

#### SpaConsole in kajenn-orchestra

`SpaConsoleMcpApplication` becomes a `RoutedApplication`. The routes of `SpaConsole` are declared for channel `mcp`, so the REST face refuses them. Its MCP client moves from `/<mount>/` to `/<mount>/_meta/mcp`.

#### Server application: API in kajenn, GUI in its own repository

- `ServerApplication` stays in kajenn and serves REST and MCP only. It gains MCP by inheriting from `RoutedApplication`. kajenn does not depend on gramlot.
- The GUI is a component in the repository `kajenn-serverapp-gui`, which depends on kajenn and gramlot.
- The recipe enables it on the server element: `gui(gui_class=...)`. Without the element there is no GUI. The core never names the GUI package.
- `ServerApplication` builds the component as `gui_class(self)` (dual relationship) and passes every request on `ui/...` to it. The component releases its resources in `on_shutdown`. The URL is `/_server/ui/`.
- The GUI reads and writes only through the REST routes of `_server`, like any other client.

#### Extension pages

- The REST routes of an extension keep using `attach_section(section, name)` of the core.
- The GUI pages of an extension register on the GUI component, only when the component exists. `ServerApplication` exposes the component, or `None`.
- gramlot is an optional extra of the extension, for example `kajenn-orchestra[gui]`.
- Pages generated from the OpenAPI description of each section are a future direction. They are not designed here.

### Decisions

- **d-faces** `decided` — Where do the REST/OpenAPI and MCP faces live?
   1. **In RoutedApplication, always on**
   2. **In dedicated subclasses, as today**
   → Choice: **In RoutedApplication, always on** (GP, 2026-10-28) — Every routed application answers network calls on every channel; visibility stays with the channel plugin.
- **d-switch** `decided` — Can an application turn the faces off?
   1. **No**
   2. **Yes, with a flag**
   → Choice: **No** (GP, 2026-10-28) — A schema lists only routes already exposed; a switch would be code without a verified use.
- **d-segment** `decided` — Which segments does the kernel reserve inside an application?
   1. **Every segment starting with an underscore**
   2. **The bare segment _**
   3. **_meta only**
   → Choice: **_meta only** (GP, 2026-10-28) — One segment for every administrative route; reserving every underscore name takes too much from the application.
- **d-mcpapp** `decided` — Does McpApplication stay?
   1. **Deprecated, then removed**
   2. **Kept**
   → Choice: **Deprecated, then removed** (GP, 2026-10-28) — A RoutedApplication with routes declared for channel mcp gives the same result.
- **d-gui-switch** `decided` — How does a recipe enable the server GUI?
   1. **Optional import of the GUI package**
   2. **app_class on the _server application**
   3. **A boolean flag on the server**
   4. **A gui element on the server naming the class**
   → Choice: **A gui element on the server naming the class** (GP, 2026-10-28) — The recipe names the class; the core holds neither a dependency nor the name of the GUI package.
- **d-gui-shape** `decided` — What is the class named by gui?
   1. **A composed server application replacing _server**
   2. **A component built by ServerApplication and called for ui/...**
   → Choice: **A component built by ServerApplication and called for ui/...** (GP, 2026-10-28) — It works with any \_server class and needs no mixin.
- **d-ext-pages** `decided` — How do extensions add GUI pages?
   1. **Register on the GUI component when present**
   2. **A separate GUI package per extension**
   3. **Pages generated from the REST description**
   → Choice: **Register on the GUI component when present** (GP, 2026-10-28) — Generated pages remain the direction for later.
- **d-gui-name** `proposed` — What is the attribute of the gui element called?
   1. **gui_class**: `class` is a Python keyword; other elements use `app_class` and `handler_class`.

### Consequences

- Two URLs change: `/<app>/_meta/schema_json` becomes `/<app>/_meta/openapi/schema_json`, and `/<app>/mcp` becomes `/<app>/_meta/mcp`. The old URLs answer with a deprecation warning for one minor release.
- MCP clients and links to the docs must be updated.
- Every routed application reserves `_meta`. A route named `_meta` must fail at registration.
- `ServerApplication` gains an MCP face.
- genropy-kajenn uses `OpenApiApplication` through `GenropyProxyOpenApiApplication`; it keeps working on the alias and must move before the removal.
- kajenn-orchestra moves `SpaConsoleMcpApplication` and registers its pool pages on the GUI component.
