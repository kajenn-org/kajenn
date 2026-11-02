<!-- generated from known-urls-from-the-recipe.xml by xmldocs: do not edit, edit the XML -->

## Known URLs answered from the recipe
*Proposal · proposal/known-urls-from-the-recipe · 2026-11-02 · draft*

### Problem

External clients read some documents at fixed paths of a site. Examples: `/.well-known/security.txt` (RFC 9116), `/robots.txt`, `/.well-known/apple-app-site-association` (iOS universal links), `/.well-known/assetlinks.json` (Android app links).

The content of these documents depends on the deployment, not on the code: a contact address, the crawler rules, the identifiers of a mobile app. The same application code runs on sites that need different values.

In kajenn an application serves a `/.well-known/` document only by declaring it in its code. A fixed path outside `/.well-known/`, such as `/robots.txt`, has no mechanism of its own. A site cannot declare these documents in its recipe.

genropy legacy answers these four documents from the instance configuration, with no code per site.

### Current state

#### kajenn today: routing

- `READ` `src/kajenn/server.py:561` · 2026-11-02 — A first path segment starting with a dot gets 404 from the server demux and reaches no application. The one exception is `.well-known` followed by a name in `well_known_applications`: the request goes to that application with its path rebuilt as `/_well_known/<name>/<rest>`.
- `READ` `src/kajenn/routed_application.py:187` · 2026-11-02 — A `RoutedApplication` declares its documents as the children of a `_well_known` branch of its router. `well_known_names` returns those children. `BaseApplication.well_known_names` returns `()`.
- `READ` `src/kajenn/server.py:257` · 2026-11-02 — `register_application` first sets `app.server = self`, then calls `index_well_known(app, app.well_known_names)`. The names are read once, at that moment.
- `READ` `src/kajenn/server.py:262` · 2026-11-02 — `index_well_known` keeps one index name → application. A name declared by two applications belongs to the last registered.
- `READ` `src/kajenn/kbus_mixin.py:295` · 2026-11-02 — An external application presents its names in `meta["well_known"]` when its process joins the KajennBus. The server re-indexes them.
- `READ` `src/kajenn/server.py:525` · 2026-11-02 — A path without a leading dot, `/robots.txt` and `/favicon.ico` included, is demuxed like any other path: it reaches the application mounted on that first segment, else the root application, else 404.

