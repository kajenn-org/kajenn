# Middleware — current state

**Version**: 0.5 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Chain assembly and effective defaults

`MiddlewareMixin` builds the chain once from its registry and switches. Lowest
`middleware_order` is outermost. Unknown names and unconsumed constructor options
raise `ValueError` and `TypeError` respectively. `get_middleware(cls)` reads an
installed layer, or returns `None`.

| Layer | Order | Class default |
|---|---:|---|
| ErrorMiddleware | 100 | enabled |
| LoggingMiddleware | 200 | disabled |
| CORSMiddleware | 300 | disabled |
| SessionMiddleware | 400 | disabled |

`default_registry()` carries these four names: `errors`, `logging`, `cors`,
`session`. `AuthMiddleware` does not exist: it was removed in 0.4.0 (commit
`05411ad`). `SessionMixin` is the only mixin that calls `setdefault` on the
switches: it enables `session`, and an explicit `False` wins. Therefore a bare
composition has errors and session.

The recipe element `middleware` declares the four names and takes `**extra`.
A middleware merged through `middleware_registry=` is switched by its own name
through `**extra`.

Claim anchors: [`default_registry`](../../../src/kajenn/middleware/__init__.py#default_registry), [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#MiddlewareMixin), [`get_middleware`](../../../src/kajenn/middleware/__init__.py#MiddlewareMixin.get_middleware), [`SessionMixin` setdefault](../../../src/kajenn/session/mixin.py#SessionMixin.__init__), [`middleware` element](../../../src/kajenn/config/elements.py#AsgiServerGrammar.middleware).

## Request identity outside the chain

No middleware layer sets `scope["auth"]`. `RoutedApplication.authenticate_scope`
writes `server.authenticate(scope)` to `scope["auth"]` when the key is missing
or `None`. `execute` and `discover` call it before resolution.

Claim anchors: [`authenticate_scope`](../../../src/kajenn/routed_application.py#RoutedApplication.authenticate_scope).

## HTTP errors

Only HTTP enters the middleware chain. `ErrorMiddleware` records whether a
response started; an error after start is re-raised rather than answered twice.
Before start, an `HTTPException` retains its status and headers, a `Redirect`
retains its Location, and other exceptions become a generic 500.

The generic 500 is logged, except for an `ExternalFailure`. An
`ExternalFailure` becomes a 500 with no log line from this layer
(errors.py:104-105).

A 401 is answered like any other error, the same one to a browser and to an
API caller: the bare status with the exception's header pairs forwarded onto
it, `WWW-Authenticate` included (D-SA-4). The negotiation that turned a browser
401 into a 302 to a login page, and an API one into a `login_url` body, is
gone, and so is the page it pointed at (D-SA-3). Error bodies follow Accept
negotiation. `Response.ERROR_MAP` is a standalone helper table, not the
middleware policy.

The error middleware writes through its own outer `send`. Thus an error
response bypasses inner CORS/session response wrappers and may lack their
headers. Disabling errors is accepted and allows exceptions to escape the
composition. Those limitations are not resolved by this documentation update.

Claim anchors: [`ErrorMiddleware`](../../../src/kajenn/middleware/errors.py#ErrorMiddleware).

Behavior evidence: [`_error_response`](../../../src/kajenn/middleware/errors.py#ErrorMiddleware._error_response), [`_forward_headers`](../../../src/kajenn/middleware/errors.py#ErrorMiddleware._forward_headers).

## Sessions, CORS and logging

`SessionMiddleware` reconnects or creates a session and writes back only dirty
sessions. It sets the cookie only for a new session, with `Max-Age = ttl * 24`.
Shared header and cookie readers live in `base.py`.

`SessionMiddleware` takes three options: `cookie_name` (default `session_id`),
`secure` (default `False`) and `samesite` (default `lax`). The write-back runs
after the inner app returns. When the inner app raises, the write-back is
skipped (session.py:136-153).

CORS handles preflight and response headers. `allow_origins` and
`allow_headers` default to `["*"]` (cors.py:62, 66). Logging records request
outcome and duration; its `level` selects the emitted severity, and an unknown
level falls back to INFO. `include_headers` (default `False`) logs the request
headers at DEBUG. `include_query` (default `True`) appends the query string to
the logged request line.

Claim anchors: [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#SessionMiddleware).

Behavior evidence: [`CORSMiddleware`](../../../src/kajenn/middleware/cors.py#CORSMiddleware), [`LoggingMiddleware`](../../../src/kajenn/middleware/logging.py#LoggingMiddleware).

## WSX handshake boundary

WebSocket and lifespan scopes bypass the chain. `WsxConnection` checks Origin
and obtains handshake identity/session directly, using `get_middleware` and
`SessionMiddleware.get_session` as read helpers. A raw `serve_websocket`
application owns its Origin/authentication policy after the server's state gate.
The absence of a WebSocket middleware pass is not the absence of an Origin gate.

Claim anchors: [`WsxConnection`](../../../src/kajenn/wsx.py#WsxConnection), [`get_middleware`](../../../src/kajenn/middleware/__init__.py#MiddlewareMixin.get_middleware), [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#SessionMiddleware), [`get_session`](../../../src/kajenn/middleware/session.py#SessionMiddleware.get_session).

## Source and test evidence

- [src/kajenn/middleware/__init__.py](../../../src/kajenn/middleware/__init__.py)
- [src/kajenn/middleware/base.py](../../../src/kajenn/middleware/base.py)
- [src/kajenn/middleware/errors.py](../../../src/kajenn/middleware/errors.py)
- [src/kajenn/middleware/cors.py](../../../src/kajenn/middleware/cors.py)
- [src/kajenn/middleware/session.py](../../../src/kajenn/middleware/session.py)
- [src/kajenn/middleware/logging.py](../../../src/kajenn/middleware/logging.py)
- [src/kajenn/wsx.py](../../../src/kajenn/wsx.py)
- [tests/core/test_middleware.py](../../../tests/core/test_middleware.py)
- [tests/core/test_middleware_std.py](../../../tests/core/test_middleware_std.py)
- [tests/server_app/test_login_flow.py](../../../tests/server_app/test_login_flow.py)
- [tests/core/test_wsx_connection.py](../../../tests/core/test_wsx_connection.py)
