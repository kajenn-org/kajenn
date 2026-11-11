# Database — current state

**Version**: 0.4 · **Last Updated**: 2026-11-03 · **Status**: 🟡 rewritten on the shipped contract; design ratification pending

Verified against source revision `63fb326` (develop). Test
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

Claim anchors: [`DatabaseGrammar`](../../../src/kajenn/application.py#DatabaseGrammar), [`databases`](../../../src/kajenn/config/handler.py#ConfigurationHandler.databases), [`_register_configured_databases`](../../../src/kajenn/asgi_server.py#AsgiServer._register_configured_databases), [`db_handler_class`](../../../src/kajenn/asgi_server.py#AsgiServer.db_handler_class), [`add_database`](../../../src/kajenn/server.py#BaseServer.add_database), [`DbHandlerBase`](../../../src/kajenn/db.py#DbHandlerBase).

## Call boundary

`BaseApplication.database_name` resolves `None` to `db_name`, else
`<code>:default` when declared, else `default`. `db_name` is a class attribute
(`None` on `BaseApplication`, application.py:180) and a constructor keyword
that overrides it (application.py:210). `db(name)` and
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

`Request.db(name)` and `Request.async_db(name)` delegate to the owning
application through `bound_application()` (request.py:432-442). A `Request`
bound to no application raises `RuntimeError` there (request.py:444-449). Test:
`test_a_request_bound_to_no_application_has_no_databases`.

Claim anchors: [`database_name`](../../../src/kajenn/application.py#BaseApplication.database_name), [`db_name`](../../../src/kajenn/application.py#BaseApplication.db_name), [`db`](../../../src/kajenn/application.py#BaseApplication.db), [`async_db`](../../../src/kajenn/application.py#BaseApplication.async_db), [`Request.db`](../../../src/kajenn/request.py#Request.db), [`Request.async_db`](../../../src/kajenn/request.py#Request.async_db), [`bound_application`](../../../src/kajenn/request.py#Request.bound_application), [`take_database`](../../../src/kajenn/server.py#BaseServer.take_database), [`release_databases`](../../../src/kajenn/server.py#BaseServer.release_databases), [`route_cleanup`](../../../src/kajenn/routed_application.py#RoutedApplication.route_cleanup), [`async_view`](../../../src/kajenn/db.py#DbHandlerBase.async_view), [`AsyncDbView`](../../../src/kajenn/db.py#AsyncDbView).

## Threads of take and release

- `take_database` is synchronous and runs on the thread of its caller
  (server.py:340-366).
- An `async def` route calls `async_db`, which calls `take_database` on the
  event-loop thread (application.py:297-301).
- A sync route runs on a pool thread, and `route_cleanup` releases its takes on
  that same thread after the handler returns or raises
  (routed_application.py:366-371, 374-387).
- The cleanup queued by the first take calls `release_databases` through
  `server.run_sync`, on whichever pool thread the pool assigns
  (server.py:359-364). It runs only when something is still taken.
- No code ties the thread of a call's `release` to the thread of its `take`
  outside a sync route.

kajenn-org/kajenn#90 records the backends this leaves unserved.

Claim anchors: [`take_database`](../../../src/kajenn/server.py#BaseServer.take_database), [`route_cleanup`](../../../src/kajenn/routed_application.py#RoutedApplication.route_cleanup), [`run_sync`](../../../src/kajenn/server.py#BaseServer.run_sync).

## Distance from the design

**The native-async branch.** The `db.py` module docstring says a natively async
handler returns its own object from `async_view` (db.py:33-35). `async_db`
passes `async_view` the object `take` returned for the call
(application.py:297-301). An object that needs its own engine, separate from
the one behind the taken object, cannot be that view: kajenn-org/kajenn#91.

**A distinct object per call.** design.md states one `take` per name per call,
kept on `call.databases`. `take_database` keys the takes by call
(server.py:349-366); nothing in the core checks or states whether two calls
receive distinct objects: kajenn-org/kajenn#92.

## Lifespan

`Lifespan.startup` opens every handler before the first `on_startup`;
a failing `open` is a `FatalBootError` and the handlers already opened are
closed. `shutdown` closes them after the last `on_shutdown`; a failing `close`
is logged and the next one runs.

Claim anchors: [`startup`](../../../src/kajenn/lifespan.py#Lifespan.startup), [`shutdown`](../../../src/kajenn/lifespan.py#Lifespan.shutdown), [`_close_handlers`](../../../src/kajenn/lifespan.py#Lifespan._close_handlers).

## Migration

`capabilities` lists the optional operations; `BaseServer.database_handler`
returns the handler of a name, `LookupError` for an unknown name and
`ValueError` for a missing capability. The `_server/databases` section
(`SERVER_ADMIN`) answers `list`, `plan` (GET) and `apply` (POST) with
`{"name", "result"}`, mapping the refusals to 404 and 422.
`kajenn db plan|apply SOURCE NAME` builds the server without starting it,
checks the name, opens every handler, runs the operation, closes the handlers
and prints plain JSON. The server never migrates on its own.

Claim anchors: [`capabilities`](../../../src/kajenn/db.py#DbHandlerBase.capabilities), [`database_handler`](../../../src/kajenn/server.py#BaseServer.database_handler), [`DatabasesSection`](../../../src/kajenn/server_app/server_sections/databases_section.py#DatabasesSection), [`DatabaseCommand`](../../../src/kajenn/__main__.py#DatabaseCommand), [`operate`](../../../src/kajenn/__main__.py#DatabaseCommand.operate).

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
