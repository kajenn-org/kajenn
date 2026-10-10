# Mounting applications

A `BaseApplication` has a `code` (its registry name) and `mount` (its first URL
segment). Both may be class attributes or constructor kwargs. By default the
code is the lowercase class name and the mount is the code. `mount=""` is the
root application; `mount=None` requests the default mount.

```python
from kajenn import AsgiServer, RoutedApplication
from genro_routes import route


class Greeting(RoutedApplication):
    @route()
    def index(self):
        return {"application": self.code}


server = AsgiServer(applications=[
    (Greeting, {"code": "home", "mount": ""}),
    (Greeting, {"code": "catalog", "mount": "shop"}),
])
```

`/index` reaches `home`; `/shop/index` reaches `catalog` with `path="/index"`.
A named mount wins over the root application. Without a root application,
`default="catalog"` makes `/` redirect to `/shop/` with status 307 and preserves
the query string. Other unmatched paths remain 404. A mount is one segment;
put deeper routing inside the application.

## Hosting another ASGI application directly

Wrap an existing ASGI callable in `BaseApplication`. This small adapter delegates
HTTP and explicitly delegates WebSocket scopes to the hosted application:

```python
from kajenn import BaseApplication


class HostedApplication(BaseApplication):
    def __init__(self, asgi_app, **kwargs):
        self.asgi_app = asgi_app
        super().__init__(**kwargs)

    async def __call__(self, scope, receive, send):
        await self.asgi_app(scope, receive, send)

    async def serve_websocket(self, scope, receive, send):
        await self.asgi_app(scope, receive, send)
```

Pass the class and its constructor parameters to the server (the hosted callable
itself remains an instance):

```python
server = AsgiServer(applications=[
    (HostedApplication, {"asgi_app": existing_asgi_app, "code": "external"}),
])
```

This is a composition fragment: supply your existing ASGI callable first. The
server strips the mount from `path`; it does **not** add it to `root_path`.
If a hosted framework needs a public URL prefix for generated links, configure
or adapt that framework's scope handling explicitly. This adapter does not
forward ASGI lifespan events: initialize and close the hosted framework through
the adapter's `on_startup` and `on_shutdown` hooks as required by that framework.

The raw WebSocket seam owns its handshake and Origin/auth checks. Read
[WebSockets](websockets.md) before exposing it.

To host an application in **another process**, declare it with `spawner=` on
its own `application` element (`spawner="subprocess"`): the server starts a
process from the same configuration that serves only that application, and its
routes stay reachable through `server.kbus_call(path, data)`. The process
connects back to the server over the kbus library; `server.kbus(address=...)`
chooses where the server listens — `unix://<path>` or
`wss://<host>:<port>/<path>` with `certfile`, `keyfile` and optionally
`cafile`, a private unix socket when omitted; plain `ws://` is refused — and its
limits `max_frame`, `max_meta`, `max_route`, `max_pending`, `stream_window` and
`write_buffer` bound what travels. See [the KajennBus protocol](../design/kbus-protocol.md).

The mount in the server is a transparent wire: the application receives a
request exactly as it would in the server's process — method, path, query
string, headers, channel, identity, the `kajenn.kbus` flag of a bus call, the
`genro.page_id` and `genro.reply_path` of a WSX message — and the caller gets
the same status, headers and body. An exception the application raises before
answering is raised again in the server, whose middleware answers it. The mount
forwards every request header, the `Authorization` header included, and never
verifies a credential; the channel the request carries travels with it. The
process verifies the credential through the server's authentication route for
that channel, reached over the bus. A session lives in the server: the proxy
forwards its identity, and only when the request carries no `Authorization`
header. In the process `scope["session"]` is `None`: a handler reads the
identity, not the session object.
