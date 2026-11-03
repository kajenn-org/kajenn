# Configuration — current state

**Version**: 0.4 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Recipe, grammar and precedence

`AsgiConfigBuilder` combines the contrib configuration builder with
`AsgiServerGrammar`. A recipe overrides `main(self, root)`.
`AsgiConfigBuilder` carries the `site_section` and `storage_mounts` hooks.
`site_section` writes the `site` section only when the recipe sets `site_name`
or `site_home`. `storage_mounts` writes the default layout: `site:` anchored to
the working directory when the recipe runs, and `home:` on `site_home`, or on
the folder of `site:` when no home is set. `BaseConfiguration` supplies the
package defaults: its `main` writes the site, server and storage sections,
`server_section` writes a bare `server`, and `storage_key` is the key material
hook of the storage section.

`DefaultConfig.parents_for` layers package defaults, optional machine defaults,
then the site recipe. `default_config=False` opts out of machine defaults; an
explicit missing path raises `ConfigError`. The machine home resolves from the
explicit argument, `KAJENN_HOME`, then `~/.kajenn`.

`ConfigurationHandler` is a read interface. Its inherited precedence is written
value, grammar signature default, call-site default, then `KeyError`. Closed
signatures are read attribute by attribute; open kwargs use resolved runtime
values. `BaseApplication.config` reads relative to `applications.<code>`.
`AsgiServer` consumes the resulting kwargs and builds applications itself.

`AsgiServer` without a configuration source writes its constructor kwargs into
a `ShortcutConfiguration` layered over the `default` template
(`DefaultConfiguration`). `server.config` is a `ConfigurationHandler` on both
roads.

Claim anchors: [`AsgiConfigBuilder`](../../../src/kajenn/config/builder.py#L61), [`AsgiServerGrammar`](../../../src/kajenn/config/elements.py#L83), [`AsgiServer`](../../../src/kajenn/asgi_server.py#L109), [`site_section`](../../../src/kajenn/config/builder.py#L106), [`storage_mounts`](../../../src/kajenn/config/builder.py#L117), [`BaseConfiguration`](../../../src/kajenn/config/builder.py#L140), [`storage_key`](../../../src/kajenn/config/builder.py#L163), [`server_section`](../../../src/kajenn/config/builder.py#L173), [`DefaultConfig`](../../../src/kajenn/config/default_config.py#L64), [`parents_for`](../../../src/kajenn/config/default_config.py#L75), [`ConfigError`](../../../src/kajenn/config/handler.py#L76), [`ConfigurationHandler`](../../../src/kajenn/config/handler.py#L80), [`ShortcutConfiguration`](../../../src/kajenn/config/templates.py#L84), [`_build_config`](../../../src/kajenn/asgi_server.py#L151).

Behavior evidence: `tests/core/test_site_home.py::test_a_homeless_server_has_no_site_section_at_all`, `test_the_home_anchors_the_home_volume_and_site_stays_where_it_was`, `tests/core/test_config.py::test_a_server_composed_in_code_has_one_too`.

## Server and application vocabulary

The grammar declares ten sections: `site`, `server`, `middleware`,
`authentication`, `channels`, `storage`, `applications`, `databases`,
`plugins` and `openapi`. The OpenAPI root section has no current core
consumer; open issue kajenn-org/kajenn#6 "Wire or drop the openapi
configuration section" covers it. `server` includes `shutdown_timeout_seconds`
and `debug`; its children are `session`, `tasks`, `websocket` and `kbus`.
`websocket.origins` is a comma-separated recipe value converted to a list;
`max_concurrent` defaults to 16. `debug` reaches the server as `server.debug`.

- **`site`** carries `name` and `home`. `site_kwargs` turns them into the
  `site_name` and `site_home` kwargs; `AsgiServer` wraps `site_home` in a
  `SiteHome`.
- **`channels`** is a collection of `channel(name, authentication_route)`.
  `channels()` refuses a channel without `authentication_route` with
  `ValueError` when the server is built.
- **`server.kbus`** carries `address`, `certfile`, `keyfile`, `cafile` and six
  limits: `max_frame`, `max_meta`, `max_route`, `max_pending`, `stream_window`
  and `write_buffer`. The limits build a `kbus.Limits`. An address that is not
  `unix://` or `wss://`, and a `wss://` address without `certfile` and
  `keyfile`, raise `ValueError`.
- **`applications`.** An application entry declared with `spawner` becomes a
  `RemoteApplication`. In an application's dedicated process (the `role`
  kwarg) only that application is built, without the `PROXY_OPTIONS`.
- **`_server` entry.** `application(code="_server")` without `app_class` gets
  `ServerApplication`. An `app_class` that is not a `ServerApplication`
  subclass raises `ValueError`.
