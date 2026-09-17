# Frequently asked questions

## Installation, mounting and configuration

### Which package should I install?

Install `kajenn` in a virtual environment with Python 3.11 or newer. That
distribution supplies the core package `kajenn` and the base server application
`kajenn_server_app`. The multiworker SPA is the separate `kajenn-orchestra`
distribution, and the Django adapter the separate `kajenn-django`; each depends
on `kajenn` and is documented on its own pages. These pages describe the
development checkout, so check your installed version when an API differs.

See [Getting started](getting-started.md#installation).

### Why does my application return 404 at `/`?

A routed application needs a route for the requested path: an `index` method
is reached at `/index`, not automatically at `/`. `mount=""` makes the
application the root fallback; it does not create a home-page route. Without
a root application, `default="catalog"` redirects `/` to that application's
mount with status 307.

See [Mounting applications](guides/applications.md).

### What is the difference between `code` and `mount`?

`code` identifies the application in the server registry and configuration.
`mount` is its first URL segment: `code="catalog", mount="shop"` serves it
under `/shop`, and the application receives the remaining path. Use
`mount=""` for the root; `None` selects the default mount derived from the
code. Put deeper routing inside the application.

See [Mounting applications](guides/applications.md).

### Does an explicit constructor argument override my recipe?

Yes, per keyword argument. An explicit mapping replaces the configured
mapping for that argument; it is not a recursive merge. For example, passing
`middleware={...}` replaces the recipe's middleware mapping, so include every
option you intend to retain.

See [Configuration](guides/configuration.md).

### Does changing configuration automatically reconfigure a running server?

No general live reconfiguration mechanism is available. Resolvers can return
new values when read again, but that does not recreate objects or remount
applications already constructed from earlier values. The SPA's supported
profile apply/reload operations are a separate, narrower mechanism.

See [Configuration](guides/configuration.md); the profile operations belong to
`kajenn-orchestra`.

## Requests, authentication and plugins

### When do invalid arguments produce 400, 415, 422 or 500?

Every failure the core judges produces 400: a missing required argument, an
unexpected keyword, a body that is not what its content type declares, a
malformed form or multipart, and — under the default `strict` reading — values
that fit the signature but fail pydantic validation. A content type the core
cannot decode produces 415. An exception inside the handler produces 500 unless
it is an HTTP exception with its own status.

422 is the handler's, for a domain rule, unless the recipe declares the FastAPI
convention for that application (`app.request(error_codes="fastapi")`), which
answers 422 to a rejected value.

See [Requests and errors](guides/requests.md#validation-and-status-codes).

### How do I receive a JSON body as one object?

Declare a `body_data` parameter to receive the hydrated document without
spreading its fields over individual parameters. A handler accepting
`**kwargs` also receives the document under `body_data`. An application whose
recipe declares `request(body="raw")` receives every body as bytes in
`body_raw`.
Extra JSON fields are dropped when the document is spread over declared scalar
parameters.

See [Body arguments](guides/requests.md#body-arguments).

### Does the server stream large uploads to my routed handler?

No. `Request.read_body()` buffers the complete body, including multipart
uploads, and has no configurable total body-size limit. Use an ingress limit
or a directly hosted application that controls ASGI `receive` when you need
bounded or streaming upload processing.

See [Requests and errors](guides/requests.md) and
[Streaming and SSE](guides/streaming.md).

### Why do I receive 401 rather than 403?

A denied anonymous caller receives 401; an identified caller lacking the
required permissions receives 403. A browser requesting HTML may instead be
sent through the login flow. Authentication establishes identity; route
authorization rules decide what that identity may access.

See [Authentication](guides/authentication.md).

### Must I enable pydantic or OpenAPI explicitly?

`AsgiServer` automatically arms both plugins on its routed applications.
They cannot be disabled; explicit plugin entries configure their options.
A composition without `PluginMixin` does not supply this pair. For schema
title, version and description, set the application's `openapi_info` class
attribute: the root `openapi` configuration section validates but has no core
consumer.

See [OpenAPI and Swagger](guides/openapi.md).

### Does `channel_channels="mcp"` hide a route from HTTP?

No. It includes the route in the MCP tool surface, but HTTP dispatch does not
apply that channel filter. Use authorization rules to restrict callers.
Conversely, an ordinary unmarked `@route()` is not offered as an MCP tool.

See [MCP](guides/mcp.md#marking-a-route-as-a-tool).

## WebSockets and hosted workers

### Should I use WSX or a raw WebSocket?

Use WSX to send request envelopes through the server's existing application
routing. Define `serve_websocket` when your application needs its own
WebSocket protocol. The raw seam delegates accept/close, Origin checks,
authentication and cleanup to your application; it does not inherit the WSX
gates or registry.

See [WebSockets](guides/websockets.md).

### Why does my WSX connection close with code 1008?

The handshake may have selected no home application, or that application may
require a cookie the request did not carry. A SPA front requires its
`spa_connection_id` cookie. For page RPCs, also complete `openchannel` with the
page's `page_id` before sending page requests; a request before that step is
refused with 409.

See [Handshake and limits](guides/websockets.md#handshake-and-limits) and
[SPA page channels](guides/websockets.md#spa-page-channels-and-push).

### Can I stream an HTTP response through WSX or a SPA worker?

No. WSX requires finite buffered responses, and the SPA worker path collects
both the complete request and the complete response. An endless hosted SSE
response never completes its worker call. Serve incremental HTTP downloads
and SSE directly on the core instead.

See [Streaming and SSE](guides/streaming.md#where-buffering-still-applies).

### Why is `/_server/...` a 404?

The server application is declared like any other, and its code lives in
`kajenn_server_app`. Nothing mounts it for you: pass
`ServerApplication()` in `applications=`, or write it on the `applications`
section with the code `_server`. `kajenn` does not export `ServerApplication`,
`AuthSection`, `AuthMethod`, `PasswordMethod` or `OidcMethod`.

The login policy and the OIDC providers belong to the application: they are not
`authentication.login` and `authentication.oidc` in a recipe, but the same three
words written under the application's own element. There is no HTML login page
and no HTML monitor page here — those endpoints serve JSON, and gramlot renders
the pages that drive them.

See the `Server application` page of the API reference.

Named orchestration profiles, the global store and the worker pool are
documented in `kajenn-orchestra`.
