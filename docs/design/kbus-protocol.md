# The KajennBus protocol

The KajennBus is the internal communication of one kajenn instance. An
application declared with `spawner=` runs in a process of its own, and the
server reaches it over the kbus library (`kajenn-kbus` on PyPI, import `kbus`):
one `kbus.Dispatcher` in the server, one member per process, one kbus stream
per forwarded HTTP request and one `kbus.Message` per bus call. An application in the server's own process travels on nothing:
`kbus_call` hands its message straight to `serve_kbus_message`.
Communication between instances is klink, not this bus. This page is the
contract of the carriage — what is on it, what bounds it, and what a peer may
assume. It is design material, not a how-to; the wire format of a kbus frame
belongs to the kbus library.

## The dispatcher and the `server` member

In the lifespan of a server that declares an external application, before any
application hook runs, `KBusMixin`:

- builds one `kbus.Dispatcher` with the limits of `server.kbus` and listens on
  its address: `unix://<path>` or `wss://<host>:<port>/<path>`, or, when none is
  configured, a unix socket in a fresh private directory (mode 0700). A
  `wss://` dispatcher presents `certfile`/`keyfile`; plain `ws://` is refused,
  so a token never crosses a network in clear;
- connects one in-process member named `server`, admitted with a random secret.
  Every forwarded request (`member.open("<code>", message)`) and every call
  towards an external application goes out through it, and its handler serves
  the calls the processes make.

At shutdown the processes are stopped first, then the dispatcher is closed,
before `lifespan.shutdown.complete` reaches the server.

## The URL and the token

For every spawn the server mints a fresh token (`secrets.token_hex(32)`) and
writes it as the secret of the application's code on the dispatcher. A relaunch
overwrites it, so the token of the previous process stops admitting. The
spawner receives ONE URL and puts it in the environment variable
`KAJENN_KBUS_PARENT`:

- `unix://<code>:<token>@<socket path>`
- `wss://<code>:<token>@<host>:<port>/<path>` — the process verifies the
  dispatcher's certificate with `server.kbus(cafile=...)` when given (it loads
  the same configuration), with the system's authorities otherwise

The command line is `<python> -m kajenn serve <source> --role application:<code>`
and nothing more: the token never appears among the arguments. The process
pops the variable, splits the name and the token from the address and connects
a `kbus.Member(<code>, secret=<token>)`. A member with an unknown name or a
wrong secret is refused by the dispatcher (`kbus.Rejected`).

## Joined

The role process loads the same configuration and builds only its own
application. It connects after that application's `on_startup` returned, then
calls `server.joined` with `meta = {"well_known": [...], "pid": <pid>}`:
`well_known` lists the discovery names the application answers under
`/.well-known/`. The server indexes those names on the mount of that
application, replacing those of a previous process, tells the spawner the role
joined and records the pid. A `well_known` that is not a list of non-empty
strings is refused with `kbus.Refused`: the process ends and is relaunched.

Until its process has joined, a mount answers 503 and `kbus_call` towards it
raises `KBusCallError` with status 503; its discovery names answer 404.

## The envelope

A message carries the request in `meta` and the body in the payload:

| `meta` key | Meaning |
| --- | --- |
| `format` | `"http"` (the payload is an `HttpRecord` request) or `"json"` (the payload is the JSON request data) |
| `path` | the route's path |
| `auth` | `{"identity", "tags"}` of the caller's avatar, when there is one |
| `channel` | becomes `scope["kajenn.channel"]` |
| `kbus` | `true` on a forwarded request that reached the server as a bus call; becomes `scope["kajenn.kbus"]` |
| `genro` | `{"page_id", "reply_path"}`, the keys present on the forwarded scope; each becomes `scope["genro.<key>"]` |

`auth` is read by the execution point (`RoutedApplication.execute`) as the
caller's avatar. The key `credential` is reserved and still unread. Other keys
travel untouched; the keys kbus reserves for itself (`id`, `kind`, `route`,
`from`, `via`) cannot be set.

