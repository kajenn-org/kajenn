# Remote OpenAPI example

This example mounts an `OpenApiApplication` at `/demo` while it runs in a
separate process. The frontend keeps an unrelated local `/health` route, which
continues to answer if the remote peer stops.

For an owned Unix-socket process, construct the frontend with:

```python
from examples.remote_openapi.frontend import create_server

server = create_server("uds:/tmp/genro-remote-demo.sock", own_process=True)
server.serve(host="127.0.0.1", port=8764)
```

The frontend starts and stops that child during its own lifespan. On macOS,
keep the socket under `/tmp`; Unix socket paths have a short platform limit.

To run the peer independently over loopback TCP:

```console
PYTHONPATH=$PWD/src .venv/bin/python -m kajenn.remote_runner \
  --factory examples.remote_openapi.app:create_application \
  --address tcp:127.0.0.1:8765 --mount demo
```

Then create a connect-only frontend with
`create_server("tcp:127.0.0.1:8765", own_process=False)`. A connect-only
frontend closes its connection at shutdown and leaves the independently owned
runner alive. TCP is restricted to loopback in this initial transport.

The remote API exposes `/demo/hello`, `/demo/echo`, `/demo/inspect`,
`/demo/delay`, `/demo/fail`, `/demo/protected`, `/demo/_meta/schema_json`, and
`/demo/_meta/docs`.

Requests and responses are buffered with explicit size limits. `/echo` proves
that arbitrary binary bytes survive the record codec; it does not promise
zero-copy transfer. Browser WSX messages can use the server's normal WSX
handshake and route through this mount. The remote application does not claim
the raw WebSocket protocol, streaming responses, or server-sent events.

Run the frontend directly from the repository root (use a socket/port unique to
your session):

```console
PYTHONPATH=$PWD/src .venv/bin/python -c 'from examples.remote_openapi.frontend import create_server; create_server("uds:/tmp/genro-remote-demo.sock").serve(host="127.0.0.1", port=8764)'
curl http://127.0.0.1:8764/demo/hello
curl http://127.0.0.1:8764/health
```

For the independently started TCP peer above, use this frontend command:

```console
PYTHONPATH=$PWD/src .venv/bin/python -c 'from examples.remote_openapi.frontend import create_server; create_server("tcp:127.0.0.1:8765", own_process=False).serve(host="127.0.0.1", port=8764)'
```

Open `http://127.0.0.1:8764/demo/_meta/docs` for Swagger. The schema's server URL
is `/demo`, so its operations use the same external mount as ordinary requests.
`/_meta/` remains the public metadata subtree; its explicit landing page is
`/demo/_meta/index`. Stop the frontend with Ctrl-C; an owned child stops with it.


## Docker

The same application can run in a container while this frontend stays on the
host. See [DOCKER.md](DOCKER.md) for build/start commands, the automated live
HTTP/WSX/restart proof, the optional ARM VM compatibility override, and cleanup.
The Docker example defaults to frontend port 18764 and published app port 18765,
so it can run beside the ordinary TCP example above.

## Listener ownership

Owned-process mode checks the child's launch identity at startup and on every
reconnection; a different runner at the address cannot serve that mount.
Connect-only mode remains the way to use an independently managed runner.
UDS mode refuses preexisting paths (including stale sockets and symlinks).
Choose an unused path; remove a stale path only after checking its owner.
See [the transport contract](../../docs/internal/opaque_transport.md) for details.
