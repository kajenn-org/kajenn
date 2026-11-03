# Websocket — current state

**Version**: 0.4 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Verified against `63fb326`. Historical phase order and test totals are archived
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

**The raw seam across processes** — an application declared with `spawner=` is
mounted as `RemoteApplication`
([remote_application.py:139](../../../src/kajenn/remote_application.py#L139)).
When the real class defines `serve_websocket`, the mount sets its own
`serve_websocket` to `_forward_websocket` (remote_application.py:168-169). That
method opens one kbus stream to the member named after the application code and
carries every ASGI websocket event across it, in both directions
(remote_application.py:317-363). In the spawned process `serve_kbus_call` sends a
stream whose `meta["format"]` is `websocket` to `_serve_websocket_stream`
(kbus_mixin.py:324-325), which runs the demuxed application's `serve_websocket`
on that stream and hands it `websocket.disconnect` with code 1006 when the
stream ends or fails (kbus_mixin.py:565-594). kbus is the kajenn-kbus library
(`import kbus`, pyproject.toml:41). Tests: `tests/core/test_external_application_websocket.py`.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L141), [`on_websocket`](../../../src/kajenn/server.py#L607), [`RemoteApplication`](../../../src/kajenn/remote_application.py#L139), [`_forward_websocket`](../../../src/kajenn/remote_application.py#L317), [`_serve_websocket_stream`](../../../src/kajenn/kbus_mixin.py#L565).

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

Claim anchors: [`WsxConnection`](../../../src/kajenn/wsx.py#L212), [`_call_application`](../../../src/kajenn/wsx.py#L370), [`OPENCHANNEL_PATH`](../../../src/kajenn/wsx.py#L97), [`WebSocketRegistry`](../../../src/kajenn/websocket.py#L301), [`bind_page`](../../../src/kajenn/websocket.py#L333), [`get_page_socket`](../../../src/kajenn/websocket.py#L347), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L127), [`bind_kwargs`](../../../src/kajenn/routed_application.py#L389), [`websocket`](../../../src/kajenn/config/elements.py#L175).

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

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L141), [`run_sync`](../../../src/kajenn/server.py#L398).

## Server-initiated page messages

**The server speaks first** — `BaseServer.send_message(page_id, path, data)`
([server.py](../../../src/kajenn/server.py)), 8 contract tests in
`tests/core/test_websocket_server_send.py`. It finds the socket that page speaks on
and writes one message with the shape of a request and NO `id`: not an answer,
and nobody answers it. `True` says it was written to the socket, `False` that
the page speaks on none or that its socket already closed — delivered means
written, never executed by the page. The method is reachable only in the
process that holds the socket: no kbus route calls `send_message`, and
`send_message` has no caller in `src/kajenn`.

**The serialized variant** — `BaseServer.send_serialized_message(page_id, path,
payload)` (server.py:471-480) writes the same message from a
`SerializedWsxPayload` that is already TYTX text, without serializing it again.
It has no caller in `src/kajenn` and no test in `tests/`.

Reduced from the plan by the owner (2026-09-07, N28): the sending lives on the
server, which knows the protocol, and the registry stays a map. There is no
sending by identity or by connection, and the registry does not learn a
socket's identity, because nothing reads either yet.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L141), [`send_message`](../../../src/kajenn/server.py#L445), [`send_serialized_message`](../../../src/kajenn/server.py#L471).

## WSX handshake, registry and concurrency configuration

**The connection** — `WsxConnection` in
[wsx.py](../../../src/kajenn/wsx.py), covered in the historical phase run by
`tests/core/test_wsx_connection.py` (36 tests). `serve()` is one socket's whole life: the gate,
the accept, the read loop, the bounded drain. It never sees a connection the
machine had already refused: the server's own state is judged above the demux,
in `on_websocket`, for every websocket alike.

**The gate order** — `_open_gate` (wsx.py:250-276) runs five steps in this
order:

- the Origin, refused before the accept (`refuse(1008, ...)`);
- the accept;
- the home application through `server.demux`: no `BaseApplication` at the
  path closes 1008 «no application at this path»;
- the home application's `handshake_cookie`: a missing cookie closes 1008
  «connection cookie required: <name>»;
- `scope["kajenn.channel"] = "wsx"` on the handshake scope, then
  `server.authenticate`: an `HTTPException` closes 1008 with its detail.

The session is read after the authentication, with
`SessionMiddleware.get_session` (wsx.py:275, 299-307).

**The identity of a message** — the handshake scope carries no `session` key,
because the middleware chain never sees a websocket scope. So
`server.authenticate` at the handshake resolves an `Authorization` header and
nothing else: `AuthMixin.authenticate` reads the session through
`self.session(scope)`, which is `scope.get("session")`
(auth/mixin.py:225-243, session/mixin.py:108-110). The avatar of the handshake
and the session read after it travel on every message scope. A message whose
`auth` is `None` reaching a `RoutedApplication` is authenticated again by
`authenticate_scope` on the message scope, which carries the session
(routed_application.py:284-298). So a session avatar is resolved per message,
and a header avatar once per connection.

**The synthetic scope** — each message becomes an http scope built by
`_request_scope` (wsx.py:409-434): `method: "WSK"`, the envelope's path, the
handshake's headers, `auth`, `session`, `kajenn.channel: "wsx"`, and
`genro.page_id` / `genro.reply_path` when the envelope carried them. The
handshake's `content-type`, `content-length` and `x-tytx-transport` headers
are dropped and replaced by `content-type: application/json` and
`x-tytx-transport: json` (wsx.py:423-425). The scope is routed by
`server.demux` straight to the application: never through `server()`, whose
chain would run once per message. An `HTTPException` becomes the answer's
status, anything else a 500, and the socket survives both. An
`ExternalFailure` — the exception an application raised in its spawned
process — is answered 500 with its original text and is not logged again
(wsx.py:391-394, exceptions.py:142-148).

**Every message is a registered request** — `_serve_message` registers every
message in `server.requests`, with or without `id`, runs its cleanups and
unregisters it (wsx.py:348-368). Only a message with an `id` is answered. Test:
`test_an_event_is_a_registered_request_too`.

**The read loop** — `_read_messages` (wsx.py:309-338) drops a binary frame and
a text that is not a WSX message with a warning, and the socket lives on
(`test_a_binary_frame_is_ignored_and_the_socket_lives_on`). A message on
`/_wsx/ping` (`PING_PATH`, wsx.py:90) is answered `pong` inline, outside the
`max_concurrent` ceiling, and reaches no application
(`test_the_ping_is_answered_by_the_server_itself`,
`test_it_does_not_reach_any_application`). Every other message runs on a task
of its own.

**The drain** — when the read loop ends, `_drain` waits at most
`DRAIN_TIMEOUT_SECONDS` (5.0, wsx.py:100) for the tasks still in flight, then
cancels the rest (wsx.py:340-346). Test:
`test_what_is_still_in_flight_when_the_client_leaves_is_cut`.

**The answer** — `_answer_of` (wsx.py:455-481) reads what the application sent.
For an application in this process it hands the body to `WsxResponseEncoder`
(wsx_payload.py:41-73): a json body passes through as text; xml and msgpack
are hydrated and re-serialized to TYTX json; any other body is taken as utf-8
text, or as bytes when it does not decode. For a mount whose
`forwards_payloads` is `True` — `RemoteApplication`, remote_application.py:145 —
the json body travels on as it arrived, and a body that is not json raises
`ValueError`, answered 500.

**WSX to a spawned application** — a WSX message whose path names a
`RemoteApplication` is an http scope like any other: `RemoteApplication.__call__`
forwards it on a kbus stream with an `HttpRecord` of the request
(remote_application.py:171-199). Tests: `test_a_wsx_message_reaches_the_spawned_application`
(`tests/core/test_external_application.py`), `test_a_wsx_message` and
`test_a_wsx_message_to_a_raising_route` (`tests/core/test_external_parity.py`).

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

Claim anchors: [`WsxConnection`](../../../src/kajenn/wsx.py#L212), [`_open_gate`](../../../src/kajenn/wsx.py#L250), [`_read_messages`](../../../src/kajenn/wsx.py#L309), [`_serve_message`](../../../src/kajenn/wsx.py#L348), [`_request_scope`](../../../src/kajenn/wsx.py#L409), [`WsxResponseEncoder`](../../../src/kajenn/wsx_payload.py#L41), [`ExternalFailure`](../../../src/kajenn/exceptions.py#L142), [`refuse`](../../../src/kajenn/websocket.py#L160), [`demux`](../../../src/kajenn/server.py#L535), [`on_websocket`](../../../src/kajenn/server.py#L607), [`websocket`](../../../src/kajenn/config/elements.py#L175), [`authenticate`](../../../src/kajenn/server.py#L428), [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#L54), [`get_session`](../../../src/kajenn/middleware/session.py#L79), [`HTTPException`](../../../src/kajenn/exceptions.py#L59), [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#L73).

## WebSocket facade and WSX envelope

**The facade** — [websocket.py](../../../src/kajenn/websocket.py); every line
of the `WebSocket` class is covered by `tests/core/test_websocket_facade.py`
(31 contract tests) alone. `WebSocket`
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
`tests/core/test_wsx_envelope.py` (30 contract tests). `WsxEnvelope(text)` reads a
message, `WsxEnvelope(id=…, method=…, path=…, data=…)` builds one, and
`encode()` gives the wire text. A field nobody set does not reach the wire, so
an event has no `id` and a request has no `status`. A read envelope keeps its
payload serialized: `serialized_data` is a `SerializedWsxPayload` holding the
TYTX string (wsx.py:148, 156-158; wsx_payload.py:20-38). `data` is an opt-in
decoder that hydrates that string when a consumer reads it (wsx.py:163-166);
the routing code reads `serialized_data`. A value passed as `data=` to the
constructor is serialized to TYTX json there. The round-trip tests are
executable examples over Decimal, date, datetime, null, bytes, a Bag with
attributes, a nested Bag, and a string full of characters JSON must escape. A
text without the prefix, a body that is not JSON and a body that is not an
object all raise `ValueError` — one answer for the read loop: this is not a
message of ours.

**The disconnect** — `WebSocketDisconnect` in
[exceptions.py](../../../src/kajenn/exceptions.py), with `code` and
`reason`. It sits beside the HTTP exceptions and is not one: a disconnect is
not a value a read can return, so it arrives as an exception.

**The dependency.** genro-tytx is pinned `>=0.16.4`
([pyproject.toml:39, 60](../../../pyproject.toml)). Bytes in a message travel
base64 inside the JSON body (`test_bytes_travel_base64_inside_the_json_body`),
and the page encodes nothing by hand.

Claim anchors: [`WebSocket`](../../../src/kajenn/websocket.py#L63), [`accept`](../../../src/kajenn/websocket.py#L127), [`WebSocketDisconnect`](../../../src/kajenn/exceptions.py#L178), [`connected`](../../../src/kajenn/websocket.py#L103), [`WsxEnvelope`](../../../src/kajenn/wsx.py#L103), [`data`](../../../src/kajenn/wsx.py#L164), [`encode`](../../../src/kajenn/wsx.py#L191), [`SerializedWsxPayload`](../../../src/kajenn/wsx_payload.py#L20).

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
([middleware/\_\_init\_\_.py:123-128](../../../src/kajenn/middleware/__init__.py))
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
(`server.py:535-573`), the request registry (`server.py:182`, registered in the
HTTP cycle at `:516-524`), the identity (`auth/core.py:160-171`,
`auth/mixin.py:225-243`), the session from the cookie
(`middleware/session.py:75-92, 125-140`), and the routing tree with its filtered
resolution (`routed_application.py:260-265, 315-330`). The bus between
processes is the kajenn-kbus library (`import kbus`); the code of `src/kajenn`
that carries a WSX message or a raw websocket over it is
`RemoteApplication` (remote_application.py:171-199, 317-363) and
`KBusMixin.serve_kbus_call` (kbus_mixin.py:316-343, 565-594).

Claim anchors: [`middleware`](../../../src/kajenn/config/elements.py#L254), [`BaseServer`](../../../src/kajenn/server.py#L141), [`on_websocket`](../../../src/kajenn/server.py#L607), [`MiddlewareMixin`](../../../src/kajenn/middleware/__init__.py#L73), [`serve_kbus_call`](../../../src/kajenn/kbus_mixin.py#L316).

## The handshake cookie an application may require

**The gate an application arms.** `BaseApplication.handshake_cookie`
([application.py](../../../src/kajenn/application.py)) returns `None`: no
cookie is demanded and every handshake passes. An application that returns a
cookie name has the handshake accepted and then closed 1008 when that cookie
is absent — `WsxConnection._open_gate` reads the property off the application
the path resolves to (#70 / PR #71). The cookie gate runs before the
authentication (wsx.py:260-272).

**Why the core owns the refusal and not the name.** An application that keeps
per-connection state elsewhere must be able to refuse a socket that carries no
identity of its own, before any message is served. The core supplies the gate
and the close code; the cookie's name and its meaning belong to whoever
returns it.

`SPECIFICATION.md` §6 Q1 is marked RESOLVED as of this phase: the design it
asked for is [decisions.md](decisions.md) and [design.md](design.md), and the
code follows in phases 1 to 5.

Claim anchors: [`BaseApplication`](../../../src/kajenn/application.py#L163), [`handshake_cookie`](../../../src/kajenn/application.py#L328), [`WsxConnection`](../../../src/kajenn/wsx.py#L212), [`_open_gate`](../../../src/kajenn/wsx.py#L250).

## Distance from the design

**Sending to a page from another process.** design.md §4 describes an
application that runs elsewhere reaching a page through the server that holds
the socket, with the signature of `send_message`. The code has no kbus route
that calls `send_message` or `send_serialized_message`: neither name appears in
`kbus_mixin.py` or `remote_application.py`, and `serve_kbus_call` serves only
http and websocket streams, `joined`, `forget_credentials` and route messages
(kbus_mixin.py:316-343).

**The `sequential` page declaration.** decisions.md §6 describes the `wsx`
field of a page's row, whose `sequential` key a page writes with
`openchannel`, enforced by the worker through the row's lock, and the refusal
of a message for a page that never opened its channel. The code has none of
it: `sequential` has no occurrence in `src/kajenn`, `_call_application` binds
the page after a 200 on `openchannel` and reads nothing else from the answer
(wsx.py:402-406), and a message carrying a `page_id` that was never bound is
served like any other.
