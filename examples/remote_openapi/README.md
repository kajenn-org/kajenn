# Remote OpenAPI example

`RemoteDemoApplication` (`app.py`) is declared with `spawner="subprocess"` in
`frontend.py`. The server starts a hub at lifespan startup and spawns
`python -m kajenn serve examples/remote_openapi/frontend.py --role application:demo --parent <hub>`.
That process builds the same configuration, hosts only `demo`, and registers
with the hub after the application's `on_startup`. Until it registers,
`/demo/...` answers 503 while `/health` answers from the server.

```bash
PYTHONPATH=$PWD .venv/bin/kajenn serve examples/remote_openapi/frontend.py --port 8000
curl http://127.0.0.1:8000/demo/hello
```
