---
orphan: true
---

# Issue 72 protocol contract

Version: 1. Internal transport contract; coordinated peer restart required.

## Channel

The internal channel is versioned independently of browser WSX. A fixed magic
and version precede TWO big-endian unsigned 32-bit lengths, then JSON info,
then opaque payload bytes. Both lengths and their sum are checked before reading
the variable parts. There is no separate info ceiling; the default combined
limit is 256 MiB (excluding the fixed 13-byte header). Socket and local queue transports use the same codec and validation.
The channel imports no SPA, TYTX, Bag or application codec.

Info owns id, method (CALL/REPLY/EVENT/REGISTER/POST), routing path and payload
format. Additional metadata has explicit endpoint ownership. Routing keys are
nonempty bounded strings; metadata must be a JSON object with no duplicate keys,
non-finite numbers or reserved-key injection. Replies echo correlation and route.
A malformed REGISTER (including an invalid PID) is rejected with socket closure,
without affecting other members. A protocol violation closes only that connection. Partial frames are failures;
EOF at a frame boundary is clean link loss, never proof of remote process death.
Concurrent stream writes serialize, bounded messages and outstanding requests
provide backpressure. Lost/possibly delivered calls are never replayed.

Control dictionaries retain value-oriented endpoint APIs, but encoding/decoding
is explicit at their producing/consuming endpoints. Frame never implicitly
hydrates payload. The SPA connector folds worker metadata without decoding the
HTTP record. Every new connection owns a fresh pending-call generation.

## HTTP application bytes

A versioned inner record is metadata length + JSON metadata + raw body bytes.
Latin-1 provides a reversible JSON representation for raw path/query/header
bytes; ordered header pairs preserve duplicates. No body base64. Only frontend
HTTP adapter and destination seam open this record. The commander sees cid,
page_id, reply_path, identity, user_frozen separately; identity and freeze verdict
are overwritten by the commander. Worker response info owns connection_id,
worker_events and explicit refusal/errors. A response body is never hydrated by
an HTTP forwarding endpoint.

Preserve existing local demux: it strips path once and leaves root_path intact.
Carry external mount separately for generic remote app construction and Swagger
links. Carry raw_path, query_string, scheme, server/client, HTTP version and
ordered headers exactly. WSGI receives genro.identity/page_id/reply_path as before.
Only identity and string tags of authenticated Avatar may cross generic trusted
links; no session, Avatar object, Bag data or pickle. Endpoint restores an Avatar.

Generic remote hosting initially buffers bounded messages, rejects SSE, raw
websocket and streaming response modes explicitly, and has bounded request,
startup and shutdown times. Existing local streaming/raw websocket is unchanged.
TCP defaults to loopback-only. The runner may explicitly opt into a network
listener with `--allow-network-listener` for a private container network; the
client still accepts only loopback destinations. Docker publishes the container
port to host loopback. Public authenticated/protected links need a later design. Spawn ownership is separate from connect-only lifecycle.

## Browser WSX

Keep WSX:// JSON and data as serialized JSON TYTX string. Parse outer JSON only
in routing code. Explicit serialized payload type/property differs from ordinary
Python strings. Value-based send_message remains. Absent data and legacy outer JSON null retain their existing absent-data
semantics. Serialized TYTX null, empty string, text and bytes retain their
distinct serialized forms; empty HTTP body still means absent WSX data.
Synthetic WSK body uses serialized input directly. Application endpoints adapt
XML/msgpack replies to browser JSON when needed; routing intermediaries do not.
Control failures may encode their own values. Page ownership, openchannel binding
only after success, event no-reply and correlation remain. The worker page queue
checks readiness and ordering, not equality between the incoming cookie and the
connection name assigned by the hosted site.

## Configuration and compatibility

Import `RemoteApplication` from `kajenn.remote_application`. Its `address`
is `uds:<private-path>` or `tcp:<loopback-ip>:<port>`. Supplying `factory` as
`module:callable` gives the frontend ownership of a fresh interpreter child;
omitting it connects to a separately managed runner. `code` and `mount` use
the normal application registry. See `examples/remote_openapi/README.md` for
complete commands; no custom demux is required. Startup follows endpoint
readiness, and shutdown drains within a configured bound before terminating
only an owned child. Connection-only shutdown never signals its peer.

