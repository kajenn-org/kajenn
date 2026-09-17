# Websocket

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

How a message on a socket becomes a method call: who holds the connection, what
a message looks like, how it reaches the application its path names, and how
the server addresses one page by itself.

## The anatomy

| Part | What it is |
|---|---|
| `WebSocket` | the neutral facade over the ASGI websocket: accept with subprotocol and headers, an idempotent close, text and bytes both ways, and an iterator over incoming messages. It knows nothing of WSX, so the admitted raw mode uses the same object |
| `WsxEnvelope` | one message as a class: `id?`, `method`, `path`, `data`, `page_id?`, `reply_path?`, and the answer's `id`, `status`, `data`. The `WSX://` prefix is its marker, and `data` is the TYTX string |
| `WsxConnection` | one per connection: it accepts, gates, resolves the identity once, then reads messages and serves each on a task of its own under a per-connection ceiling |
| `WebSocketRegistry` | every live connection of the server, and the `page_id → socket` association `openchannel` writes. Neutral: it knows no application |
| `BaseServer.on_websocket` | the entrance: it checks server state before demux, hands the raw socket to an application that defines `serve_websocket`, and otherwise builds a `WsxConnection` and drives it |
| `OPENCHANNEL_PATH` | the one path under `_wsx` the connection looks at twice: the application answering it decides whether the page may speak here |
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

## 3. A page opens its channel

A message belongs to a page when its envelope carries `page_id`, and it says
where an unsolicited answer should arrive when it carries `reply_path`. Both
reach the application as `genro.page_id` and `genro.reply_path` on the
synthetic scope. Absent in the envelope means absent in the scope — not
`None`.

`openchannel` is the first message of a page, addressed under the `_wsx` root.
The connection resolves it through the ordinary demux, like any other message,
and looks at the answer a second time: on a 200, and only then, the page is
bound to this socket in the registry. The application decides; the core writes
the association.

That division is the seam. An application that keeps a page's state in another
process answers `openchannel` from there and needs no protocol of its own to
be bound here, because the core asks nothing about where the answer was
produced. An application that has no pages never answers under `_wsx`, and
nothing is bound.

## 4. The server speaks first

A page is addressed by `BaseServer.send_message(page_id, path, data)`. The
server finds the socket that page speaks on and writes one message shaped like
a request and carrying no `id`: not an answer, and nobody answers it. `True`
says it was written to the socket, `False` that the page speaks on none or
that its socket already closed. Delivered means written, never executed by the
page.

The method is the whole seam for writing to a browser. An application that
runs elsewhere reaches a page by reaching the server that holds the socket,
with this signature and no other.

Reconnection and death need no protocol of their own. A new `openchannel` for
the same `page_id` on another socket replaces the association; closing a socket
removes only the associations still pointing at it; a page nobody binds any
more is simply unreachable. A `page_id` is never reused.

## 5. Order, honestly

Every message is a task created in the order the socket is read, and messages
of one connection run in parallel up to `max_concurrent`. The core therefore
guarantees the order of *arrival*, never the order of *completion*: two
messages of the same page can finish in either order.

An application that needs mutual exclusion between its own messages serializes
them itself, and whoever needs total ordering between its own writes waits for
the answer or carries a revision of its own. A save barrier is the
application's.

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
- **[020 applications](../020_applications/README.md)** — the application the
  demux picks for a message, and the `handshake_cookie` it may arm the gate
  with.
