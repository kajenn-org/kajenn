# Middleware

## What it does

Wraps request handling in an ordered chain of cross-cutting stages — security
headers, error handling, logging, CORS and sessions. Each stage is a class in the
server's registry; you arm it through config.

## When to use it

Whenever you need behaviour that applies across routes rather than inside a single
handler: turning exceptions into clean responses, adding CORS headers, logging
requests, hardening every response with security headers, and so on. Use the
`middleware` kwarg to configure stages. On `AsgiServer`, security headers, errors
and sessions are active by default; sessions are armed by `SessionMixin`.
Authentication is not a stage of the chain.

## The built-in chain

The registry ships these middleware with a priority. The table shows the
registry default; `AsgiServer` additionally arms session via `SessionMixin`.
**Lower priority number = more outer** (runs first on the way in, last on the way
out):

| Name               | Priority | Default | Notes                                        |
|--------------------|----------|---------|----------------------------------------------|
| `security_headers` | 50       | **on**  | browser-hardening response headers           |
| `errors`           | 100      | **on**  | maps raised exceptions to HTTP responses     |
| `logging`          | 200      | off     | request logging                              |
| `cors`             | 300      | off     | CORS headers                                 |
| `session`          | 400      | off     | armed automatically by `SessionMixin`        |

Only the `http` scope is processed by the chain.

`SessionMixin` arms its stage simply by being composed into `AsgiServer`, even
without a `session_store` kwarg. Explicit `middleware={"session": False}`
disables it. Authentication is not a middleware: the execution point resolves
the identity of every request (see [authentication](authentication.md)).

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

## Security headers

`security_headers` is on by default and adds browser-hardening headers to every
HTTP response. Without configuration it sends only the headers that cannot
break a working application:

| Header                      | Default value                       | When                         |
|-----------------------------|-------------------------------------|------------------------------|
| `X-Content-Type-Options`    | `nosniff`                           | always                       |
| `X-Frame-Options`           | `SAMEORIGIN`                        | always                       |
| `Referrer-Policy`           | `strict-origin-when-cross-origin`   | always                       |
| `Strict-Transport-Security` | `max-age=31536000`                  | only when the request scheme is `https` |

The headers that depend on what your pages load or embed stay off until you
configure them. The stage is priority 50, outside `errors`, so error responses
(404, 500, ...) carry the same headers.

Options — each named option takes the header value as a string; `None` (or
`False`) means "do not send it":

| Option                                | Header                                  | Default |
|---------------------------------------|-----------------------------------------|---------|
| `x_content_type_options`              | `X-Content-Type-Options`                | `"nosniff"` |
| `x_frame_options`                     | `X-Frame-Options`                       | `"SAMEORIGIN"` |
| `referrer_policy`                     | `Referrer-Policy`                       | `"strict-origin-when-cross-origin"` |
| `content_security_policy`             | `Content-Security-Policy`               | off |
| `content_security_policy_report_only` | `Content-Security-Policy-Report-Only`   | off |
| `permissions_policy`                  | `Permissions-Policy`                    | off |
| `cross_origin_opener_policy`          | `Cross-Origin-Opener-Policy`            | off |
| `cross_origin_resource_policy`        | `Cross-Origin-Resource-Policy`          | off |
| `cross_origin_embedder_policy`        | `Cross-Origin-Embedder-Policy`          | off |
| `hsts`                                | `Strict-Transport-Security` mode: `"auto"` (https only), `"always"`, `"never"` | `"auto"` |
| `hsts_max_age`                        | HSTS `max-age`, in seconds              | `31536000` |
| `hsts_include_subdomains`             | adds `includeSubDomains`                | `False` |
| `hsts_preload`                        | adds `preload`                          | `False` |
| `headers`                             | free `{name: value}` mapping, see below | `None` |
| `override`                            | replace a header the application already set | `False` |

```python
server = AsgiServer(
    applications=[App],
    middleware={"security_headers": {
        "content_security_policy": "default-src 'self'; frame-ancestors 'none'",
        "x_frame_options": "DENY",
        "permissions_policy": "camera=(), microphone=(), geolocation=()",
        "hsts_include_subdomains": True,
    }},
)
```

The same switch in a recipe:

```python
cfg.middleware(security_headers={"content_security_policy": "default-src 'self'"})
```

**The `headers` mapping** covers any header, named option or not. It is applied
after the named options, so it wins over them: a string sets or replaces a
header, `None`/`False`/`""` removes it. Names are case-insensitive.

```python
middleware={"security_headers": {"headers": {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": None,                 # drop a default
    "Strict-Transport-Security": "max-age=600",  # replaces the built HSTS value
}}}
```

**What the application set wins.** When a response already carries one of
these headers — a route that sends its own `Content-Security-Policy`, for
instance — the middleware leaves it alone. Pass `override=True` to replace the
application's value with the configured one instead.

**Behind a TLS-terminating proxy** the request reaches kajenn as plain `http`,
so `hsts="auto"` never fires. Use `hsts="always"` when every client reaches the
site over HTTPS (or have the ASGI server honour `X-Forwarded-Proto` so the
scheme is right), or let the proxy add HSTS itself.

Header names and values are validated when the server is built: an invalid
name, or a value containing CR/LF, raises `ValueError` at startup rather than
on a request. To turn the whole stage off: `middleware={"security_headers": False}`.

## Custom middleware

Register your own middleware class in the registry, then arm it like any built-in
stage. This fragment reuses `AsgiServer` and `App` from the setup above:

```python
from kajenn import BaseMiddleware


class StampMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        self.logger.info("Request path: %s", scope["path"])
        await self.app(scope, receive, send)


server = AsgiServer(
    applications=[App],
    middleware_registry={"stamp": StampMiddleware},
    middleware={"stamp": True},
)
```

- `middleware_registry={"stamp": StampMiddleware}` teaches the server the new
  name.
- `middleware={"stamp": True}` arms it with no custom options. Call the next app to continue dispatch.

The individual built-in middleware classes are importable from
`kajenn.middleware` (e.g. `from kajenn.middleware import
CORSMiddleware, SecurityHeadersMiddleware`), and `BaseMiddleware` / `MiddlewareMixin` from `kajenn`.

## How to verify it

With `cors` armed, a request carrying an `Origin` gets CORS headers back:

```console
$ curl -i -H "Origin: https://example.com" http://127.0.0.1:8000/index
HTTP/1.1 200 OK
access-control-allow-origin: https://example.com
```

Security headers are on every response out of the box:

```console
$ curl -sI http://127.0.0.1:8000/index | grep -iE "x-content|x-frame|referrer"
x-content-type-options: nosniff
x-frame-options: SAMEORIGIN
referrer-policy: strict-origin-when-cross-origin
```

With `logging` armed, requests appear in the server's log output.

## Gotchas

- The registry enables `security_headers` and `errors`; the shipped `AsgiServer`
  also arms `session`.
  CORS and logging require explicit configuration. There is no `auth` stage:
  `middleware={"auth": True}` raises `ValueError`.
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
- `X-Frame-Options: SAMEORIGIN` is on by default: a page meant to be framed by
  another origin needs `x_frame_options=None` (and a CSP `frame-ancestors`
  naming that origin).
- WebSocket scopes do not cross the chain, so the handshake response carries no
  security headers.
- Session is already armed on `AsgiServer`; configure its middleware options
  when needed, or disable it with `middleware={"session": False}`.
