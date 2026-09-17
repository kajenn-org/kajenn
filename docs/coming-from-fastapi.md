# Coming from Starlette / FastAPI

kajenn is an ASGI server with the features FastAPI leaves to the user, and a
base server application included. It is based on genropy history and
genro-modules: the capabilities below are not a roadmap, they are the modules
the package installs.

If you know Starlette or FastAPI, the small things carry over: you decorate a
callable to make a route, parameters bind to the signature, and OpenAPI and
Swagger come for free. This page maps the rest.

## What FastAPI leaves to you

What surrounds an endpoint — who the caller is, what survives between two
requests, what runs after the response, where files live — you assemble from
other libraries. kajenn ships those parts and feeds them from configuration.

| Capability | FastAPI | kajenn |
|---|---|---|
| Authentication | `Security(...)` schemes; the backend is yours | basic / bearer / JWT backends, a user store and an API-key store, `auth_rule` per route, default deny |
| Sessions | Starlette's signed-cookie middleware, or your own store | a session object with its store and its cookie, an `Avatar` identity attached at login, a snapshot at shutdown |
| WebSockets | a raw `WebSocket` object | WSX — request envelopes routed to the same methods as HTTP — plus the raw socket seam when you want it |
| Internal channel | none | a hub and its clients over a unix socket or TCP, with typed frames, used to reach other processes |
| Background work | `BackgroundTasks` for the current response | a task backbone: file spool, executor, scheduler with interval and cron cadences, event hub, HTTP management |
| MCP | none | routes marked `channel_channels="mcp"` are served as MCP tools by the same route tree |
| Storage | none | genro-storage mounted on the server, named volumes, optional encryption at rest |
| Database | none | a handler contract for a mounted database, reachable as `request.db` |
| Configuration | environment variables and your own loader | a recipe file the server reads itself from, with resolvers that keep secrets out of it |
| Server application | none | `/_server` with login, users, tokens, tasks and monitor sections, mounted like any other application |

## The mental model, side by side

**FastAPI** gives you an application object; you attach path operations to it,
and a container injects into each operation what it declares. **kajenn** gives
you a *server that mounts applications*. An application is a
class whose `@route`-decorated **methods** are its endpoints. The server is a
composition of capability mixins — auth, sessions, tasks, storage, plugins —
that always exist and are fed by config data rather than switched on
structurally. There is no dependency-injection container: a handler reaches what
it needs through the object graph (its application, the server, the request).

Routing is **protocol-neutral**: a `@route` describes an operation independently
of transport, which is why the *same* method tree is served as REST, as an
OpenAPI schema, as MCP tools and as WSX messages without being rewritten.

## Concept mapping

| Task | FastAPI / Starlette | kajenn |
|------|---------------------|--------|
| Create the app | `app = FastAPI()` | subclass `RoutedApplication` (or `OpenApiApplication`); build `AsgiServer(applications=[App])` |
| Define a route | `@app.get("/greet")` on a function | `@route()` on a **method** (name = URL segment), imported from `genro_routes` |
| Middleware | `app.add_middleware(...)` | `middleware={...}` kwarg; ordered built-in chain |
| Start the server | `uvicorn.run(app, ...)` | `server.serve(host=..., port=...)` (programmatic uvicorn, blocking) |

## The same endpoint

```python
from kajenn import AsgiServer, OpenApiApplication
from genro_routes import route


class Shop(OpenApiApplication):
    mount = ""
    openapi_info = {"title": "Shop API", "version": "1.0.0"}

    @route()
    def search(self, q: str = "", max_price: float = 100.0) -> dict:
        return {"query": q, "hits": []}


server = AsgiServer(applications=[Shop])
server.serve(host="127.0.0.1", port=8000)
```

The visible difference from the FastAPI equivalent is small: a method on a class
instead of a free function, and a server that mounts the app instead of *being*
the app. The invisible one matters more — switch the base class to
`McpOpenApiApplication` and mark `search` with
`@route(channel_channels="mcp,rest")`, and the same method is also an MCP tool.

## Error codes

FastAPI answers **422** when a request is well formed but its values fail
validation. kajenn reads HTTP strictly: every failure the core judges — a body
that is not what its content type declares, a malformed form, arguments that do
not fit the signature, a value pydantic rejects — answers **400**, and a content
type it cannot decode answers **415**. The core never produces 422: that code
belongs to the handler, for a domain rule it decides itself.

An application that serves clients expecting the FastAPI convention declares it
in the recipe that mounts it, with `app.request(error_codes="fastapi")`. Only
the rejected-value case moves; everything else stays 400 or 415 under both
conventions, and the generated OpenAPI document declares the code the
application chose.

## What does not port

- **`Depends`.** Handlers compose objects instead of declaring dependencies.
- **The CLI target.** `uvicorn main:app` points at an ASGI callable; `kajenn
  serve ./config.py` points at a configuration recipe the server builds itself
  from. `kajenn serve application=./hello.py:Hello` is the closer analogue for a
  single application with no recipe of its own.
- **The OpenAPI prefix.** `/_meta/docs` and `/_meta/schema_json`, not `/docs`.

## Where to go next

- **[Getting started](getting-started.md)** — the runnable hello-world.
- **[Core concepts](concepts.md)** — the server/application model in full.
- **[How-to guides](guides/index.md)** — auth, sessions, OpenAPI, MCP, tasks.

<!-- sources, verified in genropy/genro-asgi at 9ebfa56: auth src/kajenn/auth/ and
     middleware/authentication.py · sessions src/kajenn/session/ and middleware/session.py ·
     wsx src/kajenn/{websocket,wsx,wsx_payload}.py · channel src/kajenn/channel/ ·
     tasks src/kajenn/tasks/ · mcp src/kajenn/mcp/ and applications/mcp.py · storage
     src/kajenn/storage_mixin.py · db src/kajenn/db.py · configuration src/kajenn/config/ ·
     openapi src/kajenn/applications/openapi.py and plugins/openapi/ · streaming
     src/kajenn/{streaming,sse}.py · error_codes src/kajenn/application.py:93 · serve
     src/kajenn/asgi_server.py:288 · cli src/kajenn/__main__.py · server application
     src/kajenn_server_app/ -->
