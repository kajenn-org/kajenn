<!-- generated from well-known-urls.xml by xmldocs: do not edit, edit the XML -->

## Well-known URLs declared by applications
*Proposal · proposal/well-known-urls · 2026-11-02 · accepted*

### Problem

External clients read some documents at fixed paths of a site, before they access anything else. Examples: `/.well-known/security.txt` (RFC 9116), `/robots.txt` (RFC 9309), `/.well-known/apple-app-site-association` (iOS universal links), `/.well-known/assetlinks.json` (Android app links), `/.well-known/oauth-protected-resource/<resource>` (RFC 9728).

In kajenn an application answers a `/.well-known/` document only as a child of a reserved `_well_known` branch of its router, built in code before the server registers it. A mixin cannot bring its own documents into an application, a fixed path outside `/.well-known/` such as `/robots.txt` has no mechanism, and two applications declaring the same name are resolved silently by registration order.

genropy legacy answers these documents with webtools that declare their public path on the class (`alias_url`). This proposal brings that model to kajenn applications.

### Current state

#### kajenn today

- `READ` `src/kajenn/server.py:561` · 2026-11-02 — A first path segment starting with a dot gets 404 from the server demux. The one exception is `.well-known` followed by a name in `well_known_applications`: the request goes to that application with its path rewritten as `/_well_known/<name>/<rest>`.
- `READ` `src/kajenn/routed_application.py:187` · 2026-11-02 — A `RoutedApplication` declares its documents as the children of a `_well_known` branch of its router. `well_known_names` returns those children.
- `READ` `src/kajenn/server.py:242` · 2026-11-02 — `register_application` sets `app.server = self`, then indexes `app.well_known_names` once. A name declared by two applications belongs to the last registered. A claimed code or mount raises `ValueError`.
- `READ` `src/kajenn/kbus_mixin.py:295` · 2026-11-02 — An external application presents its names in `meta["well_known"]`, a list of strings, when its process joins the KajennBus.
- `READ` `src/kajenn/server.py:540` · 2026-11-02 — A path without a leading dot, `/robots.txt` and `/favicon.ico` included, is demuxed like any other path: the root application if there is one, else 404.
- `READ` `src/kajenn/site_home.py:83` · 2026-11-02 — `SiteHome.static` names a folder for the site's static files. No code serves it.
- `READ` `src/kajenn/application.py:166` · 2026-11-02 — `BaseApplication.__init__` receives `code` and `mount` from the recipe and sets `self.mount`. The class does not know its mount.
- `READ` `src/kajenn/application.py:45` · 2026-11-02 — Each application has one class attribute `grammar`, inherited by MRO and mounted in the recipe under its `application(app_class=...)` element. Composition is explicit; a mixin cannot add words.
- `RUN` `genro_builders/builder/base.py:230` · 2026-11-02 — genro-builders 0.30.0 composes the elements of plain mixin bases of a grammar class: elements with different names are united; an element with the same name is replaced entirely.

#### genropy legacy

- `READ` `genropy:gnr/web/gnrwsgisite.py:499` · 2026-11-02 — At startup the site reads `alias_url` from every webtool and builds `webtools_static_routes`, a dict absolute path → webtool. `_dispatcher` looks the exact path up before every other rule.
- `READ` `genropy:webtools/wku.py:71` · 2026-11-02 — `DeepLinkIOS` always associates the whole site (`"/": "/*"`); the configuration only excludes paths. It also emits `webcredentials` with the same app ids.
- `READ` `genropy:webtools/wku.py:94` · 2026-11-02 — `DeepLinkAndroid` emits package name, certificate fingerprints and the relations `handle_all_urls` and `get_login_creds`. It has no paths.

### Proposed solution

#### Behaviour

- An application class or a mixin declares the well-known URLs it answers in a class dict `well_known`: public path → route path inside the application.
- What is well-known is fixed by the standards, not configured: `/.well-known/...` (RFC 8615), `/robots.txt` (RFC 9309), `/favicon.ico` (the HTML default icon). RFC 8615 cites `/robots.txt` and `/favicon.ico` as the site-wide metadata that predates the prefix.
- A request whose path is well-known goes to the declared route of the declaring application. A well-known path nobody declared answers 404 with the message "unimplemented well-known URL": for `robots.txt` a 4xx means "no restrictions" (RFC 9309), for the other documents "not supported".
- Two applications declaring the same path stop the server at construction. Inside one application the more derived class wins.
- A path that depends on the mount (OAuth protected resources) is declared once on the class with the marker `{mount}`.
- The site author is responsible for what a well-known route answers: kajenn does not check that it is public.

