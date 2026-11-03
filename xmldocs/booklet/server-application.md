<!-- generated from server-application.xml by xmldocs: do not edit, edit the XML -->

## The server application _server: what it is for, what it answers, how to configure it
*Instruction booklet · booklet/server-application · 2026-11-03*

### Purpose

Every kajenn server carries one management application, `_server` (class `ServerApplication`, package `kajenn.server_app`). It is the system surface of the server: the login surface for humans, the management of users, credentials, tasks and databases, the monitor, the credential check that channels call, and three site documents (`security.txt`, `robots.txt`, `favicon.ico`).

This booklet states what `_server` does today on `develop`: why it exists, every route it answers and who may call it, every recipe word that configures it, how other code extends it, who uses it, and where its browser interface lives. Every fact carries its source.

### Requirements

- kajenn on `develop` after merge 3f5588d (2026-11-03).
- For login and user management: an `authentication().users(...)` store in the recipe.
- For OIDC: `server(external_url=...)` in the recipe.

#### 1. Why it exists

kajenn keeps everything that asks a human for a user and a password out of the core. The core authenticates presented credentials through `kajenn.auth` and does not import `kajenn.server_app`; `_server` is the application that owns the login surface and the management routes, mounted like any other application.

- `READ` `src/kajenn/server_app/__init__.py:15` · 2026-11-03 — The package is the system surface a server exposes under `/_server`. It holds what asks a human for a user and a password; `import kajenn` loads none of it.
- `READ` `src/kajenn/server_app/server_app.py:207` · 2026-11-03 — `ServerApplication` extends `OpenApiApplication`, which extends `RoutedApplication`: its routes are ordinary routes with an OpenAPI description at `/_server/_meta/schema_json`.
- `READ` `src/kajenn/server_app/server_app.py:227` · 2026-11-03 — `code` and `mount` are both `_server`, fixed on the class: `PasswordMethod` names `/_server/login` as its form action.

#### 2. Know when it is mounted

A server always has `_server`. The recipe never declares it: it only customises it.

- No application of code `_server` in the recipe or the kwargs → the server appends a default `ServerApplication()`.
- `application(code="_server")` without `app_class` → `ServerApplication` with the words written under it.
- `application(code="_server", app_class=MyServerApp)` → accepted only when `MyServerApp` subclasses `ServerApplication`; otherwise the server refuses to build.
- The dedicated process of an application declared with `spawner="subprocess"` mounts no `_server`: it hosts that application alone.

- `READ` `src/kajenn/asgi_server.py:138` · 2026-11-03 — When the process has no role and no application has code `_server`, a default `ServerApplication()` is appended to the applications.
- `READ` `src/kajenn/config/builder.py:90` · 2026-11-03 — `application(code="_server")` without `app_class` takes `ServerApplication`.
- `READ` `src/kajenn/config/handler.py:309` · 2026-11-03 — An `app_class` on code `_server` that is not a `ServerApplication` subclass raises `ValueError` naming the class.
- `TEST` `tests/core/test_server_app_always_present.py` · 2026-11-03 — A bare server, a recipe naming nothing and a recipe customising `_server` all mount it; the dedicated process of a spawned application mounts none.

#### 3. Know what it answers and who may call it

Access is decided per route by its `auth_rule`: no identity answers 401, an identity without the tag answers 403. A route with an `openapi_method` refuses other methods with 405; a route without one answers every method.

*Access summary*

| Access | Routes |
|---|---|
| Public | `/_server/` (index), `login`, `logout`, `login_methods`, `_meta/*`, OIDC `start` and `callback`, the three well-known documents |
| KajennBus only (HTTP answers 403) | `auth/authenticate`, `auth/forget_credential` |
| `SUPERADMIN` | `users/*`, `tokens/*`, `tasks/*` |
| `SERVER_ADMIN` | `monitor/*`, `databases/*` |

#### The application itself

