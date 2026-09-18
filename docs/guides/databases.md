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
from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder


class ExampleDatabase:
    def __init__(self, label: str):
        self.label = label
        self.closed = 0

    def closeConnection(self):
        self.closed += 1


class DatabaseApp(RoutedApplication):
    mount = ""
    db_name = "main"

    @route()
    def database(self, _request=None) -> dict:
        db = _request.db
        return {"label": db.label, "previous_cleanups": db.closed}

    @route()
    def lookup(self, _request=None) -> dict:
        db = _request.get_db("main")
        return {"label": db.label, "previous_cleanups": db.closed}


class Configuration(AsgiConfigBuilder):
    default_config = False

    def main(self, root):
        cfg = root.configuration()
        cfg.databases().database(code="main", db_class=ExampleDatabase, label="catalog")
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

The first two calls report cleanup counts 0 and 1. The last two both report 2:
`request.db` registers cleanup; `request.get_db(name)` is lookup only.
Stop with Ctrl-C.

## The contract for a real driver

- The recipe constructs `db_class(**params)` and wraps it with AsgiDbHandlerBase,
  or your declared `db_handler_class`.
- `request.db` selects the application's `db_name`, or `default` when omitted.
  The first successful lookup registers `closeConnection` for request cleanup.
- `request.get_db(name)` selects explicitly without registering cleanup.
- An unknown registration returns None. Declaring `code="main"` does not make it
  the automatic default; use `db_name="main"` as above.
- The handler proxies public attributes to your database. Its default cleanup
  calls the database's `closeConnection` method when present.

Choose a driver/handler that matches your concurrency and connection ownership.
The registry holds a shared object; the core adds no transaction management,
connection pool or per-request database instance. Cleanup is not an automatic
commit. See the [handler API](../api/config.rst).

```{admonition} In revisione
:class: warning

The selection and cleanup contract is tested with the probe above. A production
SQL-driver example, including transactions, thread affinity and connection-pool
behaviour, remains to be verified with the chosen integration.
```