An `http` message opens a stream (below); the reply of a `json` call carries
`status`, `format: "json"` and the response body.

## The answer stream

A forwarded HTTP request opens one stream, the request record as the opening
message. The process answers on it:

- one message `meta = {"status", "headers"}`, empty payload, at
  `http.response.start` — headers as `[name, value]` latin-1 text pairs;
- one message `meta = {"more_body"}` per `http.response.body`, the chunk as
  payload;
- the close of its direction after the body whose `more_body` is false.

The mount writes `http.response.start` on the first message and one
`http.response.body` per following message with the `more_body` it carries, so
a buffered answer stays one closing body and a chunked one (an SSE stream is
one) reaches the client chunk by chunk. `max_frame` binds one chunk, not the
answer. The mount's own direction carries no data and closes once the answer
is written.

- A client that sends `http.disconnect` while the answer runs aborts the
  stream: in the process the application's `receive` returns
  `http.disconnect` and its task is cancelled. A `WSK` request is not watched:
  its answer is buffered and adapted in the process, as one closing body.
- `request_timeout` and the `max_calls` slot bound the wait for the first
  message only, never the length of the stream.
- A process that dies before the first message answers as in the table below;
  after it, the answer ends without its terminal body.
- An application that fails after its start, or returns without finishing its
  body, aborts the stream: the answer ends without its terminal body.

## The websocket stream

When the application class defines `serve_websocket`, a websocket on its mount
opens one stream with `meta = {"format": "websocket", "path"}` and a websocket
scope record as payload (`HttpRecord.encode_websocket`). Without it, the socket
goes through WSX as for any application.

Every ASGI websocket event crosses the stream as one message, in both
directions, starting with the client's `websocket.connect`:

- `meta` carries every key of the event but `text` and `bytes` — `type`, and
  `code`/`reason` for a close or a disconnect; headers as `[name, value]`
  latin-1 text pairs;
- a `text` event has the UTF-8 text as payload and `meta["text"] = True`; a
  `bytes` event has the bytes as payload and `meta["bytes"] = True`.

In the process the application's `serve_websocket` runs with `receive`/`send`
bound to the stream. The client's `websocket.disconnect` crosses, then the
mount closes its direction; the application returning closes the process's
direction. A stream that ends or fails makes the application's `receive`
return `websocket.disconnect` with code 1006. A process that has not joined,
dies or aborts the stream closes the client's socket with code 1011.

## Serving a message: `serve_kbus_message`

`KBusMixin` adds `kbus_call(path, data)`, `kbus_post(path, data)` and
`serve_kbus_message` to the server. `serve_kbus_message` is the one place a
message becomes an ASGI scope, served through the demux, with the avatar
rebuilt from `auth`, no session and `"kajenn.kbus": True`.

A route answers `kbus_call` the same way whether its application runs in the
server's process or in a spawned one. A failing handler answers 500 with
`"<Type>: <message>"` in the json format; `kbus_call` raises `KBusCallError`
for a status of 400 or more. On an answer stream, an exception raised before
the answer started travels back as one message `{"failure"}` and is raised
again in the server (see the HTTP record); an exception other than an
`HTTPException` arrives there as an `ExternalFailure` carrying the original
`"<Type>: <message>"`, so a `kbus_call` or a WSX message answers the same text
as in the server's process, and only the spawned process logs its traceback.

In a role process, a `kbus_call` to a path outside its own application goes to
the `server` member, which serves it through the demux exactly like an
in-process call; a transport failure raises `KBusCallError` with status 503.

## Statuses at the mount

`RemoteApplication` maps the outcome of a forwarded request:

| Outcome | Status |
| --- | --- |
| a body over `KAJENN_HTTP_MAX_BODY_BYTES`, or a message over the dispatcher's `max_frame` (`kbus.FrameTooLarge`) | 413 |
| the process has not joined, `kbus.NoSuchMember`, `kbus.LinkLost`, or no first message within `request_timeout` | 503 |
| an error reply, or a stream aborted before its first message | 502 |

At most `max_calls` requests per mount are in flight at once.

