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

## Page channel binding, worker dispatch and sequential calls

**The channel of a page** — `WsxControl` under the front's `_wsx` root
([spa_app.py](../../../src/kajenn_orchestra/spa_app.py)),
`SpaCommander.serve_wsx_request`
([spa_commander.py](../../../src/kajenn_orchestra/orchestration/spa_commander.py)),
`SpaWorker.serve_wsx` and the `WsxCommands` branch
([spa_worker.py](../../../src/kajenn_orchestra/orchestration/spa_worker.py)),
14 contract tests in
`tests/spa/orchestration/test_orchestration_websocket_e2e.py` that enter where a
real message enters — the socket — over a real pool. `openchannel` is
validated by the front against `page_connection_map`, written on the page's row
by the worker through the same prologue a request goes through, and bound to
the socket by the CONNECTION, only on a 200.

**One resolution for every form** — `SpaCommander.resolve_worker` is what
`serve_request` and `serve_wsx_request` both call: the barrier, the
reception-first rule, the placement.

**The queue of a page** — `call_lock` on `PageRow`
([register_row.py](../../../src/kajenn_orchestra/register_row.py)), an
`asyncio.Lock` among the fields the parcel leaves behind, taken around the
whole call when the page opened its channel with `sequential`. The CHANNEL
itself travels: a user parked for being idle and woken by his next request
never lost his websocket, so a row that came back without `wsx` would refuse
the very next message of a page that is still connected.

**The way back** — `SpaWorker.send_message`, the `websocket` branch the front
attaches under `CommanderOperations`, and `BaseServer.send_message` at the end
of it. A page the vertex no longer knows is reachable by nobody, because the
branch validates before it writes.

**The channel is the price of being addressed** — a call that names a page is
refused unless that page opened its channel: `openchannel` is what makes a page
addressable, and a message that skips it is a client out of step with its own
row. A request that names no page — the ordinary HTTP of the site — is
untouched.

**The request itself, for a handler that needs it** — the `_request` injection
moved from the `_server` app's own `bind_kwargs` into
`RoutedApplication.bind_kwargs`: the seam is nobody's private business, and
`openchannel` is its second reader.

