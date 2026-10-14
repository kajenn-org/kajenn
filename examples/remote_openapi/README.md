# Remote OpenAPI example

`RemoteDemoApplication` (`app.py`) is declared with `spawner="subprocess"` in
`frontend.py`. The server starts a `kbus.Dispatcher` at lifespan startup and spawns
`python -m kajenn serve <absolute path of frontend.py> --role application:demo`;
the dispatcher URL travels in the environment variable `KAJENN_KBUS_PARENT`.
That process builds the same configuration, hosts only `demo`, joins the
dispatcher as a `kbus.Member` after the application's `on_startup`, and calls
`server.joined`. Until it registers,
`/demo/...` answers 503 while `/health` answers from the server.

```bash
PYTHONPATH=$PWD .venv/bin/kajenn serve examples/remote_openapi/frontend.py --port 8000
curl http://127.0.0.1:8000/demo/hello
```
