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

## Health probes

Every server answers three probe paths itself, for an orchestrator such as
Kubernetes, a load balancer or a container runtime:

| Path | Kind | Answer |
| --- | --- | --- |
| `/healthz` | liveness | 200 `ok` while the process can answer at all, leaving included |
| `/livez` | liveness | same as `/healthz` |
| `/readyz` | readiness | 200 `ready` while the state is `RUNNING` and every application is `ready`; otherwise 503 `not ready`, then one reason per line |

The server answers them in its own entry point, before the middleware chain
and before the 503 refusal of a server that is leaving. A probe opens no
session, sets no cookie, needs no credential, and is not a registered request,
so it never delays the drain. GET and HEAD are answered; any other method gets
405. Answers carry `cache-control: no-store`. Only the exact path is a probe:
`/healthz/x` reaches the applications as usual.

```console
$ curl -s http://127.0.0.1:8000/healthz
ok
$ curl -s -w ' %{http_code}\n' http://127.0.0.1:8000/readyz
ready 200
```

During shutdown, `/healthz` stays 200 and `/readyz` turns 503 with
`server stopping` (or `server quitting`), so the orchestrator stops sending
traffic without restarting the process. An application spawned in its own
process (`spawner="subprocess"`) is not ready until its process joins, and
again while it is relaunched: `/readyz` names it as `application <code>`.

### Readiness of an application

`BaseApplication.ready` is `True`. An application that depends on something
it can lose overrides it. The property is read on every probe, so it must
answer at once, with no I/O:

```python
from kajenn import RoutedApplication


class Feed(RoutedApplication):
    connected = False

    @property
    def ready(self) -> bool:
        return self.connected
```

`server.readiness()` returns the same reasons as a list, empty when the server
is ready.

### Renaming or turning off the probes

The `probes` word of the `server` section takes comma-separated paths per
kind. An empty string turns that kind off, and `enabled=False` turns both off:

```python
from kajenn.config import AsgiConfigBuilder


class Configuration(AsgiConfigBuilder):
    def main(self, root):
        root.configuration().server().probes(liveness="/_health", readiness="/_ready")
```

The same as a kwarg: `AsgiServer(probes={"liveness": "/_health", "readiness":
["/_ready"]})`; a kind left out keeps its default, and `probes=False` turns
them all off. A probe path must start with `/` and must not be a well-known
path; the same path given twice is a `ValueError` at construction.

A probe path is answered first, so it hides an application route on the same
path. Rename the probes if an application needs that path.

Kubernetes example:

```yaml
livenessProbe:
  httpGet: {path: /healthz, port: 8000}
readinessProbe:
  httpGet: {path: /readyz, port: 8000}
```
