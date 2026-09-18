# How-to Guides

Task-focused recipes for the capabilities kajenn grows on top of the core.
For troubleshooting and common choices, see the [FAQ](../faq.md).

Each one assumes you have read [Getting started](../getting-started.md) and
[Core concepts](../concepts.md). For how the pieces fit together, read the
[Architecture overview](../architecture/overview.md).

```{toctree}
:hidden:

authentication
sessions
openapi
mcp
tasks
streaming
middleware
hidden-paths
configuration
cli
requests
applications
lifecycle
websockets
```

## The guides

- **[Authentication](authentication.md)** — configure basic / bearer / JWT
  backends and API keys, protect routes with `auth_rule`, and work with the
  `Avatar` identity.
- **[Sessions](sessions.md)** — arm the session subsystem, use the memory store and shutdown snapshot, control the session cookie, and attach an avatar at login.
- **[OpenAPI & Swagger](openapi.md)** — turn `@route` methods into an OpenAPI 3.1
  schema and a Swagger UI under the `_meta` prefix, direct or mounted.
- **[MCP](mcp.md)** — expose routes as tools an AI agent can call over MCP
  Streamable HTTP, standalone or side-by-side with REST.
- **[Background tasks](tasks.md)** — arm the task backbone, fire off
  fire-and-forget work, schedule interval/cron jobs, and manage them over HTTP.
- **[Streaming & SSE](streaming.md)** — return chunked bodies with
  `StreamingResponse` and Server-Sent Events with `SseStream`.
- **[Middleware](middleware.md)** — the built-in chain, its order and defaults,
  how to arm each stage, and how to register a custom middleware.
- **[Hidden paths](hidden-paths.md)** — the dotted first segment answered 404
  before any application, and the `.well-known` documents an application
  declares.
- **[Configuration](configuration.md)** — write the recipe a server reads itself
  from, keep secrets out of it with resolvers, and read values back through the
  server and its applications.
- **[The `kajenn` command](cli.md)** — boot a server from a `config.py` or a
  single application, manage the configured sites with `configure`/`sites`/`stop`/`remove`, and
  reload on source changes.
- **[Requests and errors](requests.md)** — body decoding, uploads, validation and status codes.
- **[Mounting applications](applications.md)** — prefixes, root dispatch and hosted ASGI applications.
- **[Lifecycle](lifecycle.md)** — startup hooks, admission state and bounded shutdown.
- **[WebSockets](websockets.md)** — WSX messages, handshake rules and the raw socket seam.

## How each guide is structured

Capability recipes use the following sections where applicable:

1. **What it does** — the capability in one or two sentences.
2. **When to use it** — the situation that calls for it.
3. **Setup** — the constructor kwargs or base class you need in place.
4. **Minimal snippet** — the smallest copy-pasteable example that works.
5. **How to verify it** — a concrete check (a `curl`, a request, an observed
   effect) that proves it is working.
6. **Gotchas** — the sharp edges worth knowing before you hit them.
