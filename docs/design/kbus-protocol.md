# The KajennBus protocol

The KajennBus is the internal communication of one kajenn instance: the
server end (`KBusHub`) binds a socket, an application process (`KBusClient`)
connects and registers, an in-process end (`LocalKBus`) joins the hub over
queues, and every link is the same `KBusConnector`. An application in the
server's own process is not attached as a `LocalKBus`: `kbus_call` hands its
frame straight to `serve_kbus_frame`.
Communication between instances is klink, not this bus. This page is the wire
contract — what is on it, what bounds it, and what a peer may assume. It is
design material, not a how-to: the API is in
[KajennBus and communication](../api/kbus.rst).

Peers are not negotiated. Every process on a KajennBus must be configured with
the same limits and run the same protocol version; changing either requires a
coordinated restart of all of them.

## The frame

A frame is a fixed header, then JSON routing information, then opaque payload
bytes. The header is `struct.Struct("!4sBII")` — the magic `KJNF`, a version
byte (currently `1`), the length of the info and the length of the payload,
both big-endian unsigned 32-bit. Both lengths and their sum are checked before
either variable part is read. The frame layer never interprets the payload.

The info owns `id` (the correlation), `method` and `path` (the routing key).
`method` is one of `REGISTER`, `CALL`, `REPLY` and `EVENT`. Routing
strings are non-empty and at most 4096 characters. Any other metadata is an
ordinary JSON tree, validated to have string keys, no duplicate key and no
non-finite number. `id`, `method` and `path` are reserved and cannot be
injected through the metadata.

The same `FrameCodec` serves the socket transport and the in-process
queue-backed one (`LocalKBus`), so a local member is validated exactly like
a remote one.

## The link: `KBusConnector`

Both ends of every link are a `KBusConnector` over any object with
`read() -> Frame | None`, `write(frame)` and `close()`: a socket `FrameStream`,
an in-process `LocalFrameStream`, or another stream such as a WebSocket.

- **Correlation.** A `CALL` parks a future on its id and path; the `REPLY`
  reusing that id and path resolves it, and the caller receives the reply frame
  whole (info and payload). A reply on another path is a violation.
- **Served CALLs.** Either end serves the CALLs it receives through `on_call`,
  each on a task of its own. A CALL with no `on_call`, a raising handler or a
  handler that does not return the matching REPLY becomes an error REPLY
  (`info["error"]`); the link stays up.
- **Events.** An inbound `EVENT` is served on a task of its own, so per-link
  event ordering is **not** preserved.
- **Outcomes.** A call that gets no reply raises `KBusCallFailed`; a cancelled
  caller gets its own `asyncio.CancelledError` back (`KBusCallCancelled` is
  that class), so an outer `asyncio.timeout` still raises `TimeoutError`. Both
  carry `outcome`: `not_sent` when the frame never reached the write, `unknown`
  otherwise. An `unknown` call is **never** replayed: it may already have
  executed. A `timeout` that expires after the REPLY arrived returns the REPLY.
- **Abandoned ids.** A timed-out or cancelled call keeps its id reserved until
  its late reply arrives or the link ends, so a late reply cannot complete a
  new call. At most 256 such ids per link (`max_abandoned`); at most 1024
  pending calls (`max_pending`).
- **Violations.** A `ValueError` from the codec, or a reply that contradicts its
  call, ends that link and fails every pending call with `outcome="unknown"`.
  Other links are untouched. A partial frame is a failure; EOF at a frame
  boundary is clean link loss and never proof that the remote process died.
- A `CALL` has no default deadline; a caller with an outer surface to protect
  passes its own `timeout`.

## REGISTER

The first frame of a hub connection is a `REGISTER` whose control-json payload
is the presentation: `name` (non-empty, unique while registered: a relaunched
process registers the same code again) and `pid` are required,
any other key is kept on the member. The hub answers with a `REPLY` carrying
its own data (what `on_member_joined` returns), or an error REPLY before
closing. Anything else, a duplicate name or no REGISTER within 10 s closes the
connection. On loss the member leaves the rubric and its owner is told. A
stopping hub stops listening first, refuses any REGISTER and closes every
member, including the connections still presenting themselves.

## Reserved `info` keys

Besides `id`, `method` and `path`, the bus reserves `format`, `auth`
(`identity`, `tags`), `channel` and `credential`. Other keys travel untouched.

## Serving a frame: `serve_kbus_frame`

`KBusMixin` adds `kbus_call(path, data)`, `kbus_post(path, data)` and
`serve_kbus_frame` to the server. `serve_kbus_frame` is the one place a frame
becomes an ASGI scope, served through the demux:

- `format: "json"` (the default): the payload is the JSON request data; the
  REPLY carries `status`, `format: "json"` and the response body.
- `format: "http"`: the payload is an `HttpRecord` request; the REPLY carries
  the `HttpRecord` response.

A route answers `kbus_call` the same way whether its application runs in the
server's process (the frame goes straight to `serve_kbus_frame`, the data
crossing as JSON bytes) or in a spawned process. A failing handler answers 500
with `"<Type>: <message>"` in both formats; `kbus_call` raises `KBusCallError`
for a status of 400 or more, and for an error REPLY (with no status).

