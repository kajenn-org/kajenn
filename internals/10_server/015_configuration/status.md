# Configuration — current state

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Recipe, grammar and precedence

`AsgiConfigBuilder` combines the contrib configuration builder with
`AsgiServerGrammar`. A recipe overrides `main(self, root)`. `BaseConfiguration`
supplies package defaults through `server_section` and `storage_mounts`; the
`site:` mount is anchored to the working directory when the recipe runs.

`DefaultConfig.parents_for` layers package defaults, optional machine defaults,
then the site recipe. `default_config=False` opts out of machine defaults; an
explicit missing path raises `ConfigError`. The machine home resolves from the
explicit argument, `KAJENN_HOME`, then `~/.kajenn`.

`ConfigurationHandler` is a read interface. Its inherited precedence is written
value, grammar signature default, call-site default, then `KeyError`. Closed
signatures are read attribute by attribute; open kwargs use resolved runtime
values. `BaseApplication.config` reads relative to `applications.<code>`.
`AsgiServer` consumes the resulting kwargs and builds applications itself.

Claim anchors: [`AsgiConfigBuilder`](../../../src/kajenn/config/builder.py#L60), [`AsgiServerGrammar`](../../../src/kajenn/config/elements.py#L82), [`AsgiServer`](../../../src/kajenn/asgi_server.py#L91), [`BaseConfiguration`](../../../src/kajenn/config/builder.py#L75), [`server_section`](../../../src/kajenn/config/builder.py#L107), [`storage_mounts`](../../../src/kajenn/config/builder.py#L121), [`DefaultConfig`](../../../src/kajenn/config/default_config.py#L64), [`parents_for`](../../../src/kajenn/config/default_config.py#L75), [`ConfigError`](../../../src/kajenn/config/handler.py#L65), [`ConfigurationHandler`](../../../src/kajenn/config/handler.py#L69).

## Server and application vocabulary

The grammar declares server, middleware, authentication, storage, applications,
databases, plugins and OpenAPI sections. The OpenAPI root section has no current
core consumer. `server` includes `shutdown_timeout_seconds`; its children
include session, tasks and websocket. `websocket.origins` is a comma-separated
recipe value converted to a list; `max_concurrent` defaults to 16.

Storage mounts use the storage application's foreign grammar. Each application
mounts its own `app_class.grammar`, so the multiworker SPA's pool is under
`applications.<code>.orchestration.commander`, never a server-level section.
The `authentication` section declares the `users`/`tokens` store descriptors
(their `store_class` is the class the server builds) and the `credentials`
children; it carries no bootstrap password, because the server creates no user.

Claim anchors: [`websocket`](../../../src/kajenn/config/elements.py#L142), [`authentication`](../../../src/kajenn/config/elements.py#L195), [`identity_kwargs`](../../../src/kajenn/config/handler.py#L120).

## Live group settings versus general live configuration

SPA group profiles support live apply and reload. `SpaApplication` composes
defaults, recipe settings, a profile and environment settings. `GroupPolicy`
validates the result before `SpaCommander.apply_group_settings` commits it under
its configuration lock. A named profile requires exactly one group.

See [configuration profiles](../020_applications/configuration_profiles/README.md).

The general writable configuration tree remains unimplemented: there is no
`apply_configuration` or handler mutator and no server subscriber for dynamic
application installation. The group-settings API is a bounded operation on an
existing pool, not evidence that the general live-tree design has landed.

Claim anchors: [`SpaApplication`](../../../src/kajenn_orchestra/spa_app.py#L517), [`GroupPolicy`](../../../src/kajenn_orchestra/orchestration/group_policy.py#L70), [`SpaCommander`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L494), [`apply_group_settings`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L1448).

## Source and test evidence

- [src/kajenn/config/builder.py](../../../src/kajenn/config/builder.py)
- [src/kajenn/config/default_config.py](../../../src/kajenn/config/default_config.py)
- [src/kajenn/config/elements.py](../../../src/kajenn/config/elements.py)
- [src/kajenn/config/handler.py](../../../src/kajenn/config/handler.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn_orchestra/spa_app.py](../../../src/kajenn_orchestra/spa_app.py)
- [src/kajenn_orchestra/orchestration/group_policy.py](../../../src/kajenn_orchestra/orchestration/group_policy.py)
- [src/kajenn_orchestra/orchestration/spa_commander.py](../../../src/kajenn_orchestra/orchestration/spa_commander.py)
- [tests/core/test_config.py](../../../tests/core/test_config.py)
- [tests/core/test_config_env.py](../../../tests/core/test_config_env.py)
- [tests/spa/test_spa_profile_grammar.py](../../../tests/spa/test_spa_profile_grammar.py)
- [tests/spa/test_spa_app_profiles.py](../../../tests/spa/test_spa_app_profiles.py)
- [tests/spa/test_apply_group_settings.py](../../../tests/spa/test_apply_group_settings.py)
