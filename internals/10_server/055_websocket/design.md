# Websocket

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

How a message on a socket becomes a method call: who holds the connection, what
a message looks like, how it reaches an application in this process or a worker
in another, and how the server addresses one page by itself.

## The anatomy

| Part | What it is |
|---|---|
| `WebSocket` | the neutral facade over the ASGI websocket: accept with subprotocol and headers, an idempotent close, text and bytes both ways, and an iterator over incoming messages. It knows nothing of WSX, so the admitted raw mode uses the same object |
| `WsxEnvelope` | one message as a class: `id?`, `method`, `path`, `data`, `page_id?`, `reply_path?`, and the answer's `id`, `status`, `data`. The `WSX://` prefix is its marker, and `data` is the TYTX string |
| `WsxConnection` | one per connection: it accepts, gates, resolves the identity once, then reads messages and serves each on a task of its own under a per-connection ceiling |
| `WebSocketRegistry` | every live connection of the server, and the `page_id → socket` association `openchannel` writes. Neutral: it knows no application |
| `BaseServer.on_websocket` | the entrance: it checks server state before demux, hands the raw socket to an application that defines `serve_websocket`, and otherwise builds a `WsxConnection` and drives it |
| `WsxControl` | the front's routing class under `_wsx`: `openchannel`, the command a page sends before anything of its own |
| `WsxCommands` | the worker's dispatcher branch for the same command, where the channel is written on the page's row |
| `server/websocket` | the config element: `origins`, `max_concurrent` |

## 1. The handshake, in order

The server checks its state before resolving an application or accepting the
connection. For WSX, the application, Origin and cookie gates precede message
dispatch.

1. **The state.** A server that is not `RUNNING` refuses before accept, for
   both WSX and raw applications. The browser sees a failed handshake rather
   than a readable 1013 close frame. The state gate precedes demux, as recorded
   in [WebSocket decisions](decisions.md).
2. **The Origin, BEFORE the accept.** A rejected origin must not get an
   accepted WSX socket. With `origins`
   declared the header must be in the list; without it, same-origin.
3. **The accept.**
4. **The identity, once.** The header first through the server's own
   authentication, then the session from the cookie. An invalid credential is
   accepted and closed 1008: the answer must be readable by the client.
5. **The home application.** The handshake's path names it through the server's
   demux, and its `handshake_cookie` says which cookie the socket must carry.
   Missing cookie → 1008 «connection cookie required». No application at that
   path → 1008 «no application at this path». A home application whose
   `handshake_cookie` is `None` has no cookie gate.
6. **The registration.** The connection enters the registry, and leaves it in
   the `finally` of the read loop, whatever ends it.

## 2. A message becomes a request

The envelope is read, and its `path` is the address. What happens next is the
core's ordinary dispatch, reached through a synthetic scope:

- `type: "http"`, `method: "WSK"`, the envelope's `path`, the handshake's
  headers and query string, and the `auth` and `session` copied from the
  handshake's own scope — an identity is not re-derived per message;
- the server's demux picks the application, and the application is called
  directly. NEVER the server itself: the middleware chain must not run once per
  message;
- the synthetic `receive` hands `data` as the body; the synthetic `send`
  collects the status, the headers and the body, and the answer's `data` is
  read back from it. A response that begins as a stream is refused with an
  explicit error;
- an `HTTPException` becomes the answer's `status`; anything else becomes 500
  and a log line.

A message carrying an `id` is registered in the server's `RequestRegistry`,
exactly like an HTTP request: the shutdown waits for it, `Request.db` closes
what it opened, and the in-flight picture is complete. A message with no `id`
is an event: executed, unanswered, unregistered.

## 3. The SPA's road

For the SPA the synthetic request is packed and sent down the lane, and the
worker serves it as it serves an HTTP request:

```
browser → WsxConnection → SpaApplication → SpaCommander.serve_request
        → CALL http → SpaWorker._serve_request → hosted_app_seam → the page
```

Three things ride along the way:

- **`page_id` and `reply_path`**, added to the `http` dict beside the
  connection id when the envelope carries them, and written into the environ
  and the scope as `genro.page_id` and `genro.reply_path`. Absent in the
  envelope means absent in the environ — not `None`.
- **`openchannel`**, the mandatory first message of a page. It is a route under
  the front's own `_wsx` root, and it carries a payload form of its own — no
  `http` dict — down to the worker, through the same barrier and the same
  placement a request meets. At the worker it shares the prologue of a request,
  because the page's user may be frozen and the row must be in memory before
  `wsx` can be written on it.
- **The per-page queue.** With `sequential` declared, the row's own lock is
  taken around the serving, in the CALL's own task, with the slot open and the
  pendings counted. Without it, messages of the same page are served in
  parallel, like the page's HTTP calls.

**The cid validates, it does not choose.** At `openchannel`, at every message
addressed to the SPA and at every push, the front checks that the commander's
`page_connection_map` names the same connection id the handshake carried. A
page that is not the caller's own is answered 403. The map is already up to
date when the browser sends `openchannel`, because the birth of a page rides
the REPLY of the HTTP request that created it.

## 4. The server speaks first

A page is addressed by `SpaWorker.send_message(page_id, path, data)`. The
worker reads the connection off the page's row and places a CALL upward; the
front's `websocket` branch, mounted under the commander's operations, finds the
socket the `page_id` is associated with, validates it against
`page_connection_map`, and writes a message in the shape of a request — no
`id`. The reply says «written on the socket» or «no websocket for this page».

Reconnection and death need no protocol of their own. A new `openchannel` for
the same `page_id` on another socket replaces the association; closing a socket
removes only the associations still pointing at it; a page the fold already
dropped fails validation at the first touch and is discarded. A `page_id` is
never reused.

## 5. Order, honestly

At the server every message is a task created in the order the socket is read.
The CALL goes down in the order the tasks reach the commander, and the per-page
lock is FIFO among those waiting. But a message that meets the user's barrier
during a transfer and one that arrives after it can swap places. So the core
guarantees ordering *in the absence of transfers*, and mutual exclusion always.
Whoever needs total ordering between its own writes waits for the answer or
carries a revision of its own: a save barrier is the application's.

## 6. What stands beside this

- **[025 routing system](../025_routing-system/README.md)** — the tree a message
  is resolved on is the same tree an HTTP request is resolved on, filters
  included.
- **[030 middleware](../030_middleware/README.md)** — the chain carries HTTP and
  says so; a websocket scope passes straight through it. What the chain would
  have done at the handshake is done once by the handshake itself.
- **[050 authentication](../050_authentication/README.md)** — the avatar of the
  handshake, and the 401-vs-403 rule the refusal follows.
- **[040 sessions](../040_sessions/README.md)** — the session read from the
  cookie at the handshake and kept for the connection.
- **[20 spa](../../20_spa/README.md)** — the front, the commander and the worker
  the SPA's messages travel through, and the ASGI seam of the worker.
