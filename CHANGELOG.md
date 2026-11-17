# Changelog

All notable changes to kajenn are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Releases before 0.9.0 are listed
on [GitHub Releases](https://github.com/kajenn-org/kajenn/releases).

## [0.10.0] - 2026-11-17

### Breaking

- Authentication belongs to the application mounted with code `_auth`. A
  server that does not mount it authenticates nobody: a presented
  `Authorization` header answers 401 and a session gives no identity. A site
  that authenticates adds
  `application(code="_auth", app_class=AuthApplicationBase)` to its recipe, or
  `AuthApplicationBase` to `applications=`.
- The `_server` application has no login, no users and no tokens. The routes
  `/_server/login`, `/_server/logout`, `/_server/login_methods`,
  `/_server/users/...`, `/_server/tokens/...` and `/_server/auth/...` are
  removed. A client calls `/_auth/login`, `/_auth/logout`,
  `/_auth/login_methods`, `/_auth/users/...` and `/_auth/tokens/...`. The
  bus-only `/_server/auth/authenticate` and `/_server/auth/forget_credential`
  become `/_auth/authenticate` and `/_auth/forget_credential`. New routes:
  the bus-only `/_auth/avatar`, and the management of `/_auth/groups/...` and
  `/_auth/tags/...`.
- The OIDC routes move from `/_server/auth/oidc:<code>/start` and
  `/callback` to `/_auth/oidc:<code>/start` and `/callback`. The
  `redirect_uri` registered with each provider becomes
  `<external_url>/_auth/oidc:<code>/callback`.
- The server kwargs `auth`, `users`, `tokens` and `channels`, and the recipe
  elements `authentication` (with `users`, `tokens`, `credentials`,
  `basic_user`, `bearer_token`, `jwt`, `cache_ttl`) and `channels` (with
  `channel(name, authentication_route)`), are removed. A recipe that still
  writes them fails with the error of the grammar. The store is written with
  the word `store(store_class=..., ...)` under the `_auth` element, and
  `cache_ttl` as an attribute of that element.
- The words `login` and `oidc` (with `provider`) of the `_server` grammar move
  to the `_auth` grammar: `_server_app.login(...)` becomes
  `auth_app.login(...)`, `_server_app.oidc().provider(...)` becomes
  `auth_app.oidc().provider(...)`. The provider attributes `tags` and
  `identity_claim` are removed: the user of a provider login is found in the
  store, from the verified email the first time and from `iss` + `sub` after;
  an unknown user is refused unless `self_registration(status, tags)` is
  written.
- The `login` and `oidc` keyword arguments of `ServerApplication` are removed:
  a hand-built `(ServerApplication, {"login": {...}})` or
  `(ServerApplication, {"oidc": {...}})` raises `TypeError`. They move to the
  `_auth` application: `(AuthApplicationBase, {"login": ..., "oidc": ...})`.
- Static credentials are removed: `basic_user`, `bearer_token`, JWT verifiers
  with a shared secret and the route `/_server/tokens/create_jwt`. A `Basic`
  header is verified on the users of the `_auth` store. A fixed bearer becomes
  a `gak_` token of a system user. A JWT issued elsewhere is verified by a
  subclass of `AuthApplicationBase` that overrides `verify_credential`.
- `AuthCore`, `UserStore`, `FileUserStore`, `ApiKeyStore` and
  `FileApiKeyStore` are removed from `kajenn` and `kajenn.auth`. Code that
  imports them imports `AuthStore` and `FileAuthStore` from `kajenn`: one store
  for users, groups, tags and tokens. A custom backend subclasses `AuthStore`
  and implements its persistence methods (`write_user`, `save_group`,
  `save_tag`, `save_token`, the `get_*`, `delete_*` and `*_list` members,
  `get_login_failures`, `save_login_failures` and `delete_login_failures`).
  The lockout counters live in a `login_failures` record keyed by identity,
  not in the user record.
- The server kwarg `credential_cache_ttl` is removed:
  `AsgiServer(..., credential_cache_ttl=5)` raises `TypeError`. The lifetime
  of the cached authentication answers is the `cache_ttl` attribute of the
  `_auth` application (`application(code="_auth", ..., cache_ttl=5)`, or
  `(AuthApplicationBase, {"cache_ttl": 5})` in `applications=`). The read-only
  property `server.credential_cache_ttl` stays and returns that value.
- The server members `auth_core`, `user_store`, `api_key_store`, `channels`
  and `authentication_route(channel)` are removed. Code that read users or
  tokens through `server.user_store` or `server.api_key_store` reads
  `server.applications["_auth"].store`. The channel of a request is still
  passed to `/_auth/authenticate`; there is no per-channel authentication
  route.
- `ConfigurationHandler` loses `identity_kwargs()`, `channels()` and
  `auth_entries()`. The `_auth` application reads its own nodes
  (`store`, `login`, `oidc`, `self_registration`) in
  `read_declared_configuration(handler)`; code that called these methods
  reads the nodes under `applications._auth` with `handler.node(...)` and
  `handler.open_attrs(...)` / `handler.closed_attrs(...)`.
- `ServerApplication` loses `login_policy`, `oidc_providers`,
  `auth_section`, `register_auth_method` and `read_declared_login_surface`.
  A `_server` subclass that used them moves that code
  to a subclass of `AuthApplicationBase`, which has `login_policy`,
  `oidc_providers`, `register_auth_method`, `auth_method_registry` and
  `read_declared_configuration(handler)`; its management sections are in
  `sections`.
- `RemoteApplication.mark_joined()` takes two arguments,
  `mark_joined(declared_tags, system_users)`: the `declared_tags` and
  `system_users` the spawned process presented in its `joined` message. A
  caller that passed none passes the two lists (empty when the application
  declares nothing).
- `AuthMethod`, `PasswordMethod`, `OidcMethod`, `AuthSection`,
  `UsersSection`, `TokensSection` and `safe_next_path` are removed from
  `kajenn.server_app`. The login methods import from `kajenn.auth_app`;
  `OidcMethod.get_safe_next_path` replaces `safe_next_path`.
- The data written by earlier versions is not read and not converted:
  `FileAuthStore` reads only `site:auth`, and the records under `site:users`
  and `site:api_keys` are ignored. A site starts with an empty store; the
  server log carries the password of the temporary administrator
  `_temporary_admin`, with which the users and tokens are created again.
- The user record has `status` (`waiting`, `confirmed`, `banned`) in place of
  `enabled`, and the fields `groups`, `primary_group`, `system`, `email`,
  `external_ids`. Only `confirmed` authenticates.
- A token belongs to a user (`user`, required) and carries `"*"` or a
  non-empty list of tags, a subset of the user's tags. `label` is removed. The
  body of `/_auth/tokens/issue` is `{user, tags, expires_at}`.
- `SUPERADMIN` is removed. The management of users, groups, tags and tokens
  asks `AUTH_ADMIN`; the `_server` sections, tasks included, ask
  `SERVER_ADMIN`. A user that held `SUPERADMIN` receives `AUTH_ADMIN`, and
  `SERVER_ADMIN` for the tasks.
- The management refuses a tag that has no tag record: a user, a group or a
  token naming one is not written. The tags an application uses are declared
  in its `declared_tags` property, or created with `/_auth/tags/create_tag`.
- The session stores the identity and the group of an avatar, never its tags:
  at every request the server asks `/_auth/avatar` for the tags, and a refused
  identity loses its session. `dump()` writes `avatars: {key: {identity,
  group}}`. A handler that attaches an `Avatar` to a session attaches an
  identity of the `_auth` store.
- A session dump or snapshot written by an earlier version cannot be loaded.
  `MemorySessionStore.restore()` reads `{identity, group}` for each avatar and
  fails on a dump that holds `{identity, tags}`; an `Avatar` unpickled from an
  earlier `save_snapshot` has no `group`, and every request carrying its
  session cookie fails until the session expires. Whoever upgrades deletes the
  session dump and the snapshot file before the first start.
- `avatar.data` lives for one request: every request gets its own `Avatar`.
  Data that must last for the session is written in `session.data`.
- `Avatar` has a `group` field. The answers of `/_auth/authenticate` and
  `/_auth/avatar`, and `meta["auth"]` on the KajennBus, are
  `{identity, group, tags, ...}`. A custom authentication answer adds `group`.
- `kajenn_credential_checks_total` is counted by `_auth`
  (`/_auth/authenticate`, `/_auth/login`, the OIDC callback). The `scheme`
  label is read from the form of the credential: an accepted check is
  `basic`, `api_key`, `password` or `oidc`; the accepted `jwt` and `bearer`
  labels no longer appear.

### Changed

- A server with an OIDC provider and no `external_url` starts: the boot
  check is removed. Its OIDC routes answer 404 and `/_auth/login_methods`
  leaves the provider out until `external_url` is set (#151).

### Added

- The `_auth` application (`AuthApplicationBase`, `AuthStore`,
  `FileAuthStore`): users with groups, tags and status, a group per session,
  tokens of a user, tag records declared by the applications
  (`declared_tags`, `system_users`), management under `/_auth/...` asking
  `AUTH_ADMIN`, OIDC login with optional `self_registration`, and the
  temporary administrator `_temporary_admin` for a site without one. A write
  of the users or groups management empties the credential cache at once.
- The server capability `external_links` (`ServerCapabilities`, the
  genro-routes `env` plugin on every `RoutedApplication`): on when the server
  has an `external_url`. A route that builds an external link declares
  `env_requires="external_links"`. The default template and the recipe
  written by `kajenn configure` read `EnvResolver("KAJENN_EXTERNAL_URL",
  default="")`; `kajenn configure --external-url` writes a fixed value (#151).
- `GET /_server/metrics`, a Prometheus exporter asking `SERVER_METRICS`, and
  the `prometheus_metrics()` broadcast hook of every application. Families of
  the process, HTTP requests, credential checks, MCP calls, WSX messages,
  websocket connections, the work pool, tasks, the scheduler, spawned
  processes, kbus timeouts and the startup. `prometheus_client` is a required
  dependency (#141).
- `server.applications_broadcast(method_name, ...)` with `@broadcast_hook`,
  and `RemoteApplication.remote_call` for applications with a `spawner`
  (#142).
- `plugin_configuration(plugin=...).configure(route=..., ...)` in a recipe
  gives plugin options to the routes of an application, without changing
  its code.

### Fixed

- A node written inside an application on a running server no longer raises
  `KeyError: 'app_class'`.

## [0.9.2] - 2026-11-11

### Breaking

- `Request` requires the `application` keyword, and `bound_application()`
  is removed: a request always has an application. Code that builds a
  `Request` passes the application it belongs to (#11).
- `server.requests.run_cleanups()` takes no argument. A caller that passed
  the error drops it (#12).

### Changed

- The parameter of `server.session(...)` is named `scope`, not `request`:
  it reads a scope. A call by keyword uses `session(scope=...)` (#7).
- The listing method of the `_server` sections is `<noun>_list`
  (`application_list`, `database_list`, `schedule_list`, `token_list`,
  `user_list`). The URLs `/_server/<section>/list` do not change (#8).

### Added

- `kajenn.WebSocketDisconnect` is exported from the package, and
  `kajenn.server` exports `SHUTDOWN_TIMEOUT_SECONDS` (#10).
- The container image `ghcr.io/kajenn-org/kajenn`: kajenn and kajenn-db on
  `python:3.13-slim`, started with `kajenn serve` on port 8080. Each release
  is tagged with its version and `latest`; a nightly `develop-YYYYMMDD`
  image is published when the nightly run of `develop` passes.

### Fixed

- A custom session store that subclasses `SessionStore` no longer fails at
  shutdown under `kajenn serve --name`: `SessionStore` declares
  `save_snapshot` and `load_snapshot` with default bodies (#9).

## [0.9.1] - 2026-11-10

### Fixed

- An application with a catch-all route (`index(self, *segments, **query)`)
  starts again: only a route declared under the name `_meta` is refused, and
  `_meta/...` reaches the meta branch (#123).

## [0.9.0] - 2026-11-10

### Breaking

- `OpenApiApplication`, `McpOpenApiApplication`, `McpApplication` and the
  mounted mode (`routing_class`, `module`, `api_name`) are removed, with no
  alias. An application attaches its routing class in its own constructor
  and is a `RoutedApplication` (#111).
- The HTTP channel `rest` is renamed `api`. A route that declares no channel
  answers on `api` only (#111).
- A route named `_meta` is refused (#111).
- Every application but `_server` answers 503 with `Retry-After` until it is
  ready. The base class sends `ready` when its activation ends; an
  application that needs more overrides it (#112).
- An `on_startup` that raises leaves its application disabled instead of
  stopping the boot. An application whose failure must stop the server is
  declared `mandatory` (#112).

### Added

- Every `RoutedApplication` has the faces `_meta/index`,
  `_meta/api/schema_json`, `_meta/api/docs`, `_meta/mcp/tools` and answers
  MCP JSON-RPC on `_meta/mcp`. `_server` gains the same MCP face (#111).
- Applications added, switched off and removed on a running server: section
  `_server/applications` with `add`, `ready`, `disable`, `enable`, `remove`,
  `list`, administrator role. Writing or taking away a node of the
  `applications` branch of the live configuration does the same (#112).
- Hook `on_remove`, awaited when an application is removed (#112).
- Attribute `mandatory` on the `application` node: when a mandatory
  application fails, only `_server` answers and `/readyz` is not ready (#112).
- Option `abort_on_fatal_boot_error` on the `server` element (default
  `True`); with `False` a `FatalBootError` keeps the server up for `_server`
  only (#112).
- `server.application_failed`: an application gives up and removes itself
  (#112).

### Changed

- The 0.8.0 URLs `_meta/schema_json`, `_meta/docs` and `mcp` give the new
  answer with a `Deprecation` header, a `successor-version` link and a log
  line (#111).
- A disabled application answers 503 without `Retry-After`; a removed one
  answers 404 (#112).

### Fixed

- A GET on a POST route of an application answers 405 instead of 200: the
  `openapi` and `pydantic` plugins are attached before any route is resolved
  (#111).
