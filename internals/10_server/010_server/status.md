# Server — current state

**Version**: 0.4 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Composition and application ownership

`BaseServer` owns applications indexed by `code` and mount, database handlers,
one lazy `WorkPool`, `Lifespan`, `RequestRegistry` and `WebSocketRegistry`.
`register_application` refuses duplicate codes and mounts before assigning the
application's exactly-once `server` property. An unknown `default` is a boot error.
The configured application set is assembled at construction; dynamic mount and
unmount remain the design target, not an implemented lifecycle.

`AsgiServer` composes communication, authentication, sessions, middleware,
plugins, storage and tasks over that base. It builds its own configuration
handler and automatically registers `ServerApplication` under `_server`.
Explicit constructor kwargs override configured values per kwarg.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L86), [`WorkPool`](../../../src/kajenn/pool.py#L41), [`Lifespan`](../../../src/kajenn/lifespan.py#L89), [`RequestRegistry`](../../../src/kajenn/request_registry.py#L124), [`register_application`](../../../src/kajenn/server.py#L179), [`AsgiServer`](../../../src/kajenn/asgi_server.py#L91).

## Demux and HTTP lifecycle

`BaseServer.demux` applies four branches: a matching first mount segment with
that segment stripped; otherwise the root application; otherwise a 307 from
`/` to the declared default, preserving the query; otherwise 404. The D29 site
index is still absent. A non-root unmatched path never redirects to the default.

`BaseServer.__call__` refuses HTTP dispatch while state is not `RUNNING`, with
503 and `Retry-After: 5`. Such a refusal registers no request. The outer HTTP
middleware can answer before dispatch and therefore before that state check.
Accepted HTTP dispatch registers an item, runs LIFO cleanups in `finally`, and
unregisters it even on failure. `RequestRegistry.current` uses an instance
ContextVar; `WorkPool.run` copies its context to the executor thread. Its busy
gauge counts pending calls, so it may exceed the executor's thread count.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#L86), [`demux`](../../../src/kajenn/server.py#L325), [`RequestRegistry`](../../../src/kajenn/request_registry.py#L124), [`current`](../../../src/kajenn/request_registry.py#L144), [`WorkPool`](../../../src/kajenn/pool.py#L41).

## WebSocket dispatch and page messages

`on_websocket` is implemented. It first refuses a non-running server before
accept, for both WSX and raw applications. At a running server, an application
with `serve_websocket` receives the raw ASGI triple and the mount-relative path.
Otherwise `WsxConnection.serve` owns the handshake and the message loop.
`send_message(page_id, path, data)` addresses a bound page; `True` means a write
to the socket, not execution by the client.

See [WebSocket status](../055_websocket/status.md) for Origin, identity,
registration and concurrency rules.

Claim anchors: [`on_websocket`](../../../src/kajenn/server.py#L365), [`send_message`](../../../src/kajenn/server.py#L248).

## Startup and bounded shutdown

`Lifespan.startup` runs application hooks in registration order. Ordinary hook
exceptions are logged and isolated; `FatalBootError` on startup instead emits
`lifespan.startup.failed` and stops the sequence. Shutdown changes the state to
`shutdown_mode` when it is still running, awaits the request registry drain for
at most `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (10 seconds), then runs hooks in reverse.
An already chosen `QUITTING` or `STOPPING` state is preserved.

`BaseServer.serve` passes `shutdown_timeout_seconds` (default 5.0) to uvicorn's
`timeout_graceful_shutdown`. This bounds uvicorn's connection wait before the
lifespan hooks run; it is separate from the registry drain and is not a total
process-exit deadline. The pool shutdown still waits for executor work.
`AsgiServer.serve` resolves explicit host/port, configured values, then
`127.0.0.1` and port 0. `debug` records a usage mode; the core does not branch on it.

Claim anchors: [`Lifespan`](../../../src/kajenn/lifespan.py#L89), [`FatalBootError`](../../../src/kajenn/lifespan.py#L78), [`lifespan`](../../../src/kajenn/server.py#L208), [`BaseServer`](../../../src/kajenn/server.py#L86), [`shutdown_timeout_seconds`](../../../src/kajenn/server.py#L402), [`AsgiServer`](../../../src/kajenn/asgi_server.py#L91).

Behavior evidence: [`startup`](../../../src/kajenn/lifespan.py#L116), [`shutdown`](../../../src/kajenn/lifespan.py#L121), [`_run_hook`](../../../src/kajenn/lifespan.py#L147).

## Remaining design distance

The configuration handler has no general `apply_configuration` mutator or
subscribers that mount/unmount applications. A narrower apply mechanism owned
by one application over its own subtree does not deliver that server-wide
design. Historical D7's empty
socket and August coverage figures describe earlier revisions, not this one.

Target evidence: [recorded target](decisions.md); this paragraph records unresolved
design distance, not an executable contract.


## Source and test evidence

- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn/lifespan.py](../../../src/kajenn/lifespan.py)
- [src/kajenn/pool.py](../../../src/kajenn/pool.py)
- [src/kajenn/request_registry.py](../../../src/kajenn/request_registry.py)
- [tests/core/test_contract.py](../../../tests/core/test_contract.py)
- [tests/core/test_demux.py](../../../tests/core/test_demux.py)
- [tests/core/test_lifespan.py](../../../tests/core/test_lifespan.py)
- [tests/core/test_pool.py](../../../tests/core/test_pool.py)
- [tests/core/test_request_registry.py](../../../tests/core/test_request_registry.py)
- [tests/core/test_websocket_raw_seam.py](../../../tests/core/test_websocket_raw_seam.py)
