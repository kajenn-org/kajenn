# MCP

## What it does

Exposes your `@route` methods as **tools an AI agent can call** over the Model
Context Protocol (MCP), served as JSON-RPC over Streamable HTTP. Because
genro-routes is protocol-neutral, the same route tree answers on the `api`
channel and on MCP at once.

## When to use it

When you want an AI agent to invoke your application's operations as tools.
Every `RoutedApplication` has the MCP face on `_meta/mcp`: there is no class to
choose, only routes to declare.

## Marking a route as a tool

Expose a method as an MCP tool with `channel_channels`:

```python
from kajenn import AsgiServer, RoutedApplication
from genro_routes import route


class Calc(RoutedApplication):
    mount = ""
    openapi_info = {"title": "Calc", "version": "1.0.0"}

    @route(channel_channels="mcp")
    def add(self, a: int = 0, b: int = 0) -> dict:
        return {"result": a + b}

    @route(channel_channels="mcp,api")
    def mul(self, a: int = 0, b: int = 0) -> dict:
        return {"result": a * b}


if __name__ == "__main__":
    AsgiServer(applications=[Calc]).serve(host="127.0.0.1", port=8000)
```

Save this as `calc.py`, run `python calc.py`, and stop it with Ctrl-C after
the checks below.

- `channel_channels="mcp"` includes the method in the MCP tool surface only.
- `channel_channels="mcp,api"` also makes it callable over HTTP.

A plain HTTP request is on the channel `api`. A route declared only for `mcp`
answers `404` over HTTP: in the example `GET /add` is a 404 and
`GET /mul?a=2&b=3` answers `{"result": 6}`.

`AsgiServer` automatically arms the `pydantic` and `openapi` plugins used by
this example. Explicit entries are needed only to configure their options.

## The `_meta/mcp` endpoint

The MCP face speaks JSON-RPC on `_meta/mcp`. It negotiates one of the protocol
versions `2025-11-25`, `2025-06-18` and `2025-03-26`, and answers:

- `initialize`
- `tools/list`
- `tools/call`
- `ping`

Parameter handling for `tools/call` is the same as for an HTTP request: the
arguments bind to the method signature, typed and with defaults.

`GET /_meta/mcp/tools` returns the same list as `tools/list`, readable without
speaking MCP.

A `GET` on `_meta/mcp` opens an SSE push stream (see [streaming](streaming.md)).
It returns `405` when tasks are explicitly disabled (`tasks=False`) or the
server has no task capability. Tasks are enabled by default on `AsgiServer`.

The application's keyword arguments `mcp_name`, `mcp_version`,
`tool_separator` and `allowed_origins` configure the face; the class attributes
of the same names give the defaults.

## How to verify it

List the tools with a JSON-RPC call:

```console
$ curl -X POST http://127.0.0.1:8000/_meta/mcp \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Call one:

```bash
curl -X POST http://127.0.0.1:8000/_meta/mcp \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
         "params":{"name":"add","arguments":{"a":2,"b":3}}}'
```

## Gotchas

- Only routes carrying `channel_channels` that includes `"mcp"` appear as tools.
  A plain `@route()` answers on `api` only. A route whose channels do not
  include `"api"` is not reachable over HTTP (`404`).
- `tools/list` lists the tools the caller can call: the channel `mcp` and the
  caller's identity filter the list as they filter `tools/call`. A tool with an
  `auth_rule` appears only to a caller whose tags match it.
- `GET /_meta/mcp` (the SSE push stream) is `405` unless the server has tasks
  armed — see the [tasks guide](tasks.md).
- The URL of 0.8.0, `/mcp`, still gives the same answer with a `Deprecation`
  header and a log line, until its removal is announced; an application with a
  route of its own named `mcp` gets that route instead.
- `McpEngine` and `McpError` are importable from `kajenn`.
