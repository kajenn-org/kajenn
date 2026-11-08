# Database — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🟡 approved 2026-10-26; ratification of this entry pending

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

## Still open

**S1 [call-threads] — kajenn-org/kajenn#90, "The thread of take and release is
not fixed within a call".** Decision 4 fixes the thread of `release` only after
a sync route. For an `async def` route `take` runs on the event-loop thread and
`release` on a pool thread (application.py:297-301, server.py:359-364). The
issue adds tasks, kbus calls and WSX messages without `id`, where `take` and
`release` may run on two pool threads. A backend whose connection is bound to
the thread that opened it cannot be served in these calls. The issue records no
decision: the core fixes the thread, or the contract states the limit.

**S2 [native-async] — kajenn-org/kajenn#91, "The native-async branch of the db
contract does not fit an object with its own engine".** Decision 5 makes
`async_db()` the taken object through `async_view`. A natively async object
that needs its own engine, such as SQLAlchemy's `AsyncSession`, cannot be the
view of the taken `Session`: one call would hold two objects and two
transactions. The issue records no decision.

**S3 [distinct-take] — kajenn-org/kajenn#92, "The db contract does not state
whether a taken object is distinct per call".** Decision 4 promises one take
per name per call and one release per call. It does not say whether two calls
may receive the same object. Django's `connections[alias]` hands the same
object to two calls served on one pool thread, released in between. The issue
records no decision.

**S4 [database-grammar] — kajenn-org/kajenn#59, "Asqueel databases in server
and application configuration", item 1.** The issue asks that
`databases.database` mount the grammar of its class as a subbuilder, as
`storage` does. The `database` element declares no subbuilder and no children
(`sub_tags=""`), and takes its params as keywords (application.py:117-123).

**S5 [store-db] — kajenn-org/kajenn#59, item 5.** The issue asks that a
`users`/`tokens` `store_class` receive the name of a registered database
(`db="betadb"`) and that kajenn pass it that database. The store is built with
the server's storage and the remaining descriptor options as keywords
(auth/mixin.py:200-201); no code resolves a `db` option to a registered
database.
