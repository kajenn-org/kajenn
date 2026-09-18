# How-to guides

Start with [Getting started](../getting-started.md), then
[configuration](../configuration.md) and [core concepts](../concepts.md).
The groups below follow that learning path. Pick an advanced capability only
when you need it; its guide names any additional prerequisites.

```{toctree}
:hidden:

applications
requests
sessions
authentication
storage
databases
management
openapi
mcp
tasks
streaming
websockets
cli
lifecycle
middleware
hidden-paths
configuration
```

## Build an application

- [Applications](applications.md): identity, mounts and hosted ASGI applications.
- [Requests and errors](requests.md): input, validation, responses and request context.

## Keep state and identify callers

- [Sessions](sessions.md): cookies and state across requests.
- [Authentication](authentication.md): credentials, identities and route permissions.

## Use resources and manage the server

- [Storage](storage.md): named paths, file operations and encryption.
- [Databases](databases.md): registration, selection and connection cleanup.
- [Management application](management.md): first login, users, tokens and monitoring.

## Add interfaces and background work

- [OpenAPI and Swagger](openapi.md): API schema and interactive documentation.
- [MCP](mcp.md): expose operations as tools for an agent.
- [Background tasks](tasks.md): submit work, observe progress and schedule jobs.
- [Streaming and SSE](streaming.md): incremental responses and event streams.
- [WebSockets](websockets.md): persistent connections, WSX and raw protocols.

## Run a site

- [CLI](cli.md): configuration files, named sites and reload.
- [Lifecycle](lifecycle.md): startup, admission and shutdown.

## Configure and extend

- [Middleware](middleware.md): shared request behaviour and custom stages.
- [Hidden paths](hidden-paths.md): protected path segments and well-known documents.
- [Configuration reference](configuration.md): defaults, resolvers and grammar composition.
- [Architecture](../architecture/overview.md): how the components fit together.

## Reading examples

Whole-file examples name a file, a command and an expected result. Fragments
state what they assume already exists. Sections labelled **In revisione** identify
specific checks that remain open; the label does not mean the entire capability
is unavailable. See the [FAQ](../faq.md) for troubleshooting.