An owned launch generates a fresh random instance identifier. The child receives
it through the private launch environment (`KAJENN_REMOTE_INSTANCE_ID`, consumed
by the runner command before constructing the application), not a command-line
argument. On EVERY new connection, the frontend probes readiness and checks the
runner's reported identifier before sending application traffic. The expected
identifier is not included in that probe. A foreign runner causes
`RemotePeerMismatch` with application-call outcome `not_sent`; startup fails and
stops only the child this mount spawned. Reconnecting cannot silently switch an
owned mount to another runner. Connect-only mounts intentionally do not require
an owned-launch identifier. This is accidental-peer/launch verification, not
network authentication against a hostile actor with local process access.

UDS listeners reject every preexisting directory entry, including stale sockets
and dangling symlinks. They use raw socket bind rather than asyncio's path-based
replacement behavior, so competing runner binds have one winner. Python 3.13+
automatic pathname cleanup is disabled. On shutdown/setup failure, explicit
cleanup removes only a pathname with the recorded device/inode of this listener;
a replacement entry is left alone. Socket directories must be private or
operator-controlled. An operator must resolve stale paths after verifying their
owner; the runner does not reclaim them automatically. A SIGKILLed parent can
still leave its child alive: graceful ownership shutdown is not parent-death
supervision. A later owned mount now rejects that orphan rather than adopting it.

Transport policy is configured in the environment **before starting every
communicating process**, including external runners and containers:

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAJENN_FRAME_MAX_BYTES` | `268435456` (256 MiB) | Maximum JSON info + opaque payload bytes |
| `KAJENN_FRAME_WARN_BYTES` | `1048576` (1 MiB) | Log an accepted frame strictly above this size; `0` disables warnings |
| `KAJENN_FRAME_WARN_INTERVAL_SECONDS` | `60` | Minimum seconds between warnings per codec/connection; `0` logs every large frame |
| `KAJENN_HTTP_MAX_BODY_BYTES` | Frame maximum | Optional independent buffered HTTP body ceiling, e.g. a lower upload/download policy |

Values must be nonnegative integers; the frame maximum must be positive and fit
an unsigned 32-bit length. Explicit `FrameCodec`/`FrameStream`/channel `max_size`
and endpoint/`HttpRecord` `max_body_size` arguments override their environment
defaults. Configure peers consistently: these settings are not negotiated.
Spawned workers inherit the environment. Changes require a coordinated restart.
Warnings report byte count, threshold, direction, method, route and worker name
when carried in the snapshot. They never include payload contents. Throttling
is shared between send/receive on a codec; a new connection starts a new window.

No memory is reserved by either threshold. A 128 KiB frame has identical wire
bytes under a 16 MiB or 256 MiB maximum. Large accepted frames still incur whole
body buffering and copies; the maximum is not a process-wide memory budget.
HTTP metadata no longer has a separate 64 KiB cap. The complete HTTP record,
routing metadata and worker snapshot must together fit the outer frame; a body
exactly equal to the frame maximum cannot fit after adding that overhead.

A local frame-size rejection raises `FrameTooLarge` before writing any bytes.
Outgoing oversized HTTP requests receive 413. Oversized worker results become
correlated error replies (HTTP 502 at the frontend), preserving lifecycle events
and a snapshot rejected with the original result, and leaving unrelated calls
and the connection alive. The operation may already have executed: it is never
replayed. If even the essential control/error envelope cannot fit, SPA still
closes the link rather than silently losing lifecycle events. Configure the
maximum to accommodate that envelope; ordinary result-size refusal does not
require disconnection. Incoming over-limit headers close the offending
connection before reading its body: there is no safe resynchronization without
draining an untrusted body. Partial writes likewise remain uncertain failures.

Defaults are sixteen admitted generic calls and thirty seconds per generic request. SPA limits
its admitted buffered bodies to sixteen and preserves its existing placement
and lifecycle deadlines. A closed or malformed connection fails pending calls;
an uncertain call is never replayed. `RemoteCallFailed.outcome` is `not_sent`
or `unknown`. Cancellation remains an asyncio cancellation, with the same
outcome distinction on `RemoteCallCancelled`; cancelling the wait does not
undo application work. A subsequent new call may establish a new connection.

The `KJNF` internal protocol is incompatible with the old `WSX://` socket
frames. Its header is `!4sBII`: magic, version byte (1), info length, payload
length. Restart all communicating core/bridge peers together. Browser WSX
remains `WSX://` plus JSON; public value-based `send_message` remains available.
Low-level Frame consumers migrate from `data=` to explicit `ControlPayload`
at control endpoints or `payload=bytes, info=...` at forwarding boundaries.
The explicit `send_serialized_message` API accepts `SerializedWsxPayload`,
so ordinary Python strings are never mistaken for already serialized values.

