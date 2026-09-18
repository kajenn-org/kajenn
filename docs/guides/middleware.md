# Middleware

## What it does

Wraps request handling in an ordered chain of cross-cutting stages — error
handling, logging, CORS, sessions and auth. Each stage is a class in the
server's registry; you arm it through config.

## When to use it

Whenever you need behaviour that applies across routes rather than inside a single
handler: turning exceptions into clean responses, adding CORS headers, logging
requests, and so on. Use the `middleware` kwarg to configure stages. On `AsgiServer`, errors,
sessions and authentication are active by default; the last two are armed by
the capability mixins.

## The built-in chain

The registry ships these middleware with a priority. The table shows the
registry default; `AsgiServer` additionally arms session and auth via its mixins.
**Lower priority number = more outer** (runs first on the way in, last on the way
out):

| Name        | Priority | Default | Notes                                        |
|-------------|----------|---------|----------------------------------------------|
| `errors`    | 100      | **on**  | maps exceptions and unmatched paths to status codes |
| `logging`   | 200      | off     | request logging                              |
| `cors`      | 300      | off     | CORS headers                                 |
| `session`   | 400      | off     | armed automatically by `SessionMixin`        |
| `auth`      | 450      | off     | armed automatically by `AuthMixin`           |

Only the `http` scope is processed by the chain.

`SessionMixin` and `AuthMixin` arm their stages simply by being composed into
`AsgiServer`, even without `session_store` or `auth` kwargs. Explicit
`middleware={"session": False, "auth": False}` disables them.

## Setup — arming a stage

Pass the `middleware` kwarg. A value of `True` arms a stage with its defaults; a
dict arms it with options; `False` disarms it.

```python
from kajenn import AsgiServer, RoutedApplication
from genro_routes import route


class App(RoutedApplication):
    mount = ""

    @route()
    def index(self) -> dict:
        return {"ok": True}


server = AsgiServer(
    applications=[App],
    middleware={
        "cors": True,
        "logging": True,
    },
)
server.serve(host="127.0.0.1", port=8000)
```

## CORS options

`cors` accepts an options dict:

```python
server = AsgiServer(
    applications=[App],
    middleware={"cors": {
        "allow_origins": ["https://example.com"],
        "allow_credentials": True,
        "max_age": 600,
    }},
)
```

## Custom middleware

Register your own middleware class in the registry, then arm it like any built-in
stage:

```python
from kajenn import BaseMiddleware


class StampMiddleware(BaseMiddleware):
    ...


server = AsgiServer(
    applications=[App],
    middleware_registry={"stamp": StampMiddleware},
    middleware={"stamp": {...}},
)
```

- `middleware_registry={"stamp": StampMiddleware}` teaches the server the new
  name.
- `middleware={"stamp": {...}}` arms it (and passes its options).

The individual built-in middleware classes are importable from
`kajenn.middleware` (e.g. `from kajenn.middleware import
CORSMiddleware`), and `BaseMiddleware` / `MiddlewareMixin` from `kajenn`.

## How to verify it

With `cors` armed, a request carrying an `Origin` gets CORS headers back:

```console
$ curl -i -H "Origin: https://example.com" http://127.0.0.1:8000/index
HTTP/1.1 200 OK
access-control-allow-origin: https://example.com
```

With `logging` armed, requests appear in the server's log output.

## Gotchas

- The registry enables `errors`; the shipped `AsgiServer` also arms `session`
  and `auth`. CORS and logging require explicit configuration.
- Hidden paths are NOT a middleware: a first path segment starting with a dot
  answers 404 in the server's own demux, always, and no switch turns it off.
  See [Hidden paths](hidden-paths.md).
- A call the handler cannot take is answered by the dispatcher, never a `500`:
  a call that does not fit the signature — an unknown keyword, a missing
  required argument, one positional too many — answers **`400`**, and so do
  values the signature accepts and the handler's validation rejects, under the
  application's default `strict` reading. An application declaring the FastAPI
  convention answers **`422`** to that one case. What the handler BODY raises is
  mapped to neither and reaches `errors` as a `500`; a handler raising
  `HTTPBadRequest` itself still answers `400`, never remapped.
- An unknown middleware name in `middleware={...}` raises `ValueError`. If you are
  arming a custom stage, register it in `middleware_registry` first.
- Ordering is by priority, lower = more outer. A custom stage lands according to
  its own priority in the chain.
- Session and auth are already armed on `AsgiServer`; configure their middleware
  options when needed, or explicitly disable them.
