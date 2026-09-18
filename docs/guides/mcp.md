# MCP

## What it does

Exposes your `@route` methods as **tools an AI agent can call** over the Model
Context Protocol (MCP), served as JSON-RPC over Streamable HTTP. Because
genro-routes is protocol-neutral, the same route tree can answer REST and MCP at
once.

## When to use it

When you want an AI agent to invoke your application's operations as tools —
either as a pure MCP endpoint or side-by-side with a REST/OpenAPI face.

## Setup

Two base classes cover the two shapes:

- **`McpApplication`** — the whole application is a single JSON-RPC MCP endpoint.

  ```python
  from kajenn import McpApplication
  app = McpApplication(routing_class=..., code="mcp")
  ```

- **`McpOpenApiApplication`** — a dual-faced application: a REST/OpenAPI face
  *and* an MCP face served on `/mcp`. The MCP segment is configurable via
  `mcp_name_segment="mcp"`.

  ```python
  from kajenn import McpOpenApiApplication
  # subclass of OpenApiApplication: REST + OpenAPI + MCP on /mcp
  ```

Both are importable from `kajenn`.

## Marking a route as a tool

Expose a method as an MCP tool with `channel_channels`:

```python
from kajenn import AsgiServer, McpOpenApiApplication
from genro_routes import route


class Calc(McpOpenApiApplication):
    mount = ""
    openapi_info = {"title": "Calc", "version": "1.0.0"}

    @route(channel_channels="mcp")
    def add(self, a: int = 0, b: int = 0) -> dict:
        return {"result": a + b}

    @route(channel_channels="mcp,rest")
    def mul(self, a: int = 0, b: int = 0) -> dict:
        return {"result": a * b}


if __name__ == "__main__":
    AsgiServer(applications=[Calc]).serve(host="127.0.0.1", port=8000)
```

Save this as `calc.py`, run `python calc.py`, and stop it with Ctrl-C after
the checks below.

- `channel_channels="mcp"` includes the method in the MCP tool surface.
- `channel_channels="mcp,rest"` declares both channel names for consumers that
  filter by channel.

HTTP dispatch does not apply this channel filter. Either declaration leaves
the route callable directly over HTTP; channel metadata is not an HTTP access
restriction. Use authorization rules to restrict callers.

`AsgiServer` automatically arms the `pydantic` and `openapi` plugins used by
this example. Explicit entries are needed only to configure their options.

## The `/mcp` endpoint

The MCP face speaks JSON-RPC on `/mcp`. It negotiates one of the protocol
versions `2025-11-25`, `2025-06-18` and `2025-03-26`, and answers:

- `initialize`
- `tools/list`
- `tools/call`
- `ping`

Parameter handling for `tools/call` is the same as for REST: the arguments bind
to the method signature, typed and with defaults.

A `GET` on `/mcp` opens an SSE push stream (see [streaming](streaming.md)). It
returns `405` when tasks are explicitly disabled (`tasks=False`) or the server
has no task capability. Tasks are enabled by default on `AsgiServer`.

## How to verify it

List the tools with a JSON-RPC call:

```console
$ curl -X POST http://127.0.0.1:8000/mcp \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Call one:

```bash
curl -X POST http://127.0.0.1:8000/mcp \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
         "params":{"name":"add","arguments":{"a":2,"b":3}}}'
```

## Gotchas

- Only routes carrying `channel_channels` that includes `"mcp"` appear as tools.
  A plain `@route()` is not offered as an MCP tool. Channel metadata does not
  prevent HTTP access; authorization rules govern access restrictions.
- `GET /mcp` (the SSE push stream) is `405` unless the server has tasks armed —
  see the [tasks guide](tasks.md).
- The MCP JSON-RPC lives on `/mcp` (segment configurable via
  `mcp_name_segment`), separate from the OpenAPI `_meta` prefix.
- `McpApplication`, `McpOpenApiApplication`, `McpEngine` and `McpError` are
  importable from `kajenn`.