- **`databases`.** `databases()` returns one descriptor per database:
  `name`, `owner`, `handler_class` and `params`. The server's databases have
  owner `None`; an application's databases are named `<owner>:<code>`.
- **Orchestration helpers.** `orchestration_kwargs`, `commander_kwargs` and
  `group_kwargs` read `applications.<code>.orchestration`. No caller exists in
  `src/` or `tests/`. Open issue kajenn-org/kajenn#5 "Decide whether the
  orchestration, commander and group kwargs seam stays in kajenn" covers them.

Storage mounts use the storage application's foreign grammar. Each application
mounts its own `app_class.grammar` under `applications.<code>`, so whatever an
application adds to the vocabulary is read from its own subtree and never from
a server-level section. That is what lets an application arrive with words the
core has never heard of.
The `authentication` section declares the `users`/`tokens` store descriptors
(their `store_class` is the class the server builds), `cache_ttl` and the
`credentials` children; it carries no bootstrap password, because the server
creates no user.

Claim anchors: [`configuration`](../../../src/kajenn/config/elements.py#L93), [`site`](../../../src/kajenn/config/elements.py#L109), [`server`](../../../src/kajenn/config/elements.py#L134), [`websocket`](../../../src/kajenn/config/elements.py#L175), [`kbus`](../../../src/kajenn/config/elements.py#L194), [`authentication`](../../../src/kajenn/config/elements.py#L275), [`channels`](../../../src/kajenn/config/elements.py#L393), [`channel`](../../../src/kajenn/config/elements.py#L399), [`site_kwargs`](../../../src/kajenn/config/handler.py#L83), [`server_kwargs`](../../../src/kajenn/config/handler.py#L92), [`identity_kwargs`](../../../src/kajenn/config/handler.py#L166), [`channels()`](../../../src/kajenn/config/handler.py#L186), [`applications()`](../../../src/kajenn/config/handler.py#L289), [`databases()`](../../../src/kajenn/config/handler.py#L316), [`orchestration_kwargs`](../../../src/kajenn/config/handler.py#L352), [`_server` default class](../../../src/kajenn/config/builder.py#L75), [`RemoteApplication` built](../../../src/kajenn/asgi_server.py#L222).

Behavior evidence: `tests/core/test_config.py::test_a_second_channels_element_is_rejected_by_the_grammar`, `tests/core/test_server_app_always_present.py::test_the_recipe_customises_without_app_class`, `test_a_class_that_is_not_a_server_application_is_refused`, `tests/core/test_external_application_streaming.py::test_an_answer_larger_than_one_frame_streams_through`.

## The tree is read-only at runtime

The general writable configuration tree remains unimplemented: there is no
`apply_configuration` or handler mutator, and no server subscriber for dynamic
application installation. `ConfigurationHandler` exposes reading only.

An application that applies settings of its own while running does so over its
own subtree, with its own validation and its own lock. Nothing in the core
commits such a change, and a bounded operation of that kind is not evidence
that the general live-tree design has landed.

Claim anchors: [`ConfigurationHandler`](../../../src/kajenn/config/handler.py#L80).

## Remaining design distance

- **Bootstrap administrator password.** design.md §1 describes a bootstrap
  administrator password declared in a way that rejects a literal. No such
  word exists: the `authentication` element and `identity_kwargs` state that
  there is no bootstrap password ([elements.py](../../../src/kajenn/config/elements.py#L275), [handler.py](../../../src/kajenn/config/handler.py#L166)).
- **Survivable application failure.** design.md §5 describes an application
  that declares a failure of its own survivable, so that the server starts
  without it. `BaseApplication` has no such declaration
  ([application.py](../../../src/kajenn/application.py#L163)).
  `AsgiServer` instantiates every configured application in `__init__`, and an
  exception there stops the construction
  ([asgi_server.py](../../../src/kajenn/asgi_server.py#L187)).

## Source and test evidence

- [src/kajenn/config/builder.py](../../../src/kajenn/config/builder.py)
- [src/kajenn/config/default_config.py](../../../src/kajenn/config/default_config.py)
- [src/kajenn/config/elements.py](../../../src/kajenn/config/elements.py)
- [src/kajenn/config/handler.py](../../../src/kajenn/config/handler.py)
- [src/kajenn/config/templates.py](../../../src/kajenn/config/templates.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [tests/core/test_config.py](../../../tests/core/test_config.py)
- [tests/core/test_config_env.py](../../../tests/core/test_config_env.py)
- [tests/core/test_site_home.py](../../../tests/core/test_site_home.py)
- [tests/core/test_server_app_always_present.py](../../../tests/core/test_server_app_always_present.py)
