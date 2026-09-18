# Lifecycle and shutdown

Application `on_startup` hooks run in registration order; `on_shutdown` hooks
run in reverse. Either hook may be synchronous or asynchronous. Synchronous
hooks run directly on the event-loop thread, so keep them short or explicitly
offload blocking work. Ordinary hook exceptions are logged and the sequence
continues. Raise `kajenn.lifespan.FatalBootError` from startup when a missing
prerequisite must prevent the server from starting.

```python
from kajenn import RoutedApplication
from kajenn.lifespan import FatalBootError


class Catalog(RoutedApplication):
    async def on_startup(self):
        if self.config("parameters.catalog_url", default=None) is None:
            raise FatalBootError("Configure parameters.catalog_url")

    async def on_shutdown(self):
        pass  # close application-owned resources here
```

This is a hook fragment, not a running catalog service. Supply its configuration
and routes before serving it.

## Admission and draining

The server begins with state `RUNNING`. The first SIGINT or SIGTERM changes it
to `shutdown_mode` (normally `STOPPING`; the reload child uses `QUITTING`)
before uvicorn starts its own shutdown, so the whole graceful window is served
by a server that already refuses new work: a request arriving on an already
open connection during that window reads 503 with `Retry-After`. Lifespan
shutdown makes the same change for whoever did not arrive by signal, waits for
in-flight registered requests, then runs the application hooks.

The signal handlers belong to `UvicornServer`, which `serve()` builds. Under
uvicorn's own `--reload` the child process does not build one, so there the
state turn is not guaranteed — the reload mode is for stateless single-process
development.
The registry drain has its own finite timeout. Requests reaching core HTTP
dispatch while not running receive 503 with `Retry-After` and are not registered.
Middleware that answers on its own can still answer before that gate.

New WebSocket handshakes are refused before accept whenever the server is not
running, including raw WebSockets. A browser sees a failed handshake rather than
a readable post-accept close code. See [WebSockets](websockets.md).

`BaseServer.leaving` is that turn as an awaitable — an `asyncio.Event` set when
the state leaves `RUNNING` — and `await server.get_until_leaving(queue)` reads
the next item of a queue, or answers `None` the moment the server starts
leaving. An endless response ends itself with it instead of waiting to be
cancelled; the MCP push stream is written that way.

`shutdown_timeout_seconds` defaults to **5.0** and controls uvicorn's wait for
open connections before cancelling them, allowing lifespan shutdown to run even
when an HTTP stream never ends:

```python
from kajenn.config import AsgiConfigBuilder


class Configuration(AsgiConfigBuilder):
    def main(self, root):
        root.configuration().server(shutdown_timeout_seconds=5.0)
```

The same value can be passed to `AsgiServer(shutdown_timeout_seconds=5.0)`.
This is a connection-drain timeout, not a five-second deadline for the entire
process: application hooks and worker shutdown have their own work to finish.
Stop a foreground server with Ctrl-C; the CLI's `stop` sends SIGTERM to the
named server (or reload supervisor). See [CLI](cli.md).
