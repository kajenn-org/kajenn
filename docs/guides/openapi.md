# OpenAPI & Swagger

## What it does

Turns your ordinary `@route` methods into an OpenAPI 3.1 schema and a Swagger UI,
generated from the method signatures. No separate schema to maintain — the
routes *are* the spec.

## When to use it

When you want a documented, browsable REST API. Subclass `OpenApiApplication`
instead of `RoutedApplication` and declare `openapi_info`.

## Setup

`OpenApiApplication` supplies the schema and documentation endpoints.
`AsgiServer` automatically arms the `openapi` and `pydantic` plugins on its
routed applications; no explicit enable switches are needed.

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

- `openapi_info` is a class attribute carrying at least `title` and `version`.
- The fixed `openapi` and `pydantic` plugins cannot be disabled. Explicit
  `plugins` entries configure their options. A composition without
  `PluginMixin` does not supply this pair.

`OpenApiApplication` also accepts these keyword arguments:

- `docs="swagger" | "off"` — whether to serve the Swagger UI.
- `api_name="api"` — the name of the API sub-tree.
- `routing_class=<instance>` — supply the routed application to expose.
- `module="pkg.mod:Cls"` — load the routed application from a module path.

## The `_meta` URLs

The generated documentation lives under the **`_meta`** prefix (direct mode, when
the OpenAPI app is on the site root):

- `GET /_meta/schema_json` — the OpenAPI 3.1 document.
- `GET /_meta/docs` — the Swagger UI (returns `404` if `docs="off"`).
- `GET /_meta/index` — a splash HTML page.

Your endpoints themselves are served directly, e.g. `GET /search?q=moka`.

## Direct vs mounted mode

**Direct mode** — the `OpenApiApplication` declares `mount = ""`; endpoints are
at the root and meta is at `/_meta/...` (as above).

**Mounted mode** — the app is placed on a URL prefix:

```python
api = OpenApiApplication(code="mount", routing_class=SubApi())
```

- the endpoints are served under `/mount/api/...`
- the meta is served under `/mount/_meta/...`

## How to verify it

```console
$ curl "http://127.0.0.1:8000/search?q=moka&max_price=30"
{"query": "moka", "hits": []}

$ curl http://127.0.0.1:8000/_meta/schema_json
{"openapi": "3.1.0", ...}
```

Open `http://127.0.0.1:8000/_meta/docs` in a browser for the interactive Swagger
UI.

## Gotchas

- The documentation prefix is **`_meta`**, not `openapi`. Every meta URL is
  `/_meta/...`.
- `AsgiServer` supplies the schema plugins automatically. The root `openapi`
  configuration section is accepted by the grammar but has no core consumer;
  set title, version and description in the application’s `openapi_info` class
  attribute.
- `GET /_meta/docs` returns `404` when the app was created with `docs="off"`.
- The `_server` app has its own OpenAPI view of the system endpoints at
  `/_server/_meta/schema_json` and `/_server/_meta/docs` — separate from your
  app's.
- Because routing is protocol-neutral, the very same `@route` methods can also be
  exposed as MCP tools; see the [MCP guide](mcp.md).
