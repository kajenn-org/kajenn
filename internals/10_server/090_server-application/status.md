# Server application (`_server`) — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## The management application in every server

`AsgiServer.__init__` appends `ServerApplication()` when no application of code
`_server` reached its kwargs (`src/kajenn/asgi_server.py:138-139`). This holds on
the kwargs road and on the configuration road. A server that declares one keeps
the declared one. An application mounted with `spawner="subprocess"` runs in
its dedicated process, and that process mounts no `_server`: the append runs
only when `role` is `None`. The ratification is "D-SA-10 superseded" of
2026-10-10 (SPECIFICATION.md).

`ServerApplication` extends `OpenApiApplication`. Its constructor attaches the
auth, users, tokens, tasks, monitor and databases sections
(`src/kajenn/server_app/server_app.py:242-250`): six sections. The auth section
arrives through `register_auth_method`, the other five through `attach_section`.
Its public descriptor `index` lists the attached names. The password method and
the OIDC providers are registered under the auth section.

Tests: `tests/core/test_server_app_always_present.py::TestTheKwargsRoad::test_a_bare_server_has_the_management_application`,
`::TestTheKwargsRoad::test_a_declared_one_is_kept_as_declared`,
`::TestTheConfigurationRoad::test_a_recipe_naming_nothing_has_it`,
`::TestTheRoleProcess::test_an_application_in_its_dedicated_process_mounts_no_server_application`,
`tests/server_app/test_server_application.py::TestDeclaredMount::test_a_server_declaring_none_has_the_default_one`.

