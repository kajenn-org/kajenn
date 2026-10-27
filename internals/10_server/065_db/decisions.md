# Database — decisions

**Version**: 0.2 · **Last Updated**: 2026-10-27 · **Status**: 🟡 approved 2026-10-26; ratification of this entry pending

Everything this entry SHOULD be — the target, approved with the handler plan
on 2026-10-26: the handler contract shipped in kajenn 0.6.0, the migration
operations are merged in develop (`d1be0fc`).

1. **The handler is the backend's.** A database names a `handler_class`, a
   `DbHandlerBase` subclass; the section's `handler_class` is the default, and
   the server exposes it as `db_handler_class`. The core never imports a
   driver.
2. **One handler per class.** The first database of a class builds
   `handler_class(server)`; every database of that class goes to the same
   instance through `add_database(name, params)`.
3. **Names follow the owner.** `foo` is a server database, `chat:foo` the
   database `foo` of the application `chat`. A process mounts the server's
   databases and those of the applications it serves.
4. **One take per name per call, one release per call.** `take(name, call)` is
   sync and does no I/O; `release(call)` does the I/O and always runs on a pool
   thread; after a sync route, the thread the route ran on.
5. **Async routes get a view, not a second API.** `async_db()` is the taken
   object through `async_view`; the handler lists its `terminals`.
6. **The core adds no connection pool and no automatic commit.** Connections
   and transactions are the handler's.
7. **Migration is manual.** Optional operations are declared in
   `capabilities`; `migration_plan`/`migrate` run only from `_server/databases`
   or `kajenn db plan|apply`, never at boot.
8. **Backends ship apart.** The asqueel, SQLAlchemy and Django handlers live
   in the kajenn-db package, documented at https://kajenn-db.readthedocs.io.
