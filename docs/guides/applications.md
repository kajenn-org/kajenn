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

To host the application in **another process** instead of this one, the core
offers `RemoteApplication` (`kajenn.remote_application`) and the runner it talks
to: both ends buffer the complete request and the complete response, so that
path carries no streaming. See [the channel protocol](../design/channel-protocol.md).
