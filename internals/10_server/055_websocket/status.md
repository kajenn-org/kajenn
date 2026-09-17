# Websocket — current state

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Verified against `2465fcc`. Historical phase order and test totals are archived
in [technical notes](tech_notes.md); they are not a current coverage measurement.
Opened at phase 0 of
[#68](https://github.com/genropy/genro-asgi/issues/68) on `develop` = `a434a23`;
all six phases of code have landed since — 1, 2, 3, 4a, 4 and 5. The socket is
no longer empty: a handshake reaches the motor, every message it carries is
served as a request of its user on his own row, a page opens its channel and is
bound to its socket, the site can write back to it, and an application that
wants the socket itself is handed it.

## Raw WebSocket application seam

**The admitted mode** — `BaseApplication` defines no `serve_websocket`, and an
application that defines one is handed the raw scope, receive and send by
`BaseServer.on_websocket` ([server.py](../../../src/kajenn/server.py)),
with its mount already off the path. Nothing else of the motor runs for it. 7
contract tests in `tests/core/test_websocket_raw_seam.py`, including the two that
draw the line: the raw application is never even named while the server is not
RUNNING, because the state is judged above the demux, and both modes are turned
away by that one refusal.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L86), [`on_websocket`](../../../src/kajenn/server.py#L365).

## Page channel binding and the openchannel seam

**The channel of a page** — `WsxConnection._call_application`
([wsx.py](../../../src/kajenn/wsx.py)) looks twice at exactly one message,
the one whose path is `OPENCHANNEL_PATH` under the `_wsx` root. The
application the path names decides whether that page may speak here. Only on
a 200 does the connection call `WebSocketRegistry.bind_page(page_id, socket)`.
A page that was refused is never bound at all.

**The registry is the whole binding** — `bind_page` and `get_page_socket` on
`WebSocketRegistry` ([websocket.py](../../../src/kajenn/websocket.py)), proven
by the 12 contract tests in `tests/core/test_websocket_registry.py`: a rebind
follows a reconnected page, and `unregister` drops only the pages bound to
THAT socket.

**The channel is the price of being addressed** — a message carries `page_id`
only when it belongs to a page, and the server writes back to a page only once
that page is bound. A message that names no page — the ordinary HTTP of the
site — is untouched.

**The request itself, for a handler that needs it** — the `_request` injection
moved from the `_server` app's own `bind_kwargs` into
`RoutedApplication.bind_kwargs`: the seam is nobody's private business, and the
channel command is its second reader.

**What the seam leaves open** — the core decides that a page is bound and to
which socket. Which process serves the message, and how a request is placed
among several, is left to whatever mounts an application above the core. The
core names no such extension and imports none.

Claim anchors: [`WsxConnection`](../../../src/kajenn/wsx.py#L206), [`_call_application`](../../../src/kajenn/wsx.py#L361), [`OPENCHANNEL_PATH`](../../../src/kajenn/wsx.py#L96), [`WebSocketRegistry`](../../../src/kajenn/websocket.py#L303), [`bind_page`](../../../src/kajenn/websocket.py#L335), [`get_page_socket`](../../../src/kajenn/websocket.py#L349), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L115), [`bind_kwargs`](../../../src/kajenn/routed_application.py#L298), [`websocket`](../../../src/kajenn/config/elements.py#L142).

## The synchronous pool behind a blocking call

**One pool, reached by one method** — `BaseServer.run_sync`
([server.py](../../../src/kajenn/server.py)) dispatches a blocking callable
onto the server's pool and awaits it. An application that must run blocking
code during a request calls `self.server.run_sync(...)`; an async handler
stays on the loop and never touches the pool. The core exposes no second pool
and no private executor.

**Why it is a method on the server** — a server that places requests
differently overrides `run_sync` and every application follows it without
changing a line. The base implementation makes that override optional.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L128), [`run_sync`](../../../src/kajenn/server.py#L274).

## Server-initiated page messages

**The server speaks first** — `BaseServer.send_message(page_id, path, data)`
([server.py](../../../src/kajenn/server.py)), 8 contract tests in
`tests/core/test_websocket_server_send.py`. It finds the socket that page speaks on
and writes one message with the shape of a request and NO `id`: not an answer,
and nobody answers it. `True` says it was written to the socket, `False` that
the page speaks on none or that its socket already closed — delivered means
written, never executed by the page. The name and the signature are the ones
an extension reuses when it sends from another process through the server that
owns the socket.

Reduced from the plan by the owner (2026-09-07, N28): the sending lives on the
server, which knows the protocol, and the registry stays a map. There is no
sending by identity or by connection, and the registry does not learn a
socket's identity, because nothing reads either yet.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L86), [`send_message`](../../../src/kajenn/server.py#L248).

## WSX handshake, registry and concurrency configuration

**The connection** — `WsxConnection` in
[wsx.py](../../../src/kajenn/wsx.py), covered in the historical phase run by
`tests/core/test_wsx_connection.py`. `serve()` is one socket's whole life: the gate,
the accept, the read loop, the bounded drain. The gate closes 1008 on a path no
application serves and on a missing home cookie, and REFUSES a hostile Origin
before the accept. It never sees a connection the machine had already refused:
the server's own state is judged above the demux, in `on_websocket`, for every
websocket alike. Identity is judged once — `server.authenticate` for the avatar,
`SessionMiddleware.get_session` for the session — and travels with every
message. Each message becomes a synthetic http scope (`method: "WSK"`, the
handshake's headers, `auth`, `session`, and `genro.page_id` /
`genro.reply_path` when the envelope carried them), routed by `server.demux`
straight to the application: never through `server()`, whose chain would run
once per message. An `HTTPException` becomes the answer's status, anything
else a 500, and the socket survives both.

**The reach into the chain** — `MiddlewareMixin.get_middleware`
([middleware/\_\_init\_\_.py](../../../src/kajenn/middleware/__init__.py))
walks the assembled chain from its head and hands back the layer of a class,
or `None` when that middleware is off; `BaseServer.get_middleware` is the base
answer, `None`, like `authenticate` and `session` beside it.
`SessionMiddleware.get_session(scope)`
([middleware/session.py](../../../src/kajenn/middleware/session.py)) is the
pure reading the handshake needs — the session the store holds for a scope's
cookie, creating nothing — and the middleware's own `__call__` now uses it.

**The registry** — `WebSocketRegistry` in
[websocket.py](../../../src/kajenn/websocket.py), reached as
`server.websockets`, 12 contract tests. `register` / `unregister` for the live
sockets, `bind_page` / `get_page_socket` for the association `openchannel`
writes. A rebind follows a reconnected page; `unregister` drops only the
pages still bound to THAT socket.

**The refusal** — `WebSocket.refuse(code, reason)`: consumes the connect and
closes with no accept. Both the Origin gate and the server state gate use refusal before acceptance.

**The config** — `server/websocket` with `origins` (comma-separated in a
recipe, a list on the server) and `max_concurrent`
([config/elements.py](../../../src/kajenn/config/elements.py),
[config/handler.py](../../../src/kajenn/config/handler.py)). The ceiling
defaults to 16 (`WEBSOCKET_MAX_CONCURRENT` in
[server.py](../../../src/kajenn/server.py)).

Claim anchors: [`WsxConnection`](../../../src/kajenn/wsx.py#L193), [`refuse`](../../../src/kajenn/websocket.py#L162), [`demux`](../../../src/kajenn/server.py#L325), [`on_websocket`](../../../src/kajenn/server.py#L365), [`websocket`](../../../src/kajenn/config/elements.py#L142), [`authenticate`](../../../src/kajenn/server.py#L231), [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#L55), [`get_session`](../../../src/kajenn/middleware/session.py#L80), [`HTTPException`](../../../src/kajenn/exceptions.py#L48), [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#L80).

## WebSocket facade and WSX envelope

**The facade** — [websocket.py](../../../src/kajenn/websocket.py), 100%
covered by `tests/core/test_websocket_facade.py` (27 contract tests). `WebSocket`
wraps scope, `receive` and `send`: `accept()` consumes the connect and answers
it (with a subprotocol and with response headers, the one place a websocket can
carry a `Set-Cookie`), `close()` writes once and refuses to run before an
accept, the reads refuse the wrong payload kind and raise
`WebSocketDisconnect` when the client is gone, the writes refuse a socket
nobody accepted, and iterating yields the incoming texts until that disconnect.
`connected` is the whole state, one boolean, and the handshake facts — path,
headers, cookies, offered subprotocols — are read off the scope in the
constructor, the way `Request` reads an HTTP request.

**The envelope** — [wsx.py](../../../src/kajenn/wsx.py), covered in the historical phase run by
`tests/core/test_wsx_envelope.py` (29 contract tests). `WsxEnvelope(text)` reads a
message, `WsxEnvelope(id=…, method=…, path=…, data=…)` builds one, and
`encode()` gives the wire text. A field nobody set does not reach the wire, so
an event has no `id` and a request has no `status`. `data` is a Python value
here and the TYTX string inside the JSON body there: the round-trip tests are
executable examples over Decimal, date, datetime, null, bytes, a Bag with
attributes, a nested Bag, and a string full of characters JSON must escape. A
text without the prefix, a body that is not JSON and a body that is not an
object all raise `ValueError` — one answer for the read loop: this is not a
message of ours.

**The disconnect** — `WebSocketDisconnect` in
[exceptions.py](../../../src/kajenn/exceptions.py), with `code` and
`reason`. It sits beside the HTTP exceptions and is not one: a disconnect is
not a value a read can return, so it arrives as an exception.

**The dependency.** genro-tytx is pinned `>=0.14.0`
([pyproject.toml:34, 50](../../../pyproject.toml)), the release that carries
the `RAW` type: bytes in a message travel base64 under `::RAW` on JSON and
native on msgpack, and the page encodes nothing by hand.

Claim anchors: [`WebSocket`](../../../src/kajenn/websocket.py#L65), [`accept`](../../../src/kajenn/websocket.py#L129), [`WebSocketDisconnect`](../../../src/kajenn/exceptions.py#L122), [`connected`](../../../src/kajenn/websocket.py#L105), [`WsxEnvelope`](../../../src/kajenn/wsx.py#L101), [`encode`](../../../src/kajenn/wsx.py#L172).

## HTTP middleware and transport boundaries

**The empty socket, until phase 2.** `BaseServer.on_websocket` consumed the
connect and closed with code 1000 — the D7 socket, whose docstring said the
motor of Q1 would override this hook. It does now. The `state` the http branch
renders as a 503 with `Retry-After` is read here too, at the top of
`on_websocket`: each transport judges the same fact and renders its own
refusal, and this one turns the handshake away before the accept.

The test that drove it (`tests/core/test_demux.py`, `TestEmptyWebsocket`) became
`TestTheWebsocketBranch`, asserting only that `__call__` hands the scope to the
motor; what the motor does is `tests/core/test_wsx_connection.py`'s subject.

**The middleware chain does not see it.** `MiddlewareMixin.__call__`
([middleware/\_\_init\_\_.py:107-112](../../../src/kajenn/middleware/__init__.py))
routes only `http` scopes through the chain; every other scope passes straight
through. So at the handshake `scope["auth"]` and `scope["session"]` are NOT
already there — which is why the handshake resolves the identity itself
([decisions.md](decisions.md) §5).

**An application receives synthetic HTTP scopes.** The connection motor builds
one HTTP scope per message and hands it to `server.demux`, so an application
that demultiplexes on the PATH never reads the scope's type and serves a WSX
message exactly as it serves a request. The raw handshake scope stays with the
server's WebSocket entry point and reaches no application.

**The pieces used by the motor.** The demux
(`server.py:247-273`), the request registry (`server.py:95`, registered in the
HTTP cycle at `:225-240`), the identity (`auth/core.py:160-179`,
`auth/mixin.py:151-163`), the session from the cookie
(`middleware/session.py:76-78, 118-127`), the routing tree with its filtered
walk (`routed_application.py:173-218`), and the lane's own envelope, which
already speaks WSX with the same four fields (`channel/frame.py:15-23,
94-100`).

Claim anchors: [`middleware`](../../../src/kajenn/config/elements.py#L167), [`BaseServer`](../../../src/kajenn/server.py#L86), [`on_websocket`](../../../src/kajenn/server.py#L365), [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#L80).

## The handshake cookie an application may require

**The gate an application arms.** `BaseApplication.handshake_cookie`
([application.py](../../../src/kajenn/application.py)) returns `None`: no
cookie is demanded and every handshake passes. An application that returns a
cookie name has the handshake accepted and then closed 1008 when that cookie
is absent — `WsxConnection._open_gate` reads the property off the application
the path resolves to (#70 / PR #71).

**Why the core owns the refusal and not the name.** An application that keeps
per-connection state elsewhere must be able to refuse a socket that carries no
identity of its own, before any message is served. The core supplies the gate
and the close code; the cookie's name and its meaning belong to whoever
returns it.

`SPECIFICATION.md` §6 Q1 is marked RESOLVED as of this phase: the design it
asked for is [decisions.md](decisions.md) and [design.md](design.md), and the
code follows in phases 1 to 5.

Claim anchors: [`BaseApplication`](../../../src/kajenn/application.py#L108), [`handshake_cookie`](../../../src/kajenn/application.py#L199), [`WsxConnection`](../../../src/kajenn/wsx.py#L206), [`_open_gate`](../../../src/kajenn/wsx.py#L244).
