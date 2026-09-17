# Architecture overview

> **Status:** Draft; implementation checked against the development source on 2026-09-08.

This page explains how kajenn is put together and the principles behind it.
It is *explanation*, not a how-to: read it to understand why the pieces are
shaped the way they are. The normative source is
[`SPECIFICATION.md`](https://github.com/kajenn-org/kajenn/blob/main/SPECIFICATION.md)
(the decision log, D1…); this page summarizes it and never contradicts it.

## Core principles

These are the guiding principles of the redesign (SPECIFICATION.md §1). They
explain most of the design decisions you will meet in the code.

- **No globals.** The server is an instance with its own state — no module-level
  variables, no singletons. Use the lifespan shutdown to release resources; deleting a reference does not
  replace stopping active tasks, threads or child processes.
  State lives in objects, connected by semantic parent references.
- **Config is data, not structure.** What a server *is* comes from a
  configuration recipe rendered onto it; the code shape does not change with the
  deployment.
- **Objects always exist; backends come from config.** There is no `X | None`
  attribute flipped on by a flag. The session store, the auth core, the task
  manager are always there — their *backend* is what configuration selects.
- **Work at the time of use.** Expensive machinery (the thread pool, the task
  manager) is provisioned lazily, on first use.
- **Routes are static from boot.** The routing tree is built once; routing is
  never used as a mutable registry.
- **Extension by subclassing; capabilities as mixins.** You add behaviour by
  subclassing an application, and the server composes capabilities (auth,
  session, tasks…) as mixins over a base.

## The request flow

```{figure} ../_static/diagrams/http-flow.svg
:figclass: flow-diagram
:alt: Enabled HTTP middleware wraps core dispatch, which selects an application and calls its routed handler.

The response returns through ASGI send. Middleware may answer before core dispatch.
```

```text
uvicorn
  → AsgiServer                      the server IS the ASGI app
    → middleware chain              errors → logging → cors → session → auth
      → demultiplex                 dotted first segment → 404 (hidden path)
                                    else first path segment → mount, else root app
        → application               a RoutedApplication (or a subclass)
          → @route handler(**params)
            → Response              buffered, or a StreamingResponse
              → ASGI send
```

Each middleware class declares numeric `middleware_order`; the chain sorts by
it, lowest outermost. The registry enables `errors` by default; `AsgiServer` also arms `session`
and `auth` through its mixins, even without explicit credential/store kwargs.

## The two layers: server and application

kajenn separates *what the server owns* from *what an application does*.

### BaseServer

`BaseServer` is the common substrate of every server (SPECIFICATION.md §4, D2).
It owns: one uvicorn loop, one monitored thread pool for blocking work, the
**applications** it was composed with (a dict keyed by each app's `code`, plus
an index by `mount` — the URL prefix each one answers under, `""` being the site
root), the lifespan (ordered startup/shutdown), and the request registry. At the
base, `authenticate()` and `session()` answer "nobody / none" — auth and sessions
are capabilities layered on top, not built into the base.

The one dispatch rule (D3) is: **first path segment → the app mounted there;
else the app on the site root; else, for `/` with a `default` declared, a 307 to
it; else 404.** A single-app server is just a base server whose only app sits on
the root — there is no separate mechanism.

### AsgiServer

`AsgiServer` is the shipped composition (D22): it stacks every capability mixin
over `BaseServer` in one MRO — communication, auth, session, middleware,
plugins, storage, tasks. This is the complete mono-process async server. You
configure a capability through a constructor kwarg (`auth=…`, `middleware=…`,
`tasks=…`, `plugins=…`); the mixin peels the kwargs it understands and forwards
the rest down the cooperative `__init__` chain.

Because auth is a mixin and not part of the base, an internal (non-public)
server can compose the *same* base **without** the auth mixin — its auth and
sessions are `None` by design, and whoever fronts it owns them (D1, D6).

### BaseApplication and RoutedApplication

`BaseApplication` is the app-side contract: an ASGI callable with a `code` (its
identity), a `mount` (the URL prefix it answers under) and a `server` reference
assigned once by the owning server at attach time (a second assignment raises).
`RoutedApplication` wires
[genro-routes](https://pypi.org/project/genro-routes/) into it: handlers are
`@route`-decorated methods, resolved through the app's own router. The
`OpenApiApplication`, `McpApplication` and `McpOpenApiApplication` subclasses add
protocol faces (OpenAPI/Swagger, MCP) over the *same* route tree — which is why
one decorated method can serve REST and MCP at once.

## The `_server` application

`ServerApplication` exposes the server's own management surface — login, users,
tokens, tasks — under `/_server/…`, and its OpenAPI schema at
`/_server/_meta/schema_json`. It is declared like any other application, with
the code `_server` (D-SA-10): a hand-built server passes it in `applications=`,
a configured one writes it on the `applications` section. Nothing mounts it
behind the caller's back, and a server that declares none exposes no
`/_server/…` at all.

## Configuration: the server reads its own

A configuration is a **recipe** — a subclass of `AsgiConfigBuilder` whose `main`
opens the `configuration` root and delegates each section to its own method —
and the server builds its own read door over it:

```python
from kajenn import AsgiServer
from kajenn.config import AsgiConfigBuilder

class ServerConfiguration(AsgiConfigBuilder):
    def main(self, root):
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8000)

server = AsgiServer(config=ServerConfiguration)
```

`AsgiServer(config=…)` accepts a recipe class, a recipe instance, a path to a
`config.py`, or a ready configuration handler (see the
[configuration API](../api/config.rst)). The handler is exposed as
`server.config` and is **callable by path** — `server.config("server.host")` —
over a four-layer read stack: the written value, the element signature's
default, the call-site `default=`, then a noisy `KeyError`. Explicit
constructor kwargs win over configured ones, per kwarg.

Values that come from outside the recipe are **resolvers in place**: you store a
`genro_bag.resolvers.EnvResolver` where the value would go and it resolves at
read time, so the runtime always consumes the environment's current value. An
application reads its own subtree through `app.config(path)`, which prefixes
`applications.<code>.` and delegates to the same door — an app holds an address
in the tree, never a slice of it.

The how-to — writing the recipe, the resolvers, the read stack, an application's
own grammar — is the [Configuration guide](../guides/configuration.md).

## WebSockets and the multiworker package

The core implements [WSX and raw WebSocket hosting](../guides/websockets.md).
WSX messages enter the application through a synthetic HTTP scope, using the
same routing and cleanup logic; they bypass the HTTP middleware chain.

The separate top-level package `kajenn_orchestra` supplies the SPA front,
commander, worker processes and hosted ASGI/WSGI adapters. It ships in the
`kajenn-orchestra` distribution and is documented there. Importing `kajenn` does
not import that package. A hosted worker request and response are buffered; this
path is distinct from the core's direct HTTP streaming.

Application hooks and the server's admission state are described in
[Lifecycle](../guides/lifecycle.md).

## Where to go next

- [Getting started](../getting-started.md) — install and run.
- [Concepts](../concepts.md) — the model in more practical terms.
- [`SPECIFICATION.md`](https://github.com/kajenn-org/kajenn/blob/main/SPECIFICATION.md) — the full decision log.
