# Database — current state

**Version**: 0.3 · **Last Updated**: 2026-10-27 · **Status**: 🟡 rewritten on the shipped contract; design ratification pending

Verified against source revision `d1be0fc` (develop, migrations merged). Test
references below identify the executable contracts; they are not a new
coverage percentage.

## Declaration and registry

The `databases` section names the default `handler_class`; each `database`
may override it, and every other keyword is a param. `ConfigurationHandler.databases`
returns one descriptor per database, the server's first, each application's
under `<owner>:<code>`, with the handler class resolved (own, section default,
server default). `AsgiServer._register_configured_databases` skips the
databases of applications this process does not serve and hands the rest to
`add_database`.

`BaseServer.add_database(name, params, handler_class)` falls back to
`db_handler_class` (`None` on `BaseServer`, `databases.handler_class` on
`AsgiServer`), builds one handler per class with `handler_class(server)`,
calls its `add_database` and records the name. A claimed name and a database
with no handler class raise `ValueError`.

Claim anchors: [`DatabaseGrammar`](../../../src/kajenn/application.py#L89), [`databases`](../../../src/kajenn/config/handler.py#L316), [`_register_configured_databases`](../../../src/kajenn/asgi_server.py#L230), [`db_handler_class`](../../../src/kajenn/asgi_server.py#L249), [`add_database`](../../../src/kajenn/server.py#L291), [`DbHandlerBase`](../../../src/kajenn/db.py#L51).

## Call boundary

`BaseApplication.database_name` resolves `None` to `db_name`, else
`<code>:default` when declared, else `default`. `db(name)` and
`Request.db(name)` call `server.take_database`, which asks the handler's
`take(name, call)` once per name per call and keeps the answer on
`call.databases`; an unknown name raises `LookupError`, a take outside a
registered call `RuntimeError`. The first take queues an async cleanup that
runs `release_databases` on the pool when something is still taken. After a
sync route, `RoutedApplication.route_cleanup` releases on the route's own
thread. `release_databases` calls each handler's `release(call)` once, logs a
raise and clears the takes.

`async_db(name)` returns the taken object through the handler's `async_view`:
by default an `AsyncDbView`, whose `terminals` run with `server.run_sync`.

Claim anchors: [`database_name`](../../../src/kajenn/application.py#L220), [`db`](../../../src/kajenn/application.py#L234), [`async_db`](../../../src/kajenn/application.py#L242), [`Request.db`](../../../src/kajenn/request.py#L432), [`take_database`](../../../src/kajenn/server.py#L330), [`release_databases`](../../../src/kajenn/server.py#L358), [`route_cleanup`](../../../src/kajenn/routed_application.py#L370), [`async_view`](../../../src/kajenn/db.py#L88), [`AsyncDbView`](../../../src/kajenn/db.py#L93).

## Lifespan

`Lifespan.startup` opens every handler before the first `on_startup`;
a failing `open` is a `FatalBootError` and the handlers already opened are
closed. `shutdown` closes them after the last `on_shutdown`; a failing `close`
is logged and the next one runs.

Claim anchors: [`startup`](../../../src/kajenn/lifespan.py#L122), [`shutdown`](../../../src/kajenn/lifespan.py#L145), [`_close_handlers`](../../../src/kajenn/lifespan.py#L169).

## Migration

`capabilities` lists the optional operations; `BaseServer.database_handler`
returns the handler of a name, `LookupError` for an unknown name and
`ValueError` for a missing capability. The `_server/databases` section
(`SERVER_ADMIN`) answers `list`, `plan` (GET) and `apply` (POST) with
`{"name", "result"}`, mapping the refusals to 404 and 422.
`kajenn db plan|apply SOURCE NAME` builds the server without starting it,
checks the name, opens every handler, runs the operation, closes the handlers
and prints plain JSON. The server never migrates on its own.

Claim anchors: [`capabilities`](../../../src/kajenn/db.py#L66), [`database_handler`](../../../src/kajenn/server.py#L313), [`DatabasesSection`](../../../src/kajenn/server_app/server_sections/databases_section.py#L58), [`DatabaseCommand`](../../../src/kajenn/__main__.py#L537), [`operate`](../../../src/kajenn/__main__.py#L552).

## Source and test evidence

- [src/kajenn/db.py](../../../src/kajenn/db.py)
- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn/application.py](../../../src/kajenn/application.py)
- [src/kajenn/request.py](../../../src/kajenn/request.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/lifespan.py](../../../src/kajenn/lifespan.py)
- [src/kajenn/server_app/server_sections/databases_section.py](../../../src/kajenn/server_app/server_sections/databases_section.py)
- [src/kajenn/__main__.py](../../../src/kajenn/__main__.py)
- [tests/core/test_db.py](../../../tests/core/test_db.py)
- [tests/core/test_request.py](../../../tests/core/test_request.py)
- [tests/core/test_config.py](../../../tests/core/test_config.py)
- [tests/core/test_lifespan.py](../../../tests/core/test_lifespan.py)
- [tests/core/test_cli.py](../../../tests/core/test_cli.py)
- [tests/server_app/test_databases_section.py](../../../tests/server_app/test_databases_section.py)