## Relaunch

A joined member that disconnects while the server runs is handed back to its
spawner with the recorded pid; a process that exits without being stopped
(before or after it joined) is handed back too, one relaunch for the two
signals of one process. In the process, the closed connection to the server
ends the role. The relaunches of a role are spaced by a backoff measured from
the previous start: 1 s, doubling up to 30 s, back to 1 s once the process
stayed joined 60 s; attempts are not limited, one relaunch is pending per role
at most, and `stop` cancels it and ends the process at shutdown (killed past
`shutdown_timeout_seconds`). `Spawner` is the interface other backends
implement; `SubprocessSpawner` is the `"subprocess"` one.

## Limits

`server.kbus(address, certfile, keyfile, cafile, max_frame, max_meta,
max_route, max_pending, stream_window, write_buffer)`: the six limits build the
dispatcher's `kbus.Limits`, and the role process uses the same ones. An omitted
limit keeps the kbus default. An address in any other form than `unix://` or
`wss://`, `ws://` included, is a boot error, and so is a `wss://` address
without `certfile` and `keyfile`.

## The HTTP record

A forwarded HTTP call travels as an `HttpRecord`: the magic `b"HTTP"`, a
version byte, the metadata length and the metadata as ASCII JSON, then the body
bytes. The body is never interpreted. `encode_request`/`decode_request` carry an
ASGI http scope reduced to its transportable fields — `method`, `path`,
`raw_path`, `root_path`, `query_string`, `headers`, `scheme`, `server`,
`client`, `http_version` — with ordered header pairs, so duplicates survive.
`encode_response`/`decode_response` carry a status, text headers and a body;
the answer stream does not use them.

`BufferedAsgiEndpoint` calls the application at the far end of a `WSK` request:
it presents one complete request message and buffers the whole response,
bounded on both sides by `max_body_size`, and by default refuses a chunked or
event-stream answer instead of buffering it. It holds no state of its own, so routing and
whatever persists across calls stay outside the application call.

`RemoteApplication`, the server's mount of an external application, is a
transparent wire: the application receives a request exactly as it would in
the server's process — method, path, query string, headers, channel, avatar,
`kajenn.kbus`, `genro.page_id` and `genro.reply_path` — and the caller gets the
same status, headers and body, a streamed answer chunk by chunk. The mount
forwards every header of the request, the `Authorization` header included, and
never verifies a credential. The channel the request already carries travels as
`meta.channel`; the proxy sets no default. The process verifies the forwarded
`Authorization` header through `authenticate_credential`, which reaches the
server's authentication route for that channel over the bus. `meta.auth` on a
proxied request is the server's session identity (or an avatar a middleware put
on the server's scope); a header never becomes `meta.auth`. `meta.kbus` is set
only when the server's scope carries `kajenn.kbus`, and `meta.genro` only when
it carries `genro.page_id` or `genro.reply_path`.

An exception the application raises before its answer starts does not become a
response in the process: the first message of the answer is
`{"failure": {"status", "detail", "headers"}}` for an `HTTPException` (plus
`location` for a `Redirect`), `{"failure": {"detail"}}` for any other
exception, and the mount raises it again, so the server's middleware answers it
as it answers an application of its own. `scope["session"]` stays `None` in the
process: the session store lives in the server.

## The body ceiling

`KAJENN_HTTP_MAX_BODY_BYTES` bounds the body a mount reads before forwarding
it; a body over it answers 413. Values must be integers.

## Not part of this contract

The KajennBus carries identity as a string and the string tags of an authenticated
`Avatar`, and nothing else of the session: no `Avatar` object, no `Bag` data, no
pickle. The far endpoint rebuilds an `Avatar` from those two fields.

`BufferedAsgiEndpoint` refuses a streaming answer: the answer stream and the
websocket stream above carry streaming answers and raw sockets without it.

The package `kajenn.kbus` (the `KJNF` frame, `KBusHub`, `KBusClient`) is no
longer used by the carriage described here; it remains for kajenn-orchestra.