Claim anchors: [`WsxControl`](../../../src/kajenn_orchestra/spa_app.py#L454), [`SpaCommander`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L494), [`serve_wsx_request`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L726), [`serve_wsx`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L1416), [`SpaWorker`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L564), [`WsxCommands`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L492), [`websocket`](../../../src/kajenn/config/elements.py#L142), [`openchannel`](../../../src/kajenn_orchestra/spa_app.py#L471), [`openchannel`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L507), [`resolve_worker`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L753).

## Hosted ASGI and WSGI worker seams

**One seam on the worker** — `SpaWorker.asgi_app`, and the property
`hosted_app_seam`
([spa_worker.py](../../../src/kajenn_orchestra/orchestration/spa_worker.py)),
which is the one road out of `_serve_request`: `AsgiSeam` on the assigned
application, or `AsgiSeam(WsgiSeam(wsgi_app, worker))` when the consumer took
the shortcut. Both assigned is a contradiction, and `WorkerEntry` kills the
process at boot; NEITHER is the base worker, which
[worker_entry.py](../../../src/kajenn_orchestra/orchestration/worker_entry.py)
declares legitimate — it serves its orders, and an http CALL is refused with
the property's message (owner, 2026-09-07, N29).

**The two seams** — [environ.py](../../../src/kajenn_orchestra/environ.py), verified by
`tests/spa/orchestration/test_orchestration_asgi_seam.py`. `AsgiSeam` turns the
`http` dict into an ASGI scope and calls the application as a server would;
`WsgiSeam` is an ASGI application around a WSGI callable, its dict entrance
gone with its two readers. `SCRIPT_NAME` is `root_path` and `PATH_INFO` what is
left of `path`; `Set-Cookie` and `Location` travel like any other header; the
callable runs through `SpaWorker.run_sync`, which is the traffic pool with this
CALL's slot following onto the thread.

**What did not change**: the whole existing rig passes untouched. The bridge
assigns `wsgi_app` and knows nothing of the adapter it now goes through.

Claim anchors: [`SpaWorker`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L564), [`hosted_app_seam`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L689), [`_serve_request`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L2222), [`AsgiSeam`](../../../src/kajenn_orchestra/environ.py#L71), [`WsgiSeam`](../../../src/kajenn_orchestra/environ.py#L174), [`WorkerEntry`](../../../src/kajenn_orchestra/orchestration/worker_entry.py#L90), [`run_sync`](../../../src/kajenn/server.py#L222), [`run_sync`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L719).

## Server-initiated page messages

**The server speaks first** — `BaseServer.send_message(page_id, path, data)`
([server.py](../../../src/kajenn/server.py)), 8 contract tests in
`tests/core/test_websocket_server_send.py`. It finds the socket that page speaks on
and writes one message with the shape of a request and NO `id`: not an answer,
and nobody answers it. `True` says it was written to the socket, `False` that
the page speaks on none or that its socket already closed — delivered means
written, never executed by the page. The name and the signature are
`SpaWorker.send_message`'s, which calls it from a worker through the commander.

Reduced from the plan by the owner (2026-09-07, N28): the sending lives on the
server, which knows the protocol, and the registry stays a map. There is no
sending by identity or by connection, and the registry does not learn a
socket's identity, because nothing reads either yet.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L86), [`send_message`](../../../src/kajenn/server.py#L248), [`send_message`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L1511), [`SpaWorker`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L564).

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

**The front receives synthetic HTTP scopes.** `SpaApplication.__call__`
([spa_app.py:837-844](../../../src/kajenn_orchestra/spa_app.py))
demultiplexes between its own router and the hosted site on the PATH, and never
reads the scope's type. It receives WSX messages as synthetic HTTP scopes from the connection motor.
The raw handshake scope stays with the server's WebSocket entry point.

**The pieces used by the motor.** The demux
(`server.py:247-273`), the request registry (`server.py:95`, registered in the
HTTP cycle at `:225-240`), the identity (`auth/core.py:160-179`,
`auth/mixin.py:151-163`), the session from the cookie
(`middleware/session.py:76-78, 118-127`), the routing tree with its filtered
walk (`routed_application.py:173-218`), and the lane's own envelope, which
already speaks WSX with the same four fields (`channel/frame.py:15-23,
94-100`).

Claim anchors: [`middleware`](../../../src/kajenn/config/elements.py#L167), [`BaseServer`](../../../src/kajenn/server.py#L86), [`on_websocket`](../../../src/kajenn/server.py#L365), [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#L80), [`SpaApplication`](../../../src/kajenn_orchestra/spa_app.py#L517).

## SPA cookie gate, worker refusals and buffered protocol

**The SPA requires its connection cookie.** `BaseApplication.handshake_cookie`
returns `None`; `SpaApplication.handshake_cookie` overrides it with
`spa_connection_id` (#70 / PR #71). A missing cookie is accepted then closed
1008. `SpaApplication.gateway_response` also preserves explicit worker refusal
status and text, including the 409 for a page that skipped `openchannel`.
Evidence: `tests/spa/test_spa_application.py` and
`tests/spa/orchestration/test_orchestration_websocket_e2e.py`.

**The worker protocol is still the buffered baseline.** `pack_http` uses a
JSON-safe dict with base64 body, and `AsgiSeam` collects the response. Issue #72
has not changed that protocol in this revision.

`SPECIFICATION.md` §6 Q1 is marked RESOLVED as of this phase: the design it
asked for is [decisions.md](decisions.md) and [design.md](design.md), and the
code follows in phases 1 to 5.

Claim anchors: [`handshake_cookie`](../../../src/kajenn_orchestra/spa_app.py#L524), [`SpaApplication`](../../../src/kajenn_orchestra/spa_app.py#L517), [`gateway_response`](../../../src/kajenn_orchestra/spa_app.py#L1096), [`openchannel`](../../../src/kajenn_orchestra/spa_app.py#L471), [`openchannel`](../../../src/kajenn_orchestra/orchestration/spa_worker.py#L507), [`pack_http`](../../../src/kajenn_orchestra/spa_app.py#L1163), [`AsgiSeam`](../../../src/kajenn_orchestra/environ.py#L71).