| Path | Method | What it does |
|---|---|---|
| `/_server/` | any | Descriptor: `{title, sections}`, the attached section names. |
| `/_server/login` | POST | `identity`, `password`. Checks the lockout, verifies the password against the user store, attaches an `Avatar` to the current session. Answers `{session_id, identity, tags}`, or `{error}` with `remaining_attempts` for a known identity. Without a user store: "Login is not available". |
| `/_server/logout` | any | Deletes the session `session_id`; answers `{"status": "ok"}`. |
| `/_server/login_methods` | any | `{methods: [...]}`: one descriptor per registered login method (id, kind, label, action), so a front-end draws the login screen without knowing the configuration. |
| `/_server/_meta/schema_json`, `/_server/_meta/docs`, `/_server/_meta/index` | any | OpenAPI 3.1 document, Swagger UI, HTML index (from `OpenApiApplication`). |

- `READ` `src/kajenn/server_app/server_app.py:455` · 2026-11-03 — `index` answers the title and the sorted section names.
- `READ` `src/kajenn/server_app/server_app.py:463` · 2026-11-03 — `login` is POST, takes `identity` and `password`, and attaches the avatar to the current session; the session id does not change.
- `READ` `src/kajenn/server_app/server_app.py:538` · 2026-11-03 — `logout` deletes the session it is given.
- `READ` `src/kajenn/server_app/server_app.py:557` · 2026-11-03 — `login_methods` is public by design: it lists the descriptors of the registered methods.

#### auth: credential check for channels

| Path | Method | What it does |
|---|---|---|
| `/_server/auth/authenticate` | POST, bus only | `credential`, `channel`. Verifies a presented credential through `AuthCore`; answers `{identity, tags, data, valid_until}`. It is the default `authentication_route` of every channel. |
| `/_server/auth/forget_credential` | POST, bus only | Drops a credential from the caches of every process. |
| `/_server/auth/oidc:<code>/start` | any | Starts the OIDC authorization code flow with PKCE; 302 to the provider. |
| `/_server/auth/oidc:<code>/callback` | any | Checks the state, exchanges the code, verifies the id\_token, attaches the avatar; 302 to the safe `next` path. |

- `READ` `src/kajenn/server_app/server_sections/auth_section.py:108` · 2026-11-03 — `authenticate` and `forget_credential` answer 403 to an HTTP caller: only the KajennBus reaches them.
- `READ` `src/kajenn/auth/mixin.py:78` · 2026-11-03 — `DEFAULT_AUTHENTICATION_ROUTE` is `/_server/auth/authenticate`.
- `READ` `src/kajenn/server_app/oidc_method.py:164` · 2026-11-03 — OIDC `start` stores state, PKCE verifier and the safe `next` in the session and redirects to the provider; `callback` (line 207) completes the login.

#### users (SUPERADMIN)

| Route | Method | What it does |
|---|---|---|
| `users/list` | any | Every user record without its password hash. |
| `users/get` | any | One record by `identity`. |
| `users/create_user` | POST | New user: password and confirmation, metadata, tags; the scrypt hash is computed on the server. |
| `users/save` | POST | Merges metadata into an existing record; the hash is untouched. |
| `users/set_password` | POST | Replaces the password. |
| `users/delete` | POST | Deletes the user. |

- `TEST` `tests/server_app/test_users_section.py` · 2026-11-03 — The CRUD answers 401 anonymous and 403 without `SUPERADMIN`; without a store every route answers `{"error": "User management is not available"}`.

#### tokens (SUPERADMIN)

| Route | Method | What it does |
|---|---|---|
| `tokens/list` | any | Every API key record without its secret hash. |
| `tokens/issue` | POST | `label`, `tags`, `expires_at`. Answers the key `gak_...` once only. |
| `tokens/revoke`, `tokens/delete` | POST | By `key_id`; both empty the credential cache in every process. |
| `tokens/create_jwt` | POST | `sub`, `tags`, `expires_in`. Signs with the first HS\* verifier of the recipe. |

