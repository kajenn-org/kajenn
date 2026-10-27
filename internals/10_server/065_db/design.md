# Database

**Version**: 0.2 · **Last Updated**: 2026-10-27 · **Status**: 🟡 rewritten on the shipped contract; ratification pending

**The need.** An installation mounts its databases through the recipe, without the core knowing any concrete backend.

`db.py` holds the core's contract, `DbHandlerBase`:

- **Declaration.** `databases(handler_class=...)` sets the default; each
  `database(code=..., handler_class=None, **params)` may override it. The
  server's `db_handler_class` is that default (`None` on `BaseServer`, the
  configured one on `AsgiServer`). An application section without a default
  takes the server section's.
- **Registry (owner:db).** `foo` is a server database, `chat:foo` the database
  `foo` of the application `chat`. The server keeps `databases` (name →
  handler) and `db_handlers` (class → the one instance), built with
  `handler_class(server)` and fed with `add_database(name, params)`.
- **Call boundary.** `take(name, call)` once per name per call, the answer kept
  on `call.databases`; `release(call)` at the end of the call, on a pool
  thread. Applications reach it with `db(name)` and `async_db(name)`; the
  latter passes the taken object through `async_view`, by default an
  `AsyncDbView` whose `terminals` are awaitable and run on the pool.
- **Lifespan.** `open()` before the applications' `on_startup`, `close()` after
  their `on_shutdown`.
- **Optional operations.** `capabilities` names them (`migration_plan`,
  `migrate`); `BaseServer.database_handler(name, capability)` refuses a
  handler that does not offer one. The `_server/databases` section and
  `kajenn db plan|apply SOURCE NAME` are the only callers: the server never
  migrates on its own.

Concrete handlers (asqueel, SQLAlchemy, Django) live OUTSIDE the core, in
kajenn-db.

Interactions: configuration (the `databases` words) · the hosted applications that take databases per call · the lifespan · the `_server` application · the CLI.