## Coordinated rollout and rollback

This work assigns no release version and changes no published bridge pin.
Review and release the core candidate first, then select the actual compatible
core version in the bridge's dependency range and publish the coordinated
bridge release. Test the exact pair in an isolated deployment. During rollout,
drain and stop the old frontend, templates and workers, install the pair, and
start a complete new generation. Do not mix old/new channel peers. Rollback
similarly stops the entire generation and restores both previous distributions;
no automatic replay or remote ownership transfer is part of rollback.

Global-store FIFO/lease/abort semantics and the bridge datetime policy remain
unchanged. Commanders consuming store values still register their codecs.
Remote group orchestration, fencing, freezer transfer, production cross-host
peer authentication and streaming/raw WebSocket hosting remain separate work.

## Verification evidence

The baseline core (`2465fcc47dadb1761ec1c09466c847715725daa9`, following release
v0.45.0) passed 1,977 tests. The final integrated candidate passed 2,062 tests (676 dependency
deprecation warnings, 143.74 seconds); an additional 25 targeted tests passed
after final contract annotation updates. Exact commits and logs are recorded
in the session handoff.

`test_opaque_spa_process.py` launches an actual frontend interpreter and its
SPA worker child. A custom TYTX value is registered only at the test client and
worker. HTTP responses, WSX requests/replies and worker push preserve its
serialized bytes; frontend codec spies remain at zero on those successful
forwarding paths. Control/error producers may serialize their own values.
`test_opaque_transport_processes.py` additionally proves two separate relay
processes on UDS and TCP. `test_remote_application.py` verifies generic factory
startup, independent process IDs, mount/Swagger, authorization, bytes/headers,
connect-only ownership, lifecycle restart and bounded termination. Channel and
remote-connection tests cover malformed/versioned frames, cancellation, limits,
concurrent correlation, link loss and no replay.

The bridge base `664dda96f6da7715af5eadbc993f3f0896c0c296` (v0.7.0) passed 258
tests against the baseline; 260 including the new WSGI boundary contracts
passed against the candidate. A private copy of a real legacy site passed
browser login, assets, customer RPC/query, cookie continuity, repeated
polling/datachanges envelopes and error recovery. The legacy browser has a
separate plain-JSON/XML websocket protocol and server-side gnrasync dependency;
it does not speak core WSX. Core WSX process tests do not establish legacy
browser websocket/push compatibility. See the bridge's issue72 acceptance
report for exact evidence. On 2026-09-08 the owner explicitly assigned legacy
WebSocket compatibility to a separate project; it is excluded from issue 72.

Performance methodology and measured results are in
[opaque_transport_benchmark.md](opaque_transport_benchmark.md). The byte-size
comparison is against an explicit base64-JSON serialization reference, not a
claim about the performance of the previous full server.


## Reply ownership after cancellation

Pending replies are matched to their route and connection owner before resolving
a caller; SPA verifies the route before applying worker metadata. A call that
may have been sent but whose caller timed out or cancelled reserves its ID until
the matching late reply arrives or its connection ends. This prevents the late
reply from completing a new call reusing that ID. Generic and SPA links retain
at most 256 such IDs; saturation closes that connection, rather than growing
memory without bound or silently forgetting an uncertain call. The channel hub
uses its configured pending-call bound for the same purpose. Cancellation while
a write is draining is conservatively treated as possibly sent. Ordinary SPA
cancellation does not kill its worker; only protocol failure, link loss or the
explicit bounded-capacity failure closes the wire.


## Docker process placement

`examples/remote_openapi/DOCKER.md` demonstrates the same app code inside a
non-root Linux container and the ordinary frontend outside it. Compose builds
from an explicit source allowlist and health-checks the KJNF readiness route.
The opt-in network listener is a bind option only; it grants no remote process
ownership or peer authentication. The proof script checks real HTTP and WSX,
1MiB raw bytes, failure isolation and reconnect after Docker stops/starts the
app. An optional ARM compatibility override is documented separately from the
portable base configuration.