## Trust

A uds address and a loopback tcp address are trusted: no secret is required and
none is checked. A non-loopback tcp address (`server.kbus(address=, secret=)`)
requires a secret: the configuration refuses it without one, and the hub refuses
a REGISTER whose `presentation["secret"]` does not match. Before admission the
REGISTER on such an address is read with a ceiling of 64 KiB; the admitted
member's link then uses the normal frame maximum. The network listener carries
the secret in clear and must run on a private network; TLS belongs to klink.

## The spawner and the role

An application declared with `spawner="subprocess"` runs in its own process.
The server mints a token, and `SubprocessSpawner.ensure` starts
`python -m kajenn serve <source> --role application:<code> --parent <hub>`
with `KAJENN_KBUS_TOKEN` (and `KAJENN_KBUS_SECRET` on a network hub). The role
process loads the same configuration, serves only that application and registers
with the hub, and ends when it loses the link to the hub. Its REGISTER presents
`role` (`application:<code>`), `token` and `well_known`: the list of discovery
names the application answers under `/.well-known/`. The server refuses a token
that is not the one it minted and a `well_known` that is not a list of non-empty
strings; it indexes the names on the mount of that application at each join,
replacing those of the previous process. Until the process registers, those
names answer 404. A member lost while
the server runs, or a process that exits without being stopped (before or after
its REGISTER), is relaunched with a new token, one relaunch for the two signals
of one process. The relaunches of a role are spaced by a backoff measured from
the previous start: 1 s, doubling up to 30 s, back to 1 s once a member stayed
registered 60 s; attempts are not limited, one relaunch is pending per role at
most, and `stop` cancels it and ends the process at shutdown. `KBusSpawner` is
the interface other backends implement.

## The HTTP record

A forwarded HTTP call travels as an `HttpRecord`: the magic `b"HTTP"`, a
version byte, the metadata length and the metadata as ASCII JSON, then the body
bytes. The body is never interpreted. `encode_request`/`decode_request` carry an
ASGI http scope reduced to its transportable fields — `method`, `path`,
`raw_path`, `root_path`, `query_string`, `headers`, `scheme`, `server`,
`client`, `http_version` — with ordered header pairs, so duplicates survive.
`encode_response`/`decode_response` carry a status, text headers and a body.

`BufferedAsgiEndpoint` is what calls the application at the far end: it presents
one complete request message and buffers the whole response, bounded on both
sides by `max_body_size`, and by default refuses a chunked or event-stream
answer instead of buffering it. It holds no state of its own, so routing and
whatever persists across calls stay outside the application call.

## The environment policy

The transport limits are read from the environment of **every** communicating
process, spawned role processes and any container included. They are not negotiated, so
configure the peers consistently and restart them together after a change.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAJENN_FRAME_MAX_BYTES` | `268435456` (256 MiB) | Ceiling on JSON info plus payload bytes of one frame |
| `KAJENN_FRAME_WARN_BYTES` | `1048576` (1 MiB) | Log an accepted frame strictly above this size; `0` disables the warning |
| `KAJENN_FRAME_WARN_INTERVAL_SECONDS` | `60` | Minimum seconds between two warnings on one codec; `0` logs every large frame |
| `KAJENN_HTTP_MAX_BODY_BYTES` | the frame maximum | Independent ceiling on one buffered HTTP body |

Values must be integers; the frame maximum must be at least 1 and must fit an
unsigned 32-bit length. Explicit `max_size` arguments on `FrameCodec`,
`FrameStream` and the KajennBus ends, and `max_body_size` on `HttpRecord` and
`BufferedAsgiEndpoint`, override the environment default for that object.
Spawned children inherit the environment.

A warning reports the byte count, the threshold, the direction, the method and
the route. It never includes payload contents. The throttle window is shared
between send and receive on one codec, and a new connection starts a new window.

Neither threshold reserves memory, and neither changes the bytes on the wire: a
128 KiB frame is identical under a 16 MiB and a 256 MiB maximum. A large
accepted frame still costs whole-body buffering and copies, so the maximum is
not a process-wide memory budget. The complete HTTP record, the routing metadata
and any snapshot must together fit inside one frame, which is why a body exactly
equal to the frame maximum cannot fit once the overhead is added.

A local frame-size rejection raises `FrameTooLarge` before a single byte is
written. Incoming over-limit headers close the offending connection before its
body is read: there is no safe resynchronization without draining an untrusted
body. A partial write is likewise an uncertain failure.

These limits are not a substitute for the HTTP request limit a routed
application does not have — see the [FAQ](../faq.md) on upload size.

Measured throughput for both transports is in
[the KajennBus benchmark](kbus-benchmark.md).

## Not part of this contract

The KajennBus carries identity as a string and the string tags of an authenticated
`Avatar`, and nothing else of the session: no `Avatar` object, no `Bag` data, no
pickle. The far endpoint rebuilds an `Avatar` from those two fields.

Streaming responses, raw WebSocket hosting and cross-host peer authentication
are outside this seam: `BufferedAsgiEndpoint` refuses a streaming answer, and
the core's own direct HTTP streaming and raw WebSocket support are unaffected by
any of it.
