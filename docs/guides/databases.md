# Databases

Prerequisites: [configuration](../configuration.md) and
[request injection](requests.md#access-the-request-and-session).
kajenn mounts databases through handlers. A handler is a `DbHandlerBase`
subclass that builds each database from its configuration and gives every call
the object it works on. The core never imports a database driver: the
handlers for asqueel, SQLAlchemy and Django live in the
[kajenn-db](https://kajenn-db.readthedocs.io) package.

## Observe selection and cleanup

The complete example below is an **in-memory contract probe**, not a database
driver. It makes selection and end-of-request cleanup visible without requiring
an external service. Save as `database_demo.py` and run it in a writable directory.

```python
from genro_routes import route
from kajenn import AsgiServer, DbHandlerBase, RoutedApplication
from kajenn.config import AsgiConfigBuilder


class ExampleDatabase:
    def __init__(self, label: str):
        self.label = label
        self.releases = 0

    def describe(self) -> dict:
        return {"label": self.label, "previous_releases": self.releases}


class ExampleHandler(DbHandlerBase):
    terminals = ("describe",)

    def __init__(self, server):
        super().__init__(server)
        self.databases = {}

    def add_database(self, name, params):
        self.databases[name] = ExampleDatabase(**params)

    def take(self, name, call):
        return self.databases[name]

    def release(self, call):
        for name in call.databases:
            self.databases[name].releases += 1


class DatabaseApp(RoutedApplication):
    mount = ""
    db_name = "main"

    @route()
    def database(self, _request=None) -> dict:
        return _request.db().describe()

    @route()
    async def lookup(self, _request=None) -> dict:
        return await _request.async_db().describe()


class Configuration(AsgiConfigBuilder):
    default_config = False

    def main(self, root):
        cfg = root.configuration()
        cfg.databases(handler_class=ExampleHandler).database(code="main", label="catalog")
        cfg.applications().application(code="catalog", mount="", app_class=DatabaseApp)


if __name__ == "__main__":
    AsgiServer(config=Configuration).serve(host="127.0.0.1", port=8000)
```

```bash
python database_demo.py
# In another terminal, call these one at a time:
curl http://127.0.0.1:8000/database
curl http://127.0.0.1:8000/database
curl http://127.0.0.1:8000/lookup
curl http://127.0.0.1:8000/lookup
```

The four calls report 0, 1, 2 and 3 previous releases: every call takes `main`
once and the handler releases it when the call ends. The sync route uses
`request.db()`, the async one `request.async_db()`. Stop with Ctrl-C.

## The contract for a real driver

- The `databases` section names the default `handler_class`, a `DbHandlerBase`
  subclass; a `database` may override it. Every other keyword of `database` is
  a param: the core hands them to the handler, it does not read them.
- One handler instance serves every database of its class. It is built as
  `handler_class(server)` and receives each database with
  `add_database(name, params)`: the handler builds the database from the
  params.
- Names follow the owner: `main` is a server database, `catalog:main` the
  database `main` that the application `catalog` declares in its own
  `databases` section. An application section without `handler_class` takes
  the server section's.
- `request.db()` resolves `db_name` when set, else the application's own
  `default`, else the server's `default`; `request.db(name)` picks one by name.
  An unknown name raises `LookupError`.
- The core asks `take(name, call)` once per name per call and keeps the answer
  on `call.databases`. It calls `release(call)` at the end of every call that
  took, on a pool thread: after a sync route, on the thread the route ran on.
  A call cancelled while its work runs on the pool releases only after that
  work ends; the cancellation propagates then. Work still queued never runs.
- An `async def` route uses `request.async_db()`: the object `take` returned,
  passed through the handler's `async_view`. By default the methods the
  handler lists in `terminals` become awaitable and run on the pool.
- `open()` runs at lifespan startup, before the applications' `on_startup`;
  `close()` runs at shutdown, after their `on_shutdown`. A failing `open` stops
  the boot.

The handler owns connections and transactions: the core adds no connection pool
and no automatic commit. See the [handler API](../api/config.rst).

## Use a kajenn-db handler

Install the package with the extra of your backend, for example
`pip install "kajenn-db[asqueel]"`, and name its handler in the recipe:

```python
from kajenn_db.asqueel import AsqueelDbHandler

cfg.databases(handler_class=AsqueelDbHandler).database(
    code="main", source=EnvResolver("SHOP_DB_SOURCE"),
)
```

The params (`source` here) are the handler's own. What each handler accepts,
how its async view works and which methods it refuses on an `async def` route
are documented with the package: see
[kajenn-db](https://kajenn-db.readthedocs.io).

## Write a handler

A handler for another backend subclasses `DbHandlerBase`:

- `add_database(name, params)` — sync, no I/O: build the database from the
  params and keep it by name.
- `take(name, call)` — sync, no I/O: the object the route works on during
  `call`.
- `release(call)` — sync, may do I/O: close what `call` took from this handler.
  `call.databases` maps each name taken to the object `take` returned; it can
  also hold names of other handlers.
- `open()` / `close()` — async, optional: start and stop the backend with the
  lifespan.
- `terminals` — the method names `async_view` makes awaitable; a natively async
  backend overrides `async_view` and returns its own object.
- `capabilities` — the optional operations the handler offers. A handler that
  supports migration returns `{"migration_plan", "migrate"}` and implements
  `migration_plan(name)` and `migrate(name)`. Both are sync and open a
  connection of their own.

## Migrate a database

The server never migrates a database on its own. An operator launches it, from
the `_server` application or from the command line.

The `_server/databases` routes require the `SERVER_ADMIN` rule:

| Route | Method | Answer |
|---|---|---|
| `/_server/databases/list` | GET | every database: `name`, `owner`, `handler_class`, `capabilities` |
| `/_server/databases/plan?name=NAME` | GET | `{"name": NAME, "result": ...}`: the handler's `migration_plan(NAME)`; changes nothing |
| `/_server/databases/apply?name=NAME` | POST | `{"name": NAME, "result": ...}`: the handler's `migrate(NAME)` |

An unknown name answers 404. A database whose handler does not offer the
operation answers 422.

The command line does the same on a server it builds and never starts:

```bash
kajenn db plan ./config.py main
kajenn db apply ./config.py catalog:main
```

`SOURCE` takes the same forms as `kajenn serve`. The command checks the name
and the capability, opens every handler, runs the operation, closes the
handlers and prints `{"name": NAME, "result": ...}` as plain JSON. An unknown
name or a missing capability is an error, never an empty result.
