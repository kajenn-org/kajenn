# OpenAPI & Swagger

## What it does

Turns your ordinary `@route` methods into an OpenAPI 3.1 schema and a Swagger UI,
generated from the method signatures. No separate schema to maintain — the
routes *are* the spec.

## When to use it

Always available: every `RoutedApplication` has the `api` face, described by
OpenAPI under `_meta/api`. Declare `openapi_info` to give the document a title
and a version.

## Setup

`AsgiServer` automatically arms the `openapi` and `pydantic` plugins on its
routed applications; no explicit enable switches are needed.

```python
from kajenn import AsgiServer, RoutedApplication
from genro_routes import route


class Shop(RoutedApplication):
    mount = ""
    openapi_info = {"title": "Shop API", "version": "1.0.0"}

    @route()
    def search(self, q: str = "", max_price: float = 100.0) -> dict:
        return {"query": q, "hits": []}


server = AsgiServer(applications=[Shop])
server.serve(host="127.0.0.1", port=8000)
```

- `openapi_info` is a class attribute carrying at least `title` and `version`.
- The fixed `openapi` and `pydantic` plugins cannot be disabled. Explicit
  `plugins` entries configure their options. A composition without
  `PluginMixin` does not supply this pair.
- `docs="off"` among the application's keyword arguments turns off the Swagger
  UI page only; the schema and the routes stay.

## The `_meta` URLs

`_meta` is the one segment a routed application reserves for itself: a route
of yours cannot be named `_meta`. Under it there is one branch per channel:

- `GET /_meta/index` — the application page, with a link to the docs.
- `GET /_meta/api/schema_json` — the OpenAPI 3.1 document.
- `GET /_meta/api/docs` — the Swagger UI (returns `404` if `docs="off"`).
- `GET /_meta/mcp/tools` — the MCP tools; see the [MCP guide](mcp.md).

Your endpoints themselves are served directly, e.g. `GET /search?q=moka`. An
application mounted on a prefix serves the same URLs under it:
`/shop/search`, `/shop/_meta/api/schema_json`.

## Exposing a routing class you already have

A `RoutingClass` written on its own is exposed by an application that attaches
it in its constructor. This fragment assumes `SubApi` is your routing class:

```python
class Holder(RoutedApplication):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.route.add_branches({"name": "api", "instance": SubApi()})


server = AsgiServer(applications=[(Holder, {"code": "mount"})])
```

- the routes of `SubApi` are served under `/mount/api/...`
- the meta is served under `/mount/_meta/...`

## How to verify it

```console
$ curl "http://127.0.0.1:8000/search?q=moka&max_price=30"
{"query": "moka", "hits": []}

$ curl http://127.0.0.1:8000/_meta/api/schema_json
{"openapi": "3.1.0", ...}
```

Open `http://127.0.0.1:8000/_meta/api/docs` in a browser for the interactive
Swagger UI.

## Gotchas

- The documentation lives under **`_meta/api`**. The URLs of 0.8.0,
  `/_meta/schema_json` and `/_meta/docs`, still give the same answer with a
  `Deprecation` header and a log line, until their removal is announced.
- A route that declares no `channel_channels` answers on the `api` channel
  only. A route you also call over WSX declares it:
  `@route(channel_channels="api,wsx")`.
- `AsgiServer` supplies the schema plugins automatically. The root `openapi`
  configuration section is accepted by the grammar but has no core consumer;
  set title, version and description in the application's `openapi_info` class
  attribute.
- `GET /_meta/api/schema_json` describes the routes the caller can execute: the
  request's channel and the caller's identity filter the schema as they filter
  execution. A route with an `auth_rule` appears only to a caller whose tags
  match it; an invalid credential answers `401`.
- The `_server` app has its own OpenAPI view of the system endpoints at
  `/_server/_meta/api/schema_json` and `/_server/_meta/api/docs` — separate
  from your app's.
- Because routing is protocol-neutral, the very same `@route` methods can also be
  exposed as MCP tools; see the [MCP guide](mcp.md).