- `TEST` `tests/server_app/test_tokens_section.py` · 2026-11-03 — API keys and JWT minting answer 401 anonymous and 403 without `SUPERADMIN`.

#### tasks (SUPERADMIN)

| Route | Method | What it does |
|---|---|---|
| `tasks/list` | any | The schedules. |
| `tasks/create`, `tasks/update` | POST | A schedule: `code`, `kind`, `spec`, `task_name`, `kwargs`, `enabled`; the next run is computed. |
| `tasks/enable`, `tasks/disable`, `tasks/run_now`, `tasks/delete` | POST | By schedule `code`. |
| `tasks/logs` | any | The log of a task by name, with a limit. |
| `tasks/spool_list`, `tasks/progress`, `tasks/result` | any | Queued and running tasks, by owner or status; progress and result by `task_id`. |
| `tasks/cancel` | POST | Cancels a task by `task_id`. |

- `READ` `src/kajenn/server_app/server_sections/tasks_section.py:26` · 2026-11-03 — Every route is `SUPERADMIN`; a server with tasks disabled answers `{"error": "Tasks are disabled"}` with status 200.

#### monitor (SERVER_ADMIN)

| Route | Media type | What it does |
|---|---|---|
| `monitor/snapshot` | JSON | `{server, apps}`: host, configured port, pid, application and section names; per application its `app_snapshot`. |
| `monitor/panels` | JSON | Per application its panel descriptor, with the URL of its panel module. |
| `monitor/panel?app=<key>` | text/javascript | The panel ES module an application provides, or 404. |

- `READ` `src/kajenn/server_app/server_sections/monitor_section.py:71` · 2026-11-03 — `MONITOR_RULE` is `SERVER_ADMIN`.
- `READ` `src/kajenn/application.py:352` · 2026-11-03 — The base `app_snapshot` is `{class, code, mount}`; no application in `src/` extends it, and no class defines `panel_source`.

#### databases (SERVER_ADMIN)

| Route | Method | What it does |
|---|---|---|
| `databases/list` | any | Every database: name, owner, handler class, capabilities. |
| `databases/plan` | any | The migration plan of `name`. |
| `databases/apply` | POST | Applies the migration of `name`. |

- `READ` `src/kajenn/server_app/server_sections/databases_section.py:71` · 2026-11-03 — An unknown database answers 404; a handler without the capability answers 422.

#### Well-known documents

| Public path | Route | Given as default when |
|---|---|---|
| `/.well-known/security.txt` | `security_txt` | the `security_txt` word is written |
| `/robots.txt` | `robots_txt` | the `robots` word is written |
| `/favicon.ico` | `favicon_ico` | `static/favicon.ico` exists in the site home |

The three documents are defaults (`default_well_known`): an application that declares one of these paths in its `well_known` answers it instead, for example a favicon per host.

- `READ` `src/kajenn/server_app/server_app.py:302` · 2026-11-03 — `active_default_well_known` builds and validates the two documents and drops every path whose word or file is absent; the full mechanism is in `docs/guides/hidden-paths.md`.
- `TEST` `tests/server_app/test_well_known_documents.py` · 2026-11-03 — An application declaring `/favicon.ico` answers it even when `static/favicon.ico` exists.

#### 4. Configure it

The words of `_server` are written under its application element. The recipe customises it; it never has to declare it.

**Example — A recipe customising _server**

```python
server_app = apps.application(code="_server")
server_app.login(max_attempts=3, backoff=10)
server_app.oidc().provider(
    code="corp",
    issuer="https://idp.example.com",
    client_id="corp-client",
    client_secret=EnvResolver("CORP_OIDC_SECRET"),
)
server_app.security_txt(contact="mailto:security@example.com",
                        expires="2027-06-30T00:00:00Z")
robots = server_app.robots(sitemaps="https://example.com/sitemap.xml")
robots.agent(user_agent="*", disallow="/admin/")
```

