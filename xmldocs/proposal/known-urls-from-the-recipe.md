<!-- generated from known-urls-from-the-recipe.xml by xmldocs: do not edit, edit the XML -->

## Known URLs answered from the recipe
*Proposal · proposal/known-urls-from-the-recipe · 2026-11-02 · draft*

### Problem

External clients read some documents at fixed paths of a site. Examples: `/.well-known/security.txt` (RFC 9116), `/robots.txt`, `/.well-known/apple-app-site-association` (iOS universal links), `/.well-known/assetlinks.json` (Android app links).

The content of these documents depends on the deployment, not on the code: a contact address, the crawler rules, the identifiers of a mobile app. The same application code runs on sites that need different values.

In kajenn an application serves a `/.well-known/` document only by declaring it in its code. A fixed path outside `/.well-known/`, such as `/robots.txt`, has no mechanism of its own. A site cannot declare these documents in its recipe.

genropy legacy answers these four documents from the instance configuration, with no code per site.

### Current state

#### kajenn today

- `READ` `src/kajenn/server.py:561` · 2026-11-02 — A first path segment starting with a dot gets 404 from the server demux and reaches no application. The one exception is `.well-known` followed by a name in `well_known_applications`: the request goes to that application with its path rebuilt as `/_well_known/<name>/<rest>`.
- `READ` `src/kajenn/routed_application.py:187` · 2026-11-02 — A `RoutedApplication` declares its documents as the children of a `_well_known` branch of its router. `well_known_names` returns those children. `BaseApplication.well_known_names` returns `()`.
- `READ` `src/kajenn/server.py:262` · 2026-11-02 — `index_well_known` keeps one index name → application. A name declared by two applications belongs to the last registered.
- `READ` `src/kajenn/kbus_mixin.py:295` · 2026-11-02 — An external application presents its names in `meta["well_known"]` when its process joins the KajennBus. The server re-indexes them.
- `READ` `src/kajenn/server.py:525` · 2026-11-02 — A path without a leading dot, `/robots.txt` and `/favicon.ico` included, is demuxed like any other path: it reaches the application mounted on that first segment, else the root application, else 404.
- `READ` `src/kajenn/site_home.py:82` · 2026-11-02 — `SiteHome.static` names a folder for favicon, robots and sitemap. No code under `src/` serves that folder.
- `READ` `src/kajenn/asgi_server.py:138` · 2026-11-02 — The server always mounts a `ServerApplication` under `_server` when no declared application has that code.

The proposal channels-meta-and-server-gui (→ proposal/channels-meta-and-server-gui#d-segment) moves the branch `_well_known` to `_meta/well_known`. The public path `/.well-known/` does not change. This proposal holds for both locations.

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

- The recipe declares, at site level, the documents the site answers at fixed paths. Each declaration carries the data of the document, not its text.
- kajenn generates the body from that data, with the media type the standard requires. No application code is needed.
- The first set is the legacy one: `security.txt`, `robots.txt`, `apple-app-site-association`, `assetlinks.json`.
- A document not declared in the recipe does not exist: its path follows today's rules (404 under `/.well-known/`, normal demux elsewhere). There is no runtime exception for a missing configuration.
- Wrong data is refused at boot, not at request time.
- Applications keep declaring their own `/.well-known/` documents in code, as today.

#### Where the recipe documents are answered

`ServerApplication` is always mounted. It can carry the recipe documents as children of its own `_well_known` branch. `/.well-known/<name>` then reaches it through the index that exists today, with no change to `demux_hidden`. Decision d-owner (→ d-owner) compares this with the alternatives.

A root path such as `/robots.txt` has no existing mechanism. Decision d-root (→ d-root) covers it.

#### Example recipe shape

Element and attribute names are placeholders until the decisions below are taken.

```python
def main(self, root):
    site = root.site(name="acme")
    known = site.known_urls()
    known.security_txt(contact="mailto:security@acme.example",
                       expires="2027-11-01T00:00:00Z")
    known.robots_txt(rules=[{"user_agent": "*", "disallow": ["/_server/"]}])
    known.ios_app(team_id="ABCDE12345", bundle_id="example.acme.app",
                  excluded_paths=["/_server/*"])
    known.android_app(package_name="example.acme.app",
                      fingerprints=["AA:BB:..."])
```

### Decisions

- **d-scope** `open` — Which documents are in the first cut?
   1. **The four legacy documents**: Pro: parity with genropy legacy; every format is already known. Con: none known.
   2. **The legacy four plus openid-configuration, change-password and sitemap.xml**: Pro: covers more probes. Con: code for documents no site asks for today.
- **d-owner** `open` — Which component answers the recipe documents?
   1. **ServerApplication, in its _well_known branch**: Pro: `ServerApplication` is always mounted; the existing index routes `/.well-known/` with no demux change. Con: `_server` gains a role that is not administration.
   2. **The server demux, before the application index**: Pro: works with no application at all. Con: the demux generates content, which today it never does.
   3. **An application the site declares in the recipe**: Pro: explicit; uses only today's mechanism. Con: every site that wants one document must add an application.
- **d-root** `open` — How is a root path outside /.well-known/ (/robots.txt) answered?
   1. **An exact-path index in the demux, checked before the mount lookup**: Pro: same rule as legacy; one dict lookup. Con: a site path shadows a mount with the same first segment.
   2. **Left to the root application, as today**: Pro: no change. Con: a site with no root application cannot answer `/robots.txt`.
   3. **Only /.well-known/ documents are in scope**: Pro: smallest change. Con: `robots.txt`, part of the legacy set, is left out.
- **d-conflict** `open` — What happens when an application declares a name the recipe also declares?
   1. **Boot fails with FatalBootError**: Pro: no silent winner. Con: an external application that joins later cannot be refused at boot.
   2. **The recipe wins**: Pro: the site has the last word over application code. Con: changes today's rule (the last registered wins).
   3. **Today's rule: the last registered wins**: Pro: no new rule. Con: the result depends on registration order.
- **d-static** `open` — Does SiteHome.static also serve files at fixed paths (favicon.ico, sitemap.xml)?
   1. **No, out of this proposal**: Pro: keeps generated documents and static files apart. Con: `SiteHome.static` stays a folder nobody serves.
   2. **Yes, through the same exact-path index**: Pro: one place for every fixed path. Con: depends on d-root (→ d-root).

### Consequences

- The recipe grammar gains a site-level element and one child per document type.
- No new dependency: every format is plain text or JSON.
- A genropy site moved to kajenn writes the same data in the recipe instead of `wku.*` and `mobile_app.*`.
- With d-root-exact (→ d-root-exact), the demux gains one lookup before the mount lookup.

### Related documents

- `proposal/channels-meta-and-server-gui` — Moves the _well_known branch under _meta
