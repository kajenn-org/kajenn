<!-- generated from channels-meta-and-server-gui.xml by xmldocs: do not edit, edit the XML -->

## Channel faces on every routed application, one _meta segment, and the server GUI as a separate component
*Proposal · proposal/channels-meta-and-server-gui · 2026-11-08 · accepted*

### Problem

Three application classes expose the same router on different channels: `OpenApiApplication` (REST and OpenAPI), `McpOpenApiApplication` (REST, OpenAPI and MCP) and `McpApplication` (MCP only). `McpOpenApiApplication` is the most common base, and its name is long.

Each class reserves its own path segments inside the application: `_meta` and `mcp`. The rule for what an application may name its own routes is not written anywhere.

The server application `_server` extends `OpenApiApplication`, so it has no MCP face. A browser interface for `_server` is being built on gramlot. If it lives in kajenn, gramlot becomes a required dependency of the core.

### Current state

- `READ` `src/kajenn/routed_application.py:127` · 2026-11-08 — `RoutedApplication` is `BaseApplication` plus the genro-routes `RoutingClass`. Its `execute` is the single execution point that REST, MCP, WSX and the KajennBus reach.
- `READ` `src/kajenn/applications/openapi.py:89` · 2026-11-08 — `OpenApiApplication` attaches `_meta` with `schema_json`, `docs` (Swagger UI) and `index`.
- `READ` `src/kajenn/applications/mcp.py:387` · 2026-11-08 — `McpOpenApiApplication` adds the MCP JSON-RPC face under the segment `mcp` (`mcp_name_segment`). The `channel` plugin decides visibility: the MCP face lists only entries declared for channel `mcp`, and an undeclared method is REST only.
- `READ` `src/kajenn/applications/mcp.py:323` · 2026-11-08 — `McpApplication` makes the whole application one JSON-RPC endpoint over an external `RoutingClass`, with no REST face.
- `READ` `src/kajenn/well_known.py:41` · 2026-11-08 — A path matching `WELL_KNOWN_PATTERN` goes to the route the application declared for it in its class dict `well_known`. No segment inside the application is reserved for it.
- `READ` `src/kajenn/server_app/server_app.py:208` · 2026-11-08 — `ServerApplication` extends `OpenApiApplication`.
- `READ` `src/kajenn/asgi_server.py:139` · 2026-11-08 — The server mounts `ServerApplication` under `_server` when no declared application has that code.
- `READ` `src/kajenn/server.py:582` · 2026-11-08 — After the well-known paths, the server demux picks an application by the first path segment only. A mount is always one segment.

### Proposed solution

#### Channel faces on RoutedApplication

- The OpenAPI face and the MCP face move into `RoutedApplication`. Every application with routes has both, with no switch to turn them off.
- The channel of a plain HTTP request is named `api`, short for OpenAPI, in place of `rest`: kajenn calls a method by path and describes it with OpenAPI, it is not REST. A route for both faces declares `channel_channels="mcp,api"`. Under `_meta` every branch is named after its channel: `api` and `mcp`.
- The `channel` plugin keeps deciding visibility. Turning MCP on everywhere exposes nothing that is not declared for channel `mcp`.
- `OpenApiApplication`, `McpOpenApiApplication` and `McpApplication` are removed in the release that brings the faces, with no deprecated alias. No new class name is needed.
- `RoutedApplication` takes no external `RoutingClass`: the parameters `routing_class`, `module` and `api_name` are gone. To expose a `RoutingClass` that already exists, an application class contains it.
- The parameter `docs="off"` stays and turns off only the Swagger UI page `_meta/api/docs`. The OpenAPI face and `_meta/api/schema_json` stay.
- `BaseApplication` stays for applications without routes: `RemoteApplication` and raw ASGI applications.

#### One reserved segment: _meta

Inside a `RoutedApplication` the only reserved segment is `_meta`. It holds the administrative routes of the application. Application routes are never under it: a route `pippo` is reached as `/<app>/pippo` over REST and as tool `pippo` over MCP.

*_meta layout*

| Path | Content | Today |
|---|---|---|
| `_meta/index` | Application page with links to the channel descriptions | `_meta/index` |
| `_meta/api/schema_json` | OpenAPI 3.1 document | `_meta/schema_json` |
| `_meta/api/docs` | Swagger UI | `_meta/docs` |
| `POST _meta/mcp` | MCP JSON-RPC entry | `mcp` |
| `GET _meta/mcp/tools` | List of the MCP tools, readable without speaking MCP | none |

The names `mcp` and `api` become free for application routes.