#### Declaring well-known URLs

```python
class MobileMixin:
    well_known = {
        "/.well-known/apple-app-site-association": "mobile/apple_app_site_association",
        "/.well-known/assetlinks.json": "mobile/assetlinks",
    }

class OauthApi(RoutedApplication):
    well_known = {"/.well-known/oauth-protected-resource/{mount}": "oauth/resource"}

class Shop(MobileMixin, OauthApi):
    well_known = {"/robots.txt": "seo/robots"}
```

- Keys are public paths with the leading `/`. Values are route paths inside the application, without it.
- `BaseApplication.__init__` merges the dicts along the MRO (the more derived class wins on the same key), replaces `{mount}` with `self.mount` and stores the result in `self.well_known`. For the root mount (`""`) the segment `/{mount}` is removed whole: RFC 9728 has no trailing slash for a root resource.
- A key that is not a well-known path is a construction error.
- An application mounted twice gets one key per mount for `{mount}` keys; a key without the marker declared by both instances is a conflict, which is correct: a site has one of each.

#### Server

- The well-known paths are one kajenn constant: `^/(\.well-known/.+|robots\.txt|favicon\.ico)$`. It is not configurable. `sitemap.xml` is not in it: the sitemap protocol does not fix its location.
- `register_application` reads `app.well_known`, checks that each route exists and builds one index public path → (application, route). A path already held by another application raises `ValueError`, like a claimed code or mount.
- The demux matches the constant first. A declared path is forwarded to its application as the route path; an undeclared one answers 404 "unimplemented well-known URL". Other first segments starting with a dot stay 404.
- The `_well_known` branch, `WELL_KNOWN_ROOT`, `well_known_names` and the rewrite in `demux_hidden` are removed.
- An external application sends its `well_known` dict when it joins. A path held by another application refuses the join with `kbus.Refused`; its own re-join replaces its paths.

#### Documents of the _server application

- `/.well-known/security.txt` and `/robots.txt` are generated from words of `ServerApplicationGrammar`. `robots.txt` rules are validated at boot.
- `/favicon.ico` is served as a file from `home:/static`.
- `_server` registers each path only when its words are configured or its file exists. An application may answer any of them itself, for example a favicon per host; if both do, the server stops with the conflict error.

#### Mobile mixin

- A mixin inside kajenn gives an application its mobile capability: its own grammar words, a routing class under `mobile` and the two keys of its `well_known`.
- The recipe holds only real parameters: `ios(team_id, bundle_id, paths, excluded_paths)` and `android(package_name, fingerprints, paths, excluded_paths)`. The routes build the fixed JSON.
- iOS: `paths` is required, at least one, in Apple `components` syntax (`*`, `?`), copied as is; exclusions are written first. No `/*` default.
- Android: `paths` and `excluded_paths` are optional, same syntax, written as `relation_extensions` → `dynamic_app_link_components` (Dynamic App Links, Android 15 and later; older devices use the app manifest).
- `webcredentials` (iOS) and `get_login_creds` (Android) are always emitted from the same identifiers. The app activates them on its side.
- A platform without configuration registers no key. A mixin with no platform configured logs a warning at boot.

#### Grammar composition

`BaseApplication.__init_subclass__` composes `cls.grammar` from the grammars declared along the MRO, so a mixin brings its words into every application that uses it. Only new elements are composed: an element with the same name replaces the earlier one, as genro-builders does today.

### Decisions

- **d-declare** `decided` — How does an application say which well-known URLs it answers?
   1. **Children of a _well_known branch, as today**
   2. **A route plugin carrying the public name**
   3. **Pairs public path → route written in the recipe**
   4. **A class dict well_known: public path → route**
   → Choice: **A class dict well_known: public path → route** (GP, 2026-11-02) — The application already knows the pairing; the recipe keeps only real parameters. It is the legacy alias\_url model and needs no reserved branch.
