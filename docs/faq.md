# Frequently asked questions

## Install and first run

### Which package should I install?

Install `kajenn` in a virtual environment with Python 3.11 or newer. That one
distribution supplies both import packages: the core `kajenn` and the base
server application `kajenn_server_app`. `import kajenn` loads none of the
server application.

See [Getting started](getting-started.md#installation).

### What is the shortest thing that runs?

A `RoutedApplication` subclass with one `@route` method, and either
`AsgiServer(applications=[App]).serve(...)` from your own entry point or
`kajenn serve application=./hello.py:Hello`. There is no `app = ...` module
global for a CLI to import: the server object *is* the ASGI application.

See [Getting started](getting-started.md#hello-world) and
[the `kajenn` command](guides/cli.md).

### Where is the home directory, and how do I move it?

`~/.kajenn` — the installation root where the command keeps the site cards
(`sites/<name>.json`), the pidfiles (`run/<name>.pid`), the session snapshots of
nameless-home sites and the optional machine defaults layer (`config.py`). Set
the environment variable `KAJENN_HOME` to move all of it at once; a container or
a virtualenv gets an isolated one that way. Nothing else is inferred from the
environment.

A *site* home is a different thing: the folder one site owns, declared with
`site_home` on the recipe or `--home` on the command. See
[the site home](guides/cli.md#the-site-home).

## Mounting and configuration

### Why does my application return 404 at `/`?

A routed application needs a route for the requested path: an `index` method is
reached at `/index`, not automatically at `/`. `mount=""` makes the application
the root fallback; it does not create a home-page route. Without a root
application, `default="catalog"` redirects `/` to that application's mount with
status 307.

See [Mounting applications](guides/applications.md).

### How do I mount more than one application?

Pass several entries to `applications=`, each a class or a `(class, params)`
pair, or write one `application` line per app in the recipe. The first path
segment picks the mount; the application receives the remaining path. At most
one application takes `mount=""`, and it answers every path no other mount
claims.

```python
server = AsgiServer(applications=[
    (Shop, {"code": "shop", "mount": ""}),
    (Api, {"code": "api"}),
])
```

See [Mounting applications](guides/applications.md) and
[Core concepts](concepts.md#the-one-demux-rule).

### What is the difference between `code` and `mount`?

`code` identifies the application in the server registry and in the
configuration tree. `mount` is its first URL segment: `code="catalog",
mount="shop"` serves it under `/shop`, and the application receives the
remaining path. `mount=""` is the root; omitting it derives the mount from the
code. Put deeper routing inside the application.

See [Mounting applications](guides/applications.md).

### What does `kajenn serve` accept?

Four sources, resolved in this order: `application=<target>` (one class, no
recipe), `template=<name>` (a ready-made configuration — `default` is the only
one today), an existing `.py` path (a recipe handed to `AsgiServer(config=…)`),
otherwise a registered site name read from its card. `--host` and `--port` are
forwarded as constructor kwargs, so the server's own precedence rule applies.

See [the `kajenn` command](guides/cli.md).

### Does an explicit constructor argument override my recipe?

Yes, per keyword argument. An explicit mapping replaces the configured mapping
for that argument; it is not a recursive merge. Passing `middleware={...}`
replaces the recipe's middleware mapping, so include every option you intend to
keep. The recipe itself is not rewritten: `server.config("server.port")` still
answers what the recipe wrote.

See [Configuration](guides/configuration.md).

### Does changing configuration reconfigure a running server?

No. There is no live-reconfiguration mechanism in the core. A resolver returns a
new value when it is read again, but that does not recreate objects or remount
applications already constructed from earlier values.

See [Configuration](guides/configuration.md).

### How do I turn debug on?

`kajenn serve … --debug` — optionally with a comma-separated parameter list —
or `AsgiServer(debug=…)`, or `cfg.server(debug=…)` in a recipe. All three land
on the `server.debug` attribute: `False`, `True`, or the parameter string it was
given. It is a declaration for whoever reads it — extra middleware, extra checks
— and the core branches on it nowhere. For request logging, arm the `logging`
middleware.

See [Configuration](guides/configuration.md) and
[Middleware](guides/middleware.md).

## Sessions, identity and the server application

### How are sessions stored?

In a `SessionStore`. One store ships with the core — `MemorySessionStore`,
sessions in the process, lost on restart. `AsgiServer` always has one, and the
session middleware is armed by `SessionMixin` without any action of yours. A
custom backend is named as a class (`session_store=MyStore`, or
`server.session(store_class=MyStore)` in a recipe) and must implement the
`SessionStore` protocol.

`server.session(save_path=…)` — `save_session=` in the shortcut form — pickles
every live session at shutdown and reloads it at the next startup. The CLI arms
it when you name an instance with `--name`. That is a development convenience,
not a production persistence story.

See [Sessions](guides/sessions.md).

### How do I add authentication?

Pass `auth={...}` to configure the header backends (`basic`, `bearer`, `jwt`),
declare the identity stores with `users=` / `tokens=`, and mark the protected
routes with `@route(auth_rule="…")`. Protection is default-deny: an anonymous
caller gets 401, an identified one whose tags do not match gets 403. The server
creates no user at boot — a deployment that needs a first identity declares the
store class that carries it.

For a session login flow over JSON, declare `ServerApplication` from
`kajenn_server_app`; its `POST /_server/login` verifies against the user store
and attaches the avatar to the existing session.

See [Authentication](guides/authentication.md).

### Why do I receive 401 rather than 403?

A denied anonymous caller receives 401; an identified caller lacking the
required tags receives 403. An `Authorization` header that is present but
invalid is 401 too, and does not fall back to the session cookie. Authentication
establishes identity; `auth_rule` decides what that identity may reach. Neither
answer redirects a browser to a login page: the core owns none, and the error
middleware only picks the error body from the caller's `Accept`.

See [Authentication](guides/authentication.md).

### Where do the server application's endpoints live?

Under `/_server/…`, and only if you declared it. `ServerApplication` lives in
`kajenn_server_app` and is mounted like any other application, with the code
`_server`; nothing mounts it for you, and a server that declares none has no
`/_server/…` at all. It serves JSON — login, users, tokens, tasks and monitor —
and no HTML pages: the management pages belong to a front-end of your choosing.

`kajenn` does not export `ServerApplication`, `AuthSection`, `AuthMethod`,
`PasswordMethod` or `OidcMethod`; import them from `kajenn_server_app`.

See the [Server application](api/server-app.rst) API page.

### How do I stop a registered server?

`kajenn stop <name>` sends SIGTERM to the pid recorded in
`<KAJENN_HOME>/run/<name>.pid` — from any shell, and to the reload supervisor
when the server was started with `--reload`. A pidfile that is missing,
unreadable or names a dead process reads as not running, and `stop` cleans it
up. A foreground server stops with Ctrl-C.

See [the `kajenn` command](guides/cli.md).

## Requests, plugins and protocols

### When do invalid arguments produce 400, 415, 422 or 500?

Every failure the core judges answers 400: a missing required argument, an
unexpected keyword, a body that is not what its content type declares, a
malformed form or multipart, and — under the default `strict` reading — values
that fit the signature but fail pydantic validation. A content type the core
cannot decode answers 415. An exception raised inside the handler answers 500
unless it is an HTTP exception with its own status.

422 belongs to the handler, for a domain rule, unless the recipe declares the
FastAPI convention for that application (`app.request(error_codes="fastapi")`),
which answers 422 to a rejected value.

See [Requests and errors](guides/requests.md#validation-and-status-codes).

### How do I receive a JSON body as one object?

Declare a `body_data` parameter to receive the hydrated document without
spreading its fields over individual parameters. An application whose recipe
declares `request(body="raw")` receives every body as bytes in `body_raw`. Extra
JSON fields are dropped when the document is spread over declared scalar
parameters.

See [Body arguments](guides/requests.md#body-arguments).

### Does the server stream large uploads to my routed handler?

No. `Request.read_body()` buffers the complete body, including multipart
uploads, and has no size limit of its own — `KAJENN_HTTP_MAX_BODY_BYTES` bounds
the records the channel carries, not this read. Use an ingress limit, or an
application that drives ASGI `receive` itself, when you need bounded or
streaming upload processing.

See [Requests and errors](guides/requests.md) and
[Streaming and SSE](guides/streaming.md).

### Must I enable pydantic or OpenAPI explicitly?

No. `AsgiServer` arms both plugins on every routed application, and they cannot
be disabled — `plugins={"openapi": False}` is a configuration error, not an
opt-out. Explicit entries only configure their options. A composition without
`PluginMixin` does not supply the pair.

### How do I expose OpenAPI and a Swagger page?

Subclass `OpenApiApplication` instead of `RoutedApplication` and set
`openapi_info = {"title": ..., "version": ...}`. The schema is then at
`/_meta/schema_json` and the Swagger UI at `/_meta/docs` (404 when the app was
built with `docs="off"`). The root `openapi` configuration section is accepted
by the grammar but has no consumer in the core: set title, version and
description on `openapi_info`.

See [OpenAPI and Swagger](guides/openapi.md).

### How do I expose MCP tools?

Subclass `McpApplication` (the whole application is one JSON-RPC endpoint) or
`McpOpenApiApplication` (REST plus OpenAPI plus an MCP face under
`mcp_name_segment`, `mcp` by default), and mark the methods you want offered as
tools with `@route(channel_channels="mcp")`. A plain `@route()` is not offered
as a tool.

See [MCP](guides/mcp.md).

### Does `channel_channels="mcp"` hide a route from HTTP?

No. It includes the route in the MCP tool surface; HTTP dispatch applies no
channel filter. Use authorization rules to restrict callers.

See [MCP](guides/mcp.md#marking-a-route-as-a-tool).

### Should I use WSX or a raw WebSocket?

Use WSX to send request envelopes through the server's existing application
routing: a message becomes a synthetic HTTP request with the method `WSK` and
travels the ordinary demux. Define `serve_websocket` when your application needs
a protocol of its own; that raw seam owns accept/close, Origin checks,
authentication and cleanup, and inherits neither the WSX gates nor the registry.

See [WebSockets](guides/websockets.md).

### Why does my WSX connection close with code 1008?

The handshake path selected no home application, or that application declares a
`handshake_cookie` the request did not carry. Both are accepted and then closed
with 1008 so the client can read why. A hostile Origin is refused before the
accept instead, and the client sees a failed handshake.

See [Handshake and limits](guides/websockets.md#handshake-and-limits).

### Can I stream an HTTP response through WSX?

No. WSX requires a finite, buffered response: an ASGI response chunk with
`more_body=True` is rejected and the call becomes a 500. Serve incremental HTTP
downloads and SSE directly on the core instead.

See [Streaming and SSE](guides/streaming.md#where-buffering-still-applies).

## Deployment

### Can I run kajenn behind a reverse proxy?

You can put one in front of it, but the core does **not** read proxy headers:
there is no `X-Forwarded-*` handling and no `forwarded_allow_ips` option
anywhere in `src/kajenn/`, and the server never rewrites `root_path`. A handler
therefore sees the address and scheme of the connection the proxy opened, not of
the browser. Two consequences:

- terminate TLS at the proxy and set `middleware={"session": {"secure": True}}`
  yourself; nothing infers it;
- declare the public base address explicitly with `external_url`, which is what
  the OIDC flow hands a provider as an absolute `redirect_uri`. It is separate
  from `host`/`port`, which are only where the process binds.

Mounting the server under a URL sub-path on the proxy is not supported by the
core: the demux reads the path it receives.

### Does the CLI start more than one process?

No. `kajenn serve` starts one server process, plus a reload supervisor when you
pass `--reload`. There is no `--workers` option.

See [the `kajenn` command](guides/cli.md).
