# Databases

Prerequisites: [configuration](../configuration.md) and
[request injection](requests.md#access-the-request-and-session).
kajenn registers a database object and wraps it in a handler. The database driver
and its query API belong to your application, not to kajenn.

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

- The `databases` section names the `handler_class`, a `DbHandlerBase`
  subclass; a `database` may override it. One handler instance serves every
  database of its class and receives each one with `add_database(name, params)`:
  the handler builds the database from the params.
- Names follow the owner: `main` is a server database, `catalog:main` the
  database `main` that the application `catalog` declares in its own
  `databases` section.
- `request.db()` resolves `db_name` when set, else the application's own
  `default`, else the server's `default`; `request.db(name)` picks one by name.
  An unknown name raises `LookupError`.
- The core asks `take(name, call)` once per name per call and calls
  `release(call)` at the end of every call that took, on a pool thread.
- An `async def` route uses `request.async_db()`: the methods the handler lists
  in `terminals` become awaitable and run on the pool.

The handler owns connections and transactions: the core adds no connection pool
and no automatic commit. See the [handler API](../api/config.rst).

```{admonition} In revisione
:class: warning

The selection and cleanup contract is tested with the probe above. A production
SQL-driver example, including transactions, thread affinity and connection-pool
behaviour, remains to be verified with the chosen integration.
```