- **d-standard** `decided` — Which paths are well-known?
   1. **Only /.well-known/...**
   2. **A regex the recipe can change**
   3. **A kajenn constant with the locations fixed by standards**
   → Choice: **A kajenn constant with the locations fixed by standards** (GP, 2026-11-02) — The regex is the standards: /.well-known/, robots.txt and favicon.ico. Nothing to configure.
- **d-undeclared** `decided` — What answers a well-known path no application declared?
   1. **404 with the message unimplemented well-known URL**
   2. **The normal demux, so a root application still receives it**
   → Choice: **404 with the message unimplemented well-known URL** (GP, 2026-11-02) — 404 is the standard answer for an absent document.
- **d-conflict** `decided` — What happens when two applications declare the same path?
   1. **ValueError at construction; an external join is refused**
   2. **The last registered wins, as today**
   3. **An override flag lets one declaration replace the other**
   → Choice: **ValueError at construction; an external join is refused** (GP, 2026-11-02) — No silent winner. The override flag was discussed and not adopted: with \_server declaring only what is configured no case needs it, and two overrides would conflict again.
- **d-mount** `decided` — How is a path that contains the mount declared?
   1. **Added on the instance in __init__**
   2. **Forbid two instances of one class**
   3. **Regex keys**
   4. **A {mount} marker in the class key**
   → Choice: **A {mount} marker in the class key** (GP, 2026-11-02) — The declaration stays on the class. Forbidding two instances does not help, because the recipe chooses the mount anyway; regex keys are ambiguous between applications and their overlap cannot be checked at construction.
- **d-public** `decided` — Does kajenn check that a well-known route is public?
   1. **Yes, at registration**
   2. **No**
   → Choice: **No** (GP, 2026-11-02) — The site author knows what the route answers.
- **d-static** `decided` — How are /robots.txt and /favicon.ico available without writing an application?
   1. **Both as files from home:/static**
   2. **Not at all: an application declares them**
   3. **robots.txt from _server words, favicon.ico from home:/static**
   → Choice: **robots.txt from _server words, favicon.ico from home:/static** (GP, 2026-11-02) — robots.txt has rules worth validating at boot; favicon.ico is an image.
- **d-mobile-paths** `decided` — Which site paths does the mobile app open?
   1. **iOS paths required; Android paths optional**
   2. **The whole site (/*), as legacy**
   → Choice: **iOS paths required; Android paths optional** (GP, 2026-11-02) — Apple does not require /\*, and its documentation uses specific paths. Android paths serve Dynamic App Links; the Android app is being prepared.
- **d-mobile-missing** `decided` — What happens when an application has the mobile mixin and the recipe configures no platform?
   1. **Construction fails**
   2. **Each platform registers only if configured; a warning when none is**
   → Choice: **Each platform registers only if configured; a warning when none is** (GP, 2026-11-02) — Platforms are independent: iOS can be ready before Android.
- **d-mobile-place** `decided` — Where does the mobile mixin live?
   1. **Inside kajenn**
   2. **A separate package**
   → Choice: **Inside kajenn** (GP, 2026-11-02) — It adds no dependency.
- **d-grammar-extend** `decided` — Can a class extend an element of an inherited grammar instead of replacing it?
   1. **Yes, with @element(..., extend=True) in genro-builders**
   2. **Not now: only new elements are composed**
   → Choice: **Not now: only new elements are composed** (GP, 2026-11-02) — The mobile mixin needs only new words. extend=True is the chosen form when a case needs it.

### Consequences

- The `_well_known` branch, `WELL_KNOWN_ROOT` and `well_known_names` are removed: a breaking change, released as a minor version.
- The proposal channels-meta-and-server-gui (→ proposal/channels-meta-and-server-gui#d-segment) no longer needs a `_meta/well_known` branch.
- A site whose root application answers `/favicon.ico` today must declare it in that application's `well_known`.
- The guide `docs/guides/hidden-paths.md` is rewritten with one table: for each case, who answers, when construction stops, when the answer is 404 "unimplemented well-known URL". The `{mount}` marker and the MRO merge are documented with examples.
- Tests on the kajenn router cannot prove what an external client sees. A deployment check requests each declared document on the public HTTPS address, through the proxy, and fails on any status other than 200, on a redirect or on a body that does not parse.

### Related documents

- `proposal/channels-meta-and-server-gui` — Reserves the _meta segment inside every routed application