The proposal channels-meta-and-server-gui (→ proposal/channels-meta-and-server-gui#d-segment) moves the branch `_well_known` to `_meta/well_known`. The public path `/.well-known/` does not change. This proposal holds for both locations.

#### kajenn today: the _server application and its recipe words

- `READ` `src/kajenn/asgi_server.py:138` · 2026-11-02 — The server always mounts a `ServerApplication` under `_server` when no declared application has that code.
- `READ` `src/kajenn/server_app/server_app.py:104` · 2026-11-02 — `ServerApplicationGrammar` holds the recipe words of `_server`. The recipe writes them under `applications.application(app_class=ServerApplication, code="_server")`: today `login(...)` and `oidc().provider(...)`. The words live in the application that owns the function, not in the site dialect.
- `READ` `src/kajenn/server_app/server_app.py:201` · 2026-11-02 — The `server` setter of `ServerApplication` calls `read_declared_login_surface`, which reads the `login` and `oidc` nodes of the recipe. The setter runs inside `register_application`, before the well-known names are indexed.
- `READ` `src/kajenn/server_app/server_app.py:290` · 2026-11-02 — `attach_section` asks extensions to register their sections during startup, after the application is registered. A `_well_known` child added at that time is not in the index.

#### kajenn today: static files and the proxy

- `READ` `src/kajenn/site_home.py:82` · 2026-11-02 — `SiteHome.static` names a folder for favicon, robots and sitemap. No code under `src/` serves that folder.
- `READ` `src/kajenn/storage_mixin.py:68` · 2026-11-02 — The default storage mount `home:` is the space the site keeps its own things in, statics included. It is anchored on the folder of the site card.
- `READ` `deploy/local-https/Caddyfile:9` · 2026-11-02 — The local HTTPS deployment puts Caddy in front of kajenn with `reverse_proxy kajenn:8080`. A reverse proxy can answer a fixed path before kajenn sees the request.

#### genropy legacy

- `READ` `genropy:gnr/web/gnrwsgisite.py:499` · 2026-11-02 — At startup the site reads `alias_url` from the `__call__` of every webtool found by `find_webtools()`. It builds `webtools_static_routes`, a dict absolute path → webtool name.
- `READ` `genropy:gnr/web/gnrwsgisite.py:1230` · 2026-11-02 — `_dispatcher` looks up `request.path` in that dict before every other rule. On a match it serves the webtool through `serve_tool(['_tools', <name>])`. The match is on the exact whole path.
- `READ` `genropy:wku.py:32` · 2026-11-02 — A webtool declares its path with `@metadata(alias_url=...)` on `__call__`. The path may be outside `/.well-known/`: `RobotsTxt` uses `/robots.txt`.

*Documents served by wku.py*

| Webtool | Path | Configuration read | Output |
|---|---|---|---|
| `SecurityTxt` | `/.well-known/security.txt` | `wku.security_txt` | One `Field: value` line per child |
| `RobotsTxt` | `/robots.txt` | `wku.robots_txt` | `User-agent` blocks with their rules, plus global lines |
| `DeepLinkIOS` | `/.well-known/apple-app-site-association` | `mobile_app.ios` | JSON `applinks` and `webcredentials`, with excluded paths |
| `DeepLinkAndroid` | `/.well-known/assetlinks.json` | `mobile_app.android` | JSON with package name and certificate fingerprints |

- `READ` `genropy:wku.py:14` · 2026-11-02 — A document whose configuration is missing raises an exception at request time.

### Proposed solution

#### Behaviour

- The recipe declares the documents the site answers at fixed paths. Each declaration carries the data of the document, not its text.
- kajenn generates the body from that data, with the media type the standard requires. No application code is needed.
- The first set is the legacy one: `security.txt`, `robots.txt`, `apple-app-site-association`, `assetlinks.json`.
- A document not declared in the recipe does not exist: its path follows today's rules (404 under `/.well-known/`, normal demux elsewhere). There is no runtime exception for a missing configuration.
- Wrong data is refused at boot, not at request time.
- Applications keep declaring their own `/.well-known/` documents in code, as today.

#### Recipe words on the _server application

The declarations are words of `ServerApplicationGrammar`, written under the `_server` application element like `login` and `oidc`. No site-level element is added.

`ServerApplication` reads them in its `server` setter, where it already reads `login` and `oidc`, and builds the children of its `_well_known` branch there. The setter runs before `register_application` indexes the names, so `/.well-known/<name>` reaches `_server` through the existing index. `demux_hidden` does not change.

Decision d-owner (→ d-owner) compares this with the alternatives. A root path such as `/robots.txt` has no existing mechanism: decision d-root (→ d-root) covers it.

#### Example recipe shape

The element names below do not exist in the code. They are placeholders until the decisions below are taken.

```python
server_app = applications.application(app_class=ServerApplication, code="_server")
server_app.security_txt(contact="mailto:security@acme.example",
                        expires="2027-11-01T00:00:00Z")
server_app.robots_txt(rules=[{"user_agent": "*", "disallow": ["/_server/"]}])
server_app.ios_app(team_id="ABCDE12345", bundle_id="example.acme.app",
                   excluded_paths=["/_server/*"])
server_app.android_app(package_name="example.acme.app",
                       fingerprints=["AA:BB:..."])
```

### Decisions

- **d-scope** `open` — Which documents are in the first cut?
   1. **The four legacy documents**: Pro: parity with genropy legacy; every format is already known. Con: none known.
   2. **The legacy four plus openid-configuration, change-password and sitemap.xml**: Pro: covers more probes. Con: code for documents no site asks for today.
- **d-owner** `open` — Which component answers the recipe documents?
   1. **ServerApplication, from its own recipe words**: Pro: `_server` is always mounted; its grammar already holds recipe words; the branch is built before indexing, so neither the demux nor the index changes. Con: `_server` gains a role that is not administration.
   2. **The server demux, before the application index**: Pro: works with no application at all. Con: the demux generates content, which today it never does; the words would go in the site dialect.
   3. **An application the site declares in the recipe**: Pro: explicit; uses only today's mechanism. Con: every site that wants one document must add an application.
   4. **The reverse proxy (Caddy, Traefik)**: Pro: no kajenn code. Con: the data lives outside the recipe; every deployment writes it again in its proxy syntax; a site without a proxy has nothing.
- **d-root** `open` — How is a root path outside /.well-known/ (/robots.txt) answered?
   1. **An exact-path index in the demux, checked before the mount lookup**: Pro: same rule as legacy; one dict lookup. Con: a site path shadows a mount with the same first segment.
   2. **Left to the root application, as today**: Pro: no change. Con: a site with no root application cannot answer `/robots.txt`.
   3. **Left to the reverse proxy**: Pro: no kajenn code. Con: same as d-owner-proxy (→ d-owner-proxy); `robots.txt` data and `/.well-known/` data end up in two places.
   4. **Only /.well-known/ documents are in scope**: Pro: smallest change. Con: `robots.txt`, part of the legacy set, is left out.
- **d-conflict** `open` — What happens when an application declares a name the recipe also declares?
   1. **Boot fails with FatalBootError**: Pro: no silent winner. Con: an external application that joins later cannot be refused at boot.
   2. **The recipe wins**: Pro: the site has the last word over application code. Con: changes today's rule (the last registered wins).
   3. **Today's rule: the last registered wins**: Pro: no new rule. Con: the result depends on registration order.
- **d-static** `open` — Are files kept by the site (favicon.ico, sitemap.xml) also served at fixed paths?
   1. **No, out of this proposal**: Pro: keeps generated documents and files apart. Con: `SiteHome.static` stays a folder nobody serves.
   2. **Yes, read through the home: storage mount**: Pro: uses the mount that already holds the site's statics; the same code works on a non-local backend. Con: depends on d-root (→ d-root).

### Consequences

- `ServerApplicationGrammar` gains one element per document type. The site dialect does not change.
- No new dependency: every format is plain text or JSON.
- A genropy site moved to kajenn writes the same data in the recipe instead of `wku.*` and `mobile_app.*`.
- A `_well_known` child added to `_server` after registration, for example by an extension through `attach_section`, is not indexed. Serving it needs a new call to `index_well_known`. This proposal does not add that call.
- With d-root-exact (→ d-root-exact), the demux gains one lookup before the mount lookup.

### Related documents

- `proposal/channels-meta-and-server-gui` — Moves the _well_known branch under _meta
