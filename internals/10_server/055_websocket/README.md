# Websocket

**Version**: 0.2 · **Last Updated**: 2026-09-07 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/055_websocket.md` — DIVERGENT, 9 CONVERGE / 4 DIVERGE / 18 SILENT.

The second transport of the server. A browser opens one websocket, and every
message on it is served like an HTTP request: the server holds the connection,
reads the message, picks the application the message's `path` names, and hands
it a synthetic request whose method is `WSK`. There is one dispatch engine and
two ways into it — which is what `SPECIFICATION.md` §6 Q1 asks for — so a
route, its entry rules and its cleanups work the same whichever transport the
call arrived on.

Identity is judged once, at the handshake, because the handshake is the only
HTTP request of the connection. Everything after it is a message, and a message
carries no cookie of its own.

An application that serves a message elsewhere receives it in the same shape
it receives an HTTP request, so it needs no second protocol and no second
socket. The whole seam is the synthetic scope and the binding of a page to its
socket: where the answer is produced is the application's business, and the
browser cannot tell.

Its parts:

- **who holds the connection** — the handshake, the Origin gate, the identity,
  the registry of live connections
- **the WSX envelope** — the four fields both ways, the optional `page_id` and
  `reply_path`, and what a message without an `id` means
- **a message is a request** — the synthetic scope, `WSK`, and what an
  application does *not* have to implement
- **a page opens its channel** — `openchannel`, the binding to the socket,
  and the addressed message coming back
- **what does not travel here** — datachanges and dbevents stay pull
- **the admitted seam** — an application that wants the raw websocket

The handshake resolves an avatar using the HTTP authentication rules.

> [Authentication](../050_authentication/README.md).