*Words of ServerApplicationGrammar*

| Word | Parameters | Effect |
|---|---|---|
| `login` | `max_attempts` (default 5), `backoff` seconds (default 30) | Lockout after consecutive failures; the window doubles at each further failure. |
| `oidc` / `provider` | `code` (required, the collection key), `issuer`, `client_id`, `client_secret` (a resolver; optional for a public client), `scopes="openid email profile"`, `identity_claim="email"`, `tags` | One login method per provider, mounted at `/_server/auth/oidc:<code>/`. |
| `security_txt` | `contact`, `expires` (required); `encryption`, `acknowledgments`, `preferred_languages`, `canonical`, `policy`, `hiring` | `/.well-known/security.txt`, validated at boot. |
| `robots` / `agent` | `sitemaps`; per agent `user_agent="*"`, `allow`, `disallow` | `/robots.txt`, validated at boot. |
| `parameters`, `request`, `databases` | inherited from `ApplicationGrammar` | As for any application. |

*Server-level words that _server depends on*

| Word | Why `_server` needs it |
|---|---|
| `authentication().users(...)` | The user store behind `login` and `users/*`. |
| `authentication().tokens(...)` | The API key store behind `tokens/*`. |
| `authentication().credentials().jwt(secret=...)` | The key `tokens/create_jwt` signs with. |
| `server(external_url=...)` | Required with any OIDC provider: the server refuses to boot without it. |
| `site(home=...)` | The folder whose `static/favicon.ico` is served. |
| `channels().channel(name, authentication_route=...)` | Replaces `/_server/auth/authenticate` for one channel. |

- `READ` `src/kajenn/server_app/server_app.py:106` · 2026-11-03 — `LOCKOUT_MAX_ATTEMPTS = 5`, `LOCKOUT_BACKOFF_SECONDS = 30.0`.
- `READ` `src/kajenn/server_app/server_app.py:437` · 2026-11-03 — The lock window is `backoff * 2 ** (failed - max_attempts)` seconds from the last failure; refused attempts while locked do not count, an unknown identity has no counter, a success resets it.
- `READ` `src/kajenn/asgi_server.py:265` · 2026-11-03 — A recipe with an OIDC provider and no `external_url` is refused at boot.
- `TEST` `tests/server_app/test_config_server_app.py` · 2026-11-03 — The `login` and `oidc` words of a recipe reach the instance; `client_secret` goes through the read stack.

#### 5. Extend it

Another package adds routes to `_server` by attaching a section, and adds a login method by registering it. Both are public methods of `ServerApplication`.

| Method | What it does |
|---|---|
| `attach_section(section, name)` | Mounts a `RoutingClass` at `/_server/<name>/` and lists it in `sections` and in the index. |
| `sections` | The attached sections by name. |
| `register_auth_method(method)` | Adds a login method (kind `form`, `redirect` or `ceremony`); a duplicate id raises `ValueError`. |
| `auth_section`, `ensure_auth_section()` | The `auth` section, created on first need. |
| `app_snapshot`, `app_panel`, `panel_source` on a section | A section declaring them appears in the monitor as `_server/<name>`. |
| `app_class` subclass on code `_server` | Replaces the whole application with a subclass. |

- `READ` `src/kajenn/server_app/server_app.py:415` · 2026-11-03 — `attach_section(section, name)` adds the branch and records the section.
- `READ` `src/kajenn/server_app/server_app.py:433` · 2026-11-03 — `register_auth_method` registers on the auth section.
- `TEST` `tests/server_app/test_server_application.py` · 2026-11-03 — A section attached with `attach_section` answers under `/_server/<name>/`.

#### 6. Know who uses it