Discovery documents are not under `_meta`: an application declares its well-known URLs in a class dict, and the `_well_known` branch was removed (well-known-urls (→ proposal/well-known-urls#d-declare), 2026-11-02).

#### SpaConsole in kajenn-orchestra

`SpaConsoleMcpApplication` becomes a `RoutedApplication`. The routes of `SpaConsole` are declared for channel `mcp`, so the OpenAPI face refuses them. Its MCP client moves from `/<mount>/` to `/<mount>/_meta/mcp`.

#### Server application: API in kajenn, GUI in its own repository

- `ServerApplication` stays in kajenn and serves OpenAPI and MCP only. It gains MCP by inheriting from `RoutedApplication`. kajenn does not depend on gramlot.
- The GUI is a component in the repository `kajenn-serverapp-gui`, which depends on kajenn and gramlot.
- The recipe enables it on the server element: `gui(gui_class=...)`. Without the element there is no GUI. The core never names the GUI package.
- `ServerApplication` builds the component as `gui_class(self)` (dual relationship) and passes every request on `ui/...` to it. The component releases its resources in `on_shutdown`. The URL is `/_server/ui/`.
- The GUI reads and writes only through the OpenAPI face of `_server`, like any other client.

#### Extension pages

- The routes of an extension keep using `attach_section(section, name)` of the core.
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
   1. **Removed**
   2. **Kept**
   → Choice: **Removed** (GP, 2026-10-28) — A RoutedApplication with routes declared for channel mcp gives the same result.
- **d-removal** `decided` — How do OpenApiApplication, McpOpenApiApplication and McpApplication go away?
   1. **Deprecated aliases, then removed**
   2. **Removed in the release that brings the faces**
   → Choice: **Removed in the release that brings the faces** (GP, 2026-11-08) — kajenn is still in the laboratory: no application runs it in production.
- **d-old-urls** `decided` — How long do the old URLs \_meta/schema\_json and mcp answer?
   1. **One minor release**
   2. **A period fixed case by case**
   → Choice: **A period fixed case by case** (GP, 2026-11-08) — A public URL that changes keeps answering for a while; the length is decided case by case, as for every deprecation.
- **d-mounted** `decided` — Does RoutedApplication take an external RoutingClass through routing\_class, module and api\_name?
   1. **Yes**
   2. **No**
   → Choice: **No** (GP, 2026-11-08) — What a recipe or a hot add names is always an application class.
- **d-docs-off** `decided` — Does the parameter docs="off" stay?
   1. **Removed: the Swagger UI page is always there**
   2. **Kept, for the Swagger UI page only**
   → Choice: **Kept, for the Swagger UI page only** (GP, 2026-11-08) — It turns off the page, not the OpenAPI face nor the schema.
- **d-channel-name** `decided` — What is the channel of a plain HTTP request called, and the branch of \_meta that describes it?
   1. **rest, as today**
   2. **openapi**
   3. **api, short for openapi**
   4. **http**
   → Choice: **api, short for openapi** (GP, 2026-11-09) — kajenn is not REST: a route is a method called by path and described by OpenAPI. The channel is openapi, and api is its short name.
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
- **d-gui-name** `decided` — What is the attribute of the gui element called?
   1. **gui_class**: `class` is a Python keyword; other elements use `app_class` and `handler_class`.
   → Choice: **gui_class** (GP, 2026-11-03) — The same shape as app\_class and handler\_class.

### Consequences

- Three URLs change: `/<app>/_meta/schema_json` becomes `/<app>/_meta/api/schema_json`, `/<app>/_meta/docs` becomes `/<app>/_meta/api/docs`, and `/<app>/mcp` becomes `/<app>/_meta/mcp`. The old URLs give the same answer as the new ones, with a `Deprecation` header and a log line, for a period fixed case by case, as for every deprecation. A redirect is not used: an MCP client posting JSON-RPC does not follow it.
- MCP clients and links to the docs must be updated.
- Every route declaring the channel `rest` declares `api`.
- Every routed application reserves `_meta`. A route named `_meta` must fail at registration.
- `ServerApplication` gains an MCP face.
- Every application built on `OpenApiApplication`, `McpOpenApiApplication` or `McpApplication` moves to `RoutedApplication` in the release that removes them. genropy-kajenn uses `OpenApiApplication` through `GenropyProxyOpenApiApplication`.
- A recipe passing `routing_class` or `module` to an application is rewritten with an application class that contains the routing class.
- kajenn-orchestra moves `SpaConsoleMcpApplication` and registers its pool pages on the GUI component.
