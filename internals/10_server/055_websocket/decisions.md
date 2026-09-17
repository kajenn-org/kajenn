# Websocket — decisions

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

**The websocket, with the work finished.** Read this as a report from the day
everything described here is running: it says what the transport *is*, and
never what it lacks. What the code holds today is [status.md](status.md)'s
subject.

Every voice carries its source. A voice sourced to the owner and a date was
decided in conversation before it reached this file. Two working documents hold
those conversations: the decision register of the investigation opened on
2026-09-05, which carries the `W-1`…`W-13` questions and the namings from `N14`
on, and the investigation's diary, which carries the namings `N1`…`N13` decided
on 2026-09-05 turn by turn. The tags are their own numbering, kept here so a
later reader can find the conversation that produced a line.

---

## 1. The server holds the connection

**Source: owner, 2026-09-05 (W-1), «ok. primario quello e websocket alta
frequenza solo un domani».** `BaseServer.on_websocket` is the one place a
websocket is accepted, read, written and closed. The server is the only party
that sees every application and every connection, so the questions with one
machine-wide answer are answered there once: the Origin gate, the identity, the
registry of live connections, the refusal while the server is not `RUNNING`.

Each message is then handed to the application its `path` names, through the
same demux an HTTP request goes through. So one websocket per browser serves
every mounted application, and an application that lives in the server process
is reachable on the same socket as the SPA.

## 4. The protocol is WSX, and TYTX carries the values

**Source: owner, 2026-09-05 (W-4), «a».** A message is the text prefix
`WSX://` followed by JSON: `id` (optional), `method`, `path`, `data`,
`page_id` (optional), `reply_path` (optional). An answer is `id`, `status`,
`data`. The prefix is what tells a WSX message from any other text on the
socket, and the four fields are the ones the lane's own `Frame` already
carries, so a message copies into a CALL one field at a time.

### 4a. An application does not learn a new method

**Source: owner, 2026-09-05 (W-4c), «abbiamo delle request che generiamo per
GET, POST, PUT, DELETE e ora per WSK. Se una app usa websocket in questo modo
(emulazione rpc) accetta la convenzione».** The server builds a synthetic HTTP
request from the message and calls the application the ordinary way. The signal
is the METHOD: `WSK`. There is no translation to POST — the hosted site sees
`WSK` — and no new method on any application class.

## 5. Identity is judged once; every message is placed again

**Source: owner, 2026-09-05 (W-5), «a+».** The handshake is the only HTTP
request of the connection, so it is where the avatar is resolved — header
first, then session, the way the chain does for HTTP — and a refusal closes the
socket there. For the SPA the identity is the connection id in the cookie, and
a login changes the owner of that id exactly as it does over HTTP.

## 6. Order: parallel with a ceiling, and the queue belongs to the page

**Source: owner, 2026-09-05 (W-8), «sì, mi pare bello», revised the same day.**
At the server every message is a task, with a per-connection ceiling
(`max_concurrent`) and the control ping outside it; the client correlates on
the `id`. The ceiling is **configurable, default 16** (owner, 2026-09-06:
«configurabile default 16»). It exists because a client that floods must not
sink the server, and it is a setpoint because how many calls a page fires at
once is an installation's own business. This is the semantics the HTTP calls of the same page already have —
a page fires dozens of calls at once — and a slow message blocks neither the
others nor the ping.

Whether a page is served one message at a time is the PAGE's own declaration.
The owner's words: «ragioniamo su un `page_id` specifico: una pagina di ordini,
lì mi va bene che tutte le chiamate websocket vadano in parallelo; poi ho una
pagina che fa monitoraggio eventi su un dispositivo e lì dico che tutta la
pagina deve essere serializzata come eventi». The declaration lives in the
`wsx` field of the page's row — absent, `True`, or a dict whose `sequential`
key is the flag — written by the page's first WSX message, the mandatory
`openchannel` command, and enforced by the WORKER through the row's own lock. A
message for a page that never opened its channel is refused with a clear error.

## 7. The handshake cookie gate

**Source: owner, 2026-09-06 (W-13), «a».** The path of the handshake names the
socket's home application through the server's demux, and that application's
`handshake_cookie` property says which cookie the handshake must carry —
`None` for an application that requires none. The owner's words: «assente →
accept e 1008 "connection cookie required"». A handshake on a path no
application serves is accepted and closed 1008, «no application at this path».
With a home application that declares no cookie, there is no cookie gate.

## 9. One websocket per index page

**Source: owner, 2026-09-05 (W-9), «la connessione è della index page e tutte
le sottopagine lo devono comunque mettere. Potenzialmente potrei avere 3 index
page e ognuna 6 sub pages e avere 3 websocket e non 18».** The envelope carries
an OPTIONAL `page_id`, message by message, in both directions — absent for a
client that has no pages. The socket belongs to the root page; a subpage sends
through it and puts its own `page_id` in every message. How the client
channels that is the page framework's business.