| User | What it uses |
|---|---|
| Every channel of the server (REST, MCP, WSX, KajennBus) | `auth/authenticate`, to turn a presented credential into an identity. |
| A spawned application in its dedicated process | The parent's `_server`: it mounts none of its own. |
| A human through a browser | Login (password or OIDC), then the management routes through a GUI. |
| Scripts and operators | The REST routes with an API key or a JWT; `docs/guides/management.md` shows them with curl. |
| kajenn-orchestra | Attaches its `inspector` section when the inspector is enabled. |
| kajenn-press | Calls `login` in-process from its admin views. |
| kajenn-deploy | Health check of the container on `GET /_server`. |

- `READ` `kajenn-orchestra:src/kajenn_orchestra/spa_app.py:831` · 2026-11-03 — kajenn-orchestra attaches `InspectorSection` under `inspector` when the inspector is enabled.
- `READ` `kajenn-press:src/kajenn_press/admin/views.py:55` · 2026-11-03 — kajenn-press calls `application_at("_server").login(...)` in-process.
- `READ` `kajenn-deploy:local-https/compose.yaml:13` · 2026-11-03 — The local HTTPS container checks its health on `/_server`.
- `READ` `src/kajenn/__main__.py:754` · 2026-11-03 — The `kajenn` command line has no command that calls `/_server`; `kajenn db` runs the migrations in-process.

#### 7. Find its browser interface

The browser interface of `_server` is not in kajenn. It lives in the repository `kajenn-org/kajenn-serverapp-gui`, today a placeholder holding the first version, built with gramlot and imported from a kajenn working tree. kajenn does not depend on gramlot.

The accepted proposal channels-meta-and-server-gui (→ proposal/channels-meta-and-server-gui#d-gui-shape) fixes the shape the GUI takes:

- The recipe enables it with `gui(gui_class=...)`; without the element there is no GUI and the core never names the package.
- `ServerApplication` builds `gui_class(self)` and passes every request on `ui/...` to it: the URL is `/_server/ui/`. The component releases its resources in `on_shutdown`.
- The GUI reads and writes only through the REST routes of `_server`, like any other client.
- Extension pages register on the GUI component when it exists; gramlot is an optional extra of the extension.

The list of what the GUI must cover is in that repository, in `xmldocs/proposal/server-gui-scope.xml`.

- **d-gui-built** `open` — When is the GUI component built as the proposal fixes it?
   1. **Not decided here**: The work belongs to kajenn-serverapp-gui and its scope proposal.

### Rules

- **must-not** — Declare `_server` to make it exist. *(The recipe customises _server; the server mounts it anyway.)*
- **must** — Subclass `ServerApplication` when replacing `_server` through `app_class`. *(The configuration handler refuses any other class.)*
- **must** — Write `server(external_url=...)` when any OIDC provider is configured. *(The server refuses to boot without it.)*
- **must-not** — Import a GUI package from kajenn code. *(kajenn does not depend on gramlot.)*
- **should** — Give every GUI need a REST route on `_server` before building the page. *(The proposal makes REST the only door of the GUI.)*

### Current limits

- The `logout` docstring says POST, but the route declares no method and answers every one; it has no `auth_rule` and deletes any session id it is given. · `src/kajenn/server_app/server_app.py:538`
- The package docstring still says nothing is mounted automatically; the server mounts `_server` always. `tests/server_app/test_config_server_app.py` line 15 says the same. · `src/kajenn/server_app/__init__.py:27`
- The monitor serves data only: no page. The per-application snapshot holds class, code and mount; pools, requests, websockets and databases are not in it. · `src/kajenn/server_app/server_sections/monitor_section.py:27`
- With tasks disabled the tasks routes answer an error with status 200, not an error status. · `src/kajenn/server_app/server_sections/tasks_section.py:52`
- No route reads or edits the effective configuration, the sessions, the logs or the application list with versions: a GUI cannot show them through REST today.
- No route of `_server` declares a channel: the MCP face of the proposal is not there yet.

### Related documents

- `proposal/channels-meta-and-server-gui` (proposal) — Channel faces on every routed application, one _meta segment, and the server GUI as a separate component
- `proposal/well-known-urls` (proposal) — Well-known URLs declared by applications