Claim anchors: [`AsgiServer`](../../../src/kajenn/asgi_server.py#AsgiServer), [`ServerApplication`](../../../src/kajenn/server_app/server_app.py#ServerApplication), [`RoutedApplication`](../../../src/kajenn/routed_application.py#RoutedApplication), [`bind_kwargs`](../../../src/kajenn/routed_application.py#RoutedApplication.bind_kwargs).

## `app_class` of the `_server` entry

A recipe entry `application(code="_server", ...)` without `app_class` means
`ServerApplication` (`src/kajenn/config/builder.py:76-92`). An `app_class` that
is not `ServerApplication` or a subclass is a `ValueError` naming the class
(`src/kajenn/config/handler.py:309-311`).

Tests: `tests/core/test_server_app_always_present.py::TestTheConfigurationRoad::test_the_recipe_customises_without_app_class`,
`::TestTheConfigurationRoad::test_the_recipe_may_hand_a_subclass`,
`::TestTheConfigurationRoad::test_a_class_that_is_not_a_server_application_is_refused`.

## Route gates

- Public, no `auth_rule`: `index`, `login`, `logout`, `login_methods`.
- `SUPERADMIN`: every route of users, tokens and tasks.
- `SERVER_ADMIN`: every route of monitor (`MONITOR_RULE`) and databases (`DATABASES_RULE`).
- Bus only: `auth/authenticate` and `auth/forget_credential` answer 403 to an
  HTTP caller (`src/kajenn/server_app/server_sections/auth_section.py:115-116`).

`_request` injection comes from the common `RoutedApplication.bind_kwargs`,
not a private server-app override.

Behavior evidence: [`MonitorSection`](../../../src/kajenn/server_app/server_sections/monitor_section.py#MonitorSection), [`UsersSection`](../../../src/kajenn/server_app/server_sections/users_section.py#UsersSection), [`TokensSection`](../../../src/kajenn/server_app/server_sections/tokens_section.py#TokensSection), [`TasksSection`](../../../src/kajenn/server_app/server_sections/tasks_section.py#TasksSection), [`DatabasesSection`](../../../src/kajenn/server_app/server_sections/databases_section.py#DatabasesSection).

## `ServerApplicationGrammar`: login, oidc, provider

`ServerApplicationGrammar` (`src/kajenn/server_app/server_app.py:121-204`)
declares the words written under the `_server` application element:

- `login(max_attempts, backoff)`: the lockout policy;
- `oidc()`: the collection of providers, keyed by `code`;
- `provider(code, issuer, client_id, client_secret, scopes, identity_claim, tags)`;
- `security_txt(...)`, `robots(sitemaps)` and `agent(user_agent, allow, disallow)`:
  see "Well-known documents" below.

`read_declared_login_surface` reads `login` and `oidc` when the server is
attached (`src/kajenn/server_app/server_app.py:252-298`). Each provider becomes
an `OidcMethod` registered as `oidc:<code>` under `auth`. A hand-built server
passes the same values as the `login=` and `oidc=` constructor kwargs.

Tests: `tests/server_app/test_config_server_app.py::TestLoginSurface`.

## Login lockout

`login` counts failures on the user's `UserStore` record (`failed_attempts`,
`last_failed_at`) (`src/kajenn/server_app/server_app.py:459-532`). After
`max_attempts` failures (default `LOCKOUT_MAX_ATTEMPTS = 5`) the identity is
refused for `backoff * 2**(failed_attempts - max_attempts)` seconds (default
`LOCKOUT_BACKOFF_SECONDS = 30.0`). A refused attempt does not touch the
counter. A success resets it. An unknown identity has no counter.

Tests: `tests/server_app/test_login_flow.py::TestLoginLockout`.

## Users and tokens routes

- users: `list`, `get`, `create_user`, `save`, `set_password`, `delete`
  (`src/kajenn/server_app/server_sections/users_section.py:93-188`).
- tokens: `list`, `issue`, `revoke`, `delete`, `create_jwt`
  (`src/kajenn/server_app/server_sections/tokens_section.py:83-150`).

Tests: [tests/server_app/test_users_section.py](../../../tests/server_app/test_users_section.py),
[tests/server_app/test_tokens_section.py](../../../tests/server_app/test_tokens_section.py).

## Databases section: list, plan, apply

`DatabasesSection` (`src/kajenn/server_app/server_sections/databases_section.py:58`)
answers three routes, all `SERVER_ADMIN`:

- `list`: every registered database with `name`, `owner`, `handler_class`, `capabilities`;
- `plan?name=`: the handler's `migration_plan(name)`;
- `apply?name=` (POST): the handler's `migrate(name)`.

An unknown name is 404. A database whose handler lacks the operation is 422.
Both come from `server.database_handler`. The answer of `plan` and `apply` is
`{"name", "result"}`.

Tests: `tests/server_app/test_databases_section.py::TestDatabasesGate`,
`::TestDatabasesList`, `::TestMigrationRoutes`.

## Well-known documents answered by `_server`

`ServerApplication.well_known` declares three public paths
(`src/kajenn/server_app/server_app.py:228-232`):

- `/.well-known/security.txt` → `security_txt`, built from the `security_txt` word;
- `/robots.txt` → `robots_txt`, built from the `robots` word and its `agent` children;
- `/favicon.ico` → `favicon_ico`, the file `static/favicon.ico` of the site home.

`active_well_known` keeps a path only when its word is written or the file
exists (`src/kajenn/server_app/server_app.py:300-321`). The server indexes only
the kept paths. `security_txt` requires `contact` and an ISO 8601 `expires`.
`robots` requires at least one `agent`; each rule starts with `/` or `*`; each
sitemap is an absolute http(s) URL. A value that fails is a `ValueError` at
construction. A past `expires` is logged as a warning.

Tests: [tests/server_app/test_well_known_documents.py](../../../tests/server_app/test_well_known_documents.py),
[tests/server_app/test_well_known_validation.py](../../../tests/server_app/test_well_known_validation.py),
[tests/server_app/test_well_known_unconfigured.py](../../../tests/server_app/test_well_known_unconfigured.py).

## Attached sections and unfinished administration

`attach_section` links a `RoutingClass` under `/_server/<name>` and lists it in
`sections`. Five of the six shipped sections arrive through that call in
`__init__`; `auth` arrives through `ensure_auth_section`, which calls it too. An
application outside the core attaches its diagnostic surface the same way;
that surface has no route auth rule, so its mounting gate differs from the
monitor's `SERVER_ADMIN` protection.

Per-section configurable tags, a plugin configuration page, general dynamic
application installation and monitor/workbench proposals are not delivered by
the current section list.

Behavior evidence: [`attach_section`](../../../src/kajenn/server_app/server_app.py#ServerApplication.attach_section), [`sections`](../../../src/kajenn/server_app/server_app.py#ServerApplication.sections).

## Source and test evidence

- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn/server_app/server_app.py](../../../src/kajenn/server_app/server_app.py)
- [src/kajenn/server_app/server_sections/__init__.py](../../../src/kajenn/server_app/server_sections/__init__.py)
- [src/kajenn/config/builder.py](../../../src/kajenn/config/builder.py)
- [src/kajenn/config/handler.py](../../../src/kajenn/config/handler.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [tests/core/test_server_app_always_present.py](../../../tests/core/test_server_app_always_present.py)
- [tests/server_app/test_server_application.py](../../../tests/server_app/test_server_application.py)
- [tests/server_app/test_server_monitor.py](../../../tests/server_app/test_server_monitor.py)
- [tests/server_app/test_login_flow.py](../../../tests/server_app/test_login_flow.py)
- [tests/server_app/test_config_server_app.py](../../../tests/server_app/test_config_server_app.py)
- [tests/server_app/test_databases_section.py](../../../tests/server_app/test_databases_section.py)
