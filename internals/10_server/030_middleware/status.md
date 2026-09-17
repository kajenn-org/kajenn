# Middleware — current state

**Version**: 0.4 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
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
| AuthMiddleware | 450 | disabled |

`AsgiServer` nevertheless enables session and auth through their capability
mixins' `setdefault`; an explicit `False` wins. Therefore a bare composition
normally has errors, session and auth. Extra registry classes are Python
constructor options; the recipe middleware element names only the five built-ins.

Claim anchors: [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#L80), [`get_middleware`](../../../src/kajenn/middleware/__init__.py#L103).

## HTTP errors

Only HTTP enters the middleware chain. `ErrorMiddleware` records whether a
response started; an error after start is re-raised rather than answered twice.
Before start, an `HTTPException` retains its status and headers, a `Redirect`
retains its Location, and other exceptions become a generic logged 500.

A 401 is answered like any other error, the same one to a browser and to an
API caller: the bare status with the exception's `WWW-Authenticate` forwarded
onto it (D-SA-4). The negotiation that turned a browser 401 into a 302 to a
login page, and an API one into a `login_url` body, is gone, and so is the page
it pointed at (D-SA-3). Error bodies follow Accept negotiation.
`Response.ERROR_MAP` is a standalone helper table, not the middleware policy.

The error middleware writes through its own outer `send`. Thus an error
response bypasses inner CORS/session response wrappers and may lack their
headers. Disabling errors is accepted and allows exceptions to escape the
composition. Those limitations are not resolved by this documentation update.

Claim anchors: [`ErrorMiddleware`](../../../src/kajenn/middleware/errors.py#L60).

Behavior evidence: [`_error_response`](../../../src/kajenn/middleware/errors.py#L87).

## Sessions, authentication, CORS and logging

`SessionMiddleware` reconnects or creates a session and writes back only dirty
sessions. It sets the cookie only for a new session, with `Max-Age = ttl * 24`.
`AuthMiddleware` writes `server.authenticate(scope)` to `scope['auth']` after
the session layer has run. Shared header and cookie readers live in `base.py`.

CORS handles preflight and response headers. Wellknown raises 404 for its
reserved paths. Logging records request outcome and duration; its `level`
selects the emitted severity, and an unknown level falls back to INFO.

Claim anchors: [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#L55), [`AuthMiddleware`](../../../src/kajenn/middleware/authentication.py#L39).

Behavior evidence: [`CORSMiddleware`](../../../src/kajenn/middleware/cors.py#L43), [`LoggingMiddleware`](../../../src/kajenn/middleware/logging.py#L37).

## WSX handshake boundary

WebSocket and lifespan scopes bypass the chain. `WsxConnection` checks Origin
and obtains handshake identity/session directly, using `get_middleware` and
`SessionMiddleware.get_session` as read helpers. A raw `serve_websocket`
application owns its Origin/authentication policy after the server's state gate.
The absence of a WebSocket middleware pass is not the absence of an Origin gate.

Claim anchors: [`WsxConnection`](../../../src/kajenn/wsx.py#L193), [`get_middleware`](../../../src/kajenn/middleware/__init__.py#L103), [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#L55), [`get_session`](../../../src/kajenn/middleware/session.py#L80).

## Source and test evidence

- [src/kajenn/middleware/__init__.py](../../../src/kajenn/middleware/__init__.py)
- [src/kajenn/middleware/base.py](../../../src/kajenn/middleware/base.py)
- [src/kajenn/middleware/errors.py](../../../src/kajenn/middleware/errors.py)
- [src/kajenn/middleware/cors.py](../../../src/kajenn/middleware/cors.py)
- [src/kajenn/middleware/session.py](../../../src/kajenn/middleware/session.py)
- [src/kajenn/middleware/authentication.py](../../../src/kajenn/middleware/authentication.py)
- [src/kajenn/middleware/logging.py](../../../src/kajenn/middleware/logging.py)
- [src/kajenn/wsx.py](../../../src/kajenn/wsx.py)
- [tests/core/test_middleware.py](../../../tests/core/test_middleware.py)
- [tests/core/test_middleware_std.py](../../../tests/core/test_middleware_std.py)
- [tests/server_app/test_login_flow.py](../../../tests/server_app/test_login_flow.py)
- [tests/core/test_wsx_connection.py](../../../tests/core/test_wsx_connection.py)
