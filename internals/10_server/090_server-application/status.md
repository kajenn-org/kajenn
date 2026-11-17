# Server application (`_server`) — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.
Authentication passages updated against `dfc3941`.

## The management application in every server

`AsgiServer.__init__` appends `ServerApplication()` when no application of code
`_server` reached its kwargs (`src/kajenn/asgi_server.py:138-139`). This holds on
the kwargs road and on the configuration road. A server that declares one keeps
the declared one. An application mounted with `spawner="subprocess"` runs in
its dedicated process, and that process mounts no `_server`: the append runs
only when `role` is `None`. The ratification is "D-SA-10 superseded" of
2026-10-10 (SPECIFICATION.md).

`ServerApplication` extends `RoutedApplication`, so it carries the `_meta`
branch and the MCP face: `/_server/_meta/api/schema_json`,
`/_server/_meta/api/docs` and MCP JSON-RPC on `/_server/_meta/mcp`. Its
constructor attaches six sections through `attach_section`: tasks, monitor,
databases, k8s, applications and metrics. Its public `index` descriptor lists
the attached names. Login and identity management belong to `_auth`.

Claim anchor: [`ServerApplication.__init__`](../../../src/kajenn/server_app/server_app.py#ServerApplication.__init__).

Tests: `tests/core/test_server_app_always_present.py::TestTheKwargsRoad::test_a_bare_server_has_the_management_application`,
`::TestTheKwargsRoad::test_a_declared_one_is_kept_as_declared`,
`::TestTheConfigurationRoad::test_a_recipe_naming_nothing_has_it`,
`::TestTheRoleProcess::test_an_application_in_its_dedicated_process_mounts_no_server_application`,
`tests/server_app/test_server_application.py::TestDeclaredMount::test_a_server_declaring_none_has_the_default_one`,
`tests/server_app/test_server_application.py::TestServerEndpoints::test_meta_schema_json_is_exposed`,
`tests/core/test_mcp_application.py::TestRoutedApplication::test_the_server_application_has_an_mcp_face`.

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

- Public: `index` and the k8s probes.
- `SERVER_ADMIN`: tasks, monitor, databases and applications.
- `SERVER_METRICS`: metrics.

`_request` injection comes from `RoutedApplication.bind_kwargs`.
The authenticate, avatar and forget-credential bus-only routes belong to
`_auth`; its identity management sections ask for `AUTH_ADMIN`.

## Application grammar and authentication

`ServerApplicationGrammar` declares `security_txt`, `robots` and `agent`.
The separately mounted `AuthApplicationBase` owns the `store`, `login`,
`oidc`/`provider` and `self_registration` words, and the `cache_ttl` attribute.
Its login checks passwords with a store-backed lockout and attaches the avatar
to the existing session. Users, groups, tags and tokens are managed under
`/_auth/`, not under this application.

Claim anchors: [`ServerApplicationGrammar`](../../../src/kajenn/server_app/server_app.py#ServerApplicationGrammar), [`AuthApplicationGrammar`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationGrammar).

## Fatal boot with the public applications closed

With `abort_on_fatal_boot_error=False`, a fatal startup closes the public
applications. `BaseServer.answers_when_public_closed` allows management;
`AsgiServer` overrides it to allow both `_server` and `_auth`, so management
can still authenticate. The ordinary application-enabled check still applies.

Claim anchors: [`BaseServer.answers_when_public_closed`](../../../src/kajenn/server.py#BaseServer.answers_when_public_closed), [`AsgiServer.answers_when_public_closed`](../../../src/kajenn/asgi_server.py#AsgiServer.answers_when_public_closed).

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
`sections`. All six shipped sections arrive through that call in
`__init__`. An
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
- [tests/server_app/test_config_server_app.py](../../../tests/server_app/test_config_server_app.py)
- [tests/server_app/test_databases_section.py](../../../tests/server_app/test_databases_section.py)
