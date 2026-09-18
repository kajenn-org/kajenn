# The channel protocol

The channel is how one kajenn process talks to another: the parent end
(`ChannelHub`) binds a socket, the child end (`ChannelClient`) connects and
registers, and both speak the same frame protocol. This page is the wire
contract — what is on it, what bounds it, and what a peer may assume. It is
design material, not a how-to: the API is in
[Channel and communication](../api/channel.rst).

Peers are not negotiated. Every process on a channel must be configured with
the same limits and run the same protocol version; changing either requires a
coordinated restart of all of them.

## The frame

A frame is a fixed header, then JSON routing information, then opaque payload
bytes. The header is `struct.Struct("!4sBII")` — the magic `KJNF`, a version
byte (currently `1`), the length of the info and the length of the payload,
both big-endian unsigned 32-bit. Both lengths and their sum are checked before
either variable part is read. The frame layer never interprets the payload.

The info owns `id` (the correlation), `method` and `path` (the routing key).
`method` is one of `REGISTER`, `POST`, `CALL`, `REPLY` and `EVENT`. Routing
strings are non-empty and at most 4096 characters. Any other metadata is an
ordinary JSON tree, validated to have string keys, no duplicate key and no
non-finite number. `id`, `method` and `path` are reserved and cannot be
injected through the metadata.

The same `FrameCodec` serves the socket transport and the in-process
queue-backed one (`LocalChannel`), so a local member is validated exactly like
a remote one.

## What the hub guarantees

- A `CALL` parks a future on the frame id and resolves it when the matching
  `REPLY` arrives, reusing the id. The hub returns the reply's `data` verbatim:
  it reads only `result`, `error` and `events`, and interprets none of them.
- A `CALL` has no default deadline. A caller with an outer surface to protect
  passes its own `timeout`. The terminator is member death: on EOF, and on a
  deliberate `stop()`, every pending call addressed to that member fails with
  `ConnectionError`.
- `MAX_PENDING_CALLS` (1024, overridable per hub) bounds the outstanding calls;
  past it the call raises rather than growing memory without bound.
- An inbound `EVENT` is served on a task of its own, so a slow consumer does not
  hold the member's receive loop away from the reply behind it. Per-member
  event ordering is therefore **not** preserved: a consumer that needs ordering
  provides it itself.
- EOF is the connection-loss signal: the member is dropped from the rubric and
  `on_channel_lost(member)` fires. Sweep and relaunch belong to whoever owns
  that member. A deliberate `stop()` is not a death and fires nothing.
- A `ValueError` from the codec is a protocol violation of one member: that
  connection is closed and the hub's other members are untouched.
- A partial frame is a failure. EOF at a frame boundary is clean link loss and
  is never proof that the remote process died.

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

## Remote mounts

`RemoteApplication` mounts one application served by an endpoint in another
process; `RemoteApplicationRunner` is that endpoint, reachable as
`python -m kajenn.remote_runner`. The address is `uds:<path>` or
`tcp:<loopback-ip>:<port>`. A `factory` given as `module:callable` makes the
mount the owner of a fresh interpreter child; omitting it connects to a runner
somebody else manages, and a connect-only shutdown never signals its peer.

Defaults: `request_timeout` 30.0 s, `startup_timeout` 10.0 s,
`shutdown_timeout` 5.0 s, `max_calls` 16 in flight.

An owned launch generates a fresh random instance identifier and hands it to the
child through `KAJENN_REMOTE_INSTANCE_ID`, which the runner consumes before
constructing the application. On every new connection the frontend probes
readiness and checks the runner's reported identifier: a foreign runner raises
`RemotePeerMismatch` with the outcome `not_sent`, so reconnecting cannot
silently switch an owned mount to another process. This is accidental-peer
verification, not authentication against a hostile actor with local process
access.

A TCP address must be loopback unless the runner is started with
`--allow-network-listener`, which is a bind option only: it grants no remote
ownership and no peer authentication. UDS listeners reject every preexisting
directory entry, including stale sockets and dangling symlinks, and bind the raw
socket rather than letting asyncio replace a path, so competing binds have one
winner. Cleanup removes only a pathname whose device and inode are the ones this
listener recorded.

## Delivery and retries

`RemoteCallFailed.outcome` is `not_sent` or `unknown`, and an uncertain call is
**never** replayed — the operation may already have executed. Cancellation is an
ordinary asyncio cancellation carrying the same distinction on
`RemoteCallCancelled`; cancelling the wait does not undo application work.

A call whose caller timed out or cancelled reserves its id until the matching
late reply arrives or its connection ends, so a late reply cannot complete a new
call that reused the id. At most 256 such ids are retained per connection;
saturation closes that connection rather than forgetting an uncertain call.

## The environment policy

The transport limits are read from the environment of **every** communicating
process, the runner and any container included. They are not negotiated, so
configure the peers consistently and restart them together after a change.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAJENN_FRAME_MAX_BYTES` | `268435456` (256 MiB) | Ceiling on JSON info plus payload bytes of one frame |
| `KAJENN_FRAME_WARN_BYTES` | `1048576` (1 MiB) | Log an accepted frame strictly above this size; `0` disables the warning |
| `KAJENN_FRAME_WARN_INTERVAL_SECONDS` | `60` | Minimum seconds between two warnings on one codec; `0` logs every large frame |
| `KAJENN_HTTP_MAX_BODY_BYTES` | the frame maximum | Independent ceiling on one buffered HTTP body |

Values must be integers; the frame maximum must be at least 1 and must fit an
unsigned 32-bit length. Explicit `max_size` arguments on `FrameCodec`,
`FrameStream` and the channel ends, and `max_body_size` on `HttpRecord` and
`BufferedAsgiEndpoint`, override the environment default for that object.
Spawned children inherit the environment.

A warning reports the byte count, the threshold, the direction, the method, the
route and the worker name when the frame carries one. It never includes payload
contents. The throttle window is shared between send and receive on one codec,
and a new connection starts a new window.

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
[the channel benchmark](channel-benchmark.md).

## Not part of this contract

The channel carries identity as a string and the string tags of an authenticated
`Avatar`, and nothing else of the session: no `Avatar` object, no `Bag` data, no
pickle. The far endpoint rebuilds an `Avatar` from those two fields.

Streaming responses, raw WebSocket hosting and cross-host peer authentication
are outside this seam: `BufferedAsgiEndpoint` refuses a streaming answer, and
the core's own direct HTTP streaming and raw WebSocket support are unaffected by
any of it.
