# Server — current state

**Version**: 0.5 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Composition and application ownership

`BaseServer` owns applications indexed by `code` and mount, the well-known
paths they declare, database handlers, one lazy `WorkPool`, `Lifespan`,
`RequestRegistry` and `WebSocketRegistry`.
`register_application` refuses duplicate codes and mounts before assigning the
application's exactly-once `server` property. It then indexes the
application's `active_well_known()` through `claim_well_known`. A well-known
path another application holds raises `ValueError`. An unknown `default` is a
boot error. The configured application set is assembled at construction.
No method removes an application: dynamic mount and unmount remain the design
target, not an implemented lifecycle.

`AsgiServer` composes `KBusMixin`, `AuthMixin`, `SessionMixin`,
`MiddlewareMixin`, `PluginMixin`, `StorageMixin` and `TaskMixin` over that base.
It builds its own configuration handler. It appends a `ServerApplication` under
`_server` when no application of code `_server` was declared. It appends none
when the constructor receives `role`: the dedicated process of an application
mounted with `spawner="subprocess"` hosts that application alone.
Explicit constructor kwargs override configured values per kwarg.
`AsgiServer` peels `site_name` and `site_home`; `site_home` becomes a
`SiteHome`, read as `server.site_home`.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#BaseServer), [`WorkPool`](../../../src/kajenn/pool.py#WorkPool), [`Lifespan`](../../../src/kajenn/lifespan.py#Lifespan), [`RequestRegistry`](../../../src/kajenn/request_registry.py#RequestRegistry), [`register_application`](../../../src/kajenn/server.py#BaseServer.register_application), [`claim_well_known`](../../../src/kajenn/server.py#BaseServer.claim_well_known), [`AsgiServer`](../../../src/kajenn/asgi_server.py#AsgiServer), [`_server` appended](../../../src/kajenn/asgi_server.py#AsgiServer.__init__), [`SiteHome`](../../../src/kajenn/site_home.py#SiteHome).

Behavior evidence: `tests/core/test_server_app_always_present.py::TestTheKwargsRoad::test_a_bare_server_has_the_management_application`, `test_an_application_in_its_dedicated_process_mounts_no_server_application`.

## Demux and HTTP lifecycle

`BaseServer.demux` reads the well-known paths first. A path matching
`WELL_KNOWN_PATTERN` (`/.well-known/...`, `/robots.txt`, `/favicon.ico`) goes to
the application that declared it, with `scope["path"]` set to `/<route>`. An
undeclared well-known path answers 404 "unimplemented well-known URL". Any
other first segment starting with `HIDDEN_SEGMENT_PREFIX` (`.`) answers 404, and
no application is reached. Then four branches apply: a matching first mount
segment with that segment stripped; otherwise the root application; otherwise
a 307 from `/` to the declared default, preserving the query; otherwise 404.
The D29 site index is still absent. A non-root unmatched path never redirects
to the default.

`BaseServer.__call__` refuses HTTP dispatch while state is not `RUNNING`, with
503 and `Retry-After: 5`. Such a refusal registers no request. The outer HTTP
middleware can answer before dispatch and therefore before that state check.
Accepted HTTP dispatch registers an item, runs LIFO cleanups in `finally`, and
unregisters it even on failure. `RequestRegistry.current` uses an instance
ContextVar; `WorkPool.run` copies its context to the executor thread. Its busy
gauge counts pending calls, so it may exceed the executor's thread count.

The `RequestRegistry` docstring names the server as the single writer. Four
call sites outside `BaseServer.__call__` also register items: the WSX message
loop, `serve_kbus_message`, `_serve_http_stream` and the task executor.

Claim anchors: [`BaseServer`](../../../src/kajenn/server.py#BaseServer), [`demux`](../../../src/kajenn/server.py#BaseServer.demux), [`demux_well_known`](../../../src/kajenn/server.py#BaseServer.demux_well_known), [`WELL_KNOWN_PATTERN`](../../../src/kajenn/well_known.py#WELL_KNOWN_PATTERN), [`HIDDEN_SEGMENT_PREFIX`](../../../src/kajenn/well_known.py#HIDDEN_SEGMENT_PREFIX), [`RequestRegistry`](../../../src/kajenn/request_registry.py#RequestRegistry), [`current`](../../../src/kajenn/request_registry.py#RequestRegistry.current), [`WorkPool`](../../../src/kajenn/pool.py#WorkPool).

Registry writers: [`wsx.py`](../../../src/kajenn/wsx.py#WsxConnection._serve_message), [`serve_kbus_message`](../../../src/kajenn/kbus_mixin.py#KBusMixin.serve_kbus_message), [`_serve_http_stream`](../../../src/kajenn/kbus_mixin.py#KBusMixin._serve_http_stream), [`tasks/executor.py`](../../../src/kajenn/tasks/executor.py#LocalTaskExecutor.execute).

Behavior evidence: `tests/core/test_well_known.py`, `tests/core/test_hidden_paths.py`, `tests/core/test_well_known_external.py`.

## Server-owned databases

`add_database(name, params, handler_class)` mounts a database under its owner
rule name (`foo` for the server, `chat:foo` for the application `chat`). One
handler instance is built per handler class and reused. A claimed name and a
database with no handler class raise `ValueError`. `database_handler` refuses
an unknown name with `LookupError` and a handler without the asked capability
with `ValueError`. `take_database` gives each name at most once per registered
call; the first take queues `release_databases` as an async cleanup run on a
pool thread. A take outside a registered call raises `RuntimeError`.
`AsgiServer` registers the configured databases after the cooperative chain.
A database owned by an application this process does not serve is skipped.

Claim anchors: [`add_database`](../../../src/kajenn/server.py#BaseServer.add_database), [`database_handler`](../../../src/kajenn/server.py#BaseServer.database_handler), [`take_database`](../../../src/kajenn/server.py#BaseServer.take_database), [`release_databases`](../../../src/kajenn/server.py#BaseServer.release_databases), [`_register_configured_databases`](../../../src/kajenn/asgi_server.py#AsgiServer._register_configured_databases).

Behavior evidence: `tests/core/test_db.py::test_add_database_reuses_the_handler_and_refuses_a_claimed_name`, `test_a_take_outside_a_registered_call_is_an_error`, `test_an_application_in_its_dedicated_process_mounts_the_server_databases_and_its_own`.

## Sync work and the server loop

`run_sync` dispatches a blocking callable onto the pool and records the
running loop. `run_on_loop(coro)` runs a coroutine on that loop from a pool
thread and returns its result. Called on the loop thread, it closes the
coroutine and raises `RuntimeError`. Called before any `run_sync`, it raises
`RuntimeError` too.

Claim anchors: [`run_sync`](../../../src/kajenn/server.py#BaseServer.run_sync), [`run_on_loop`](../../../src/kajenn/server.py#BaseServer.run_on_loop).

Behavior evidence: `tests/core/test_authentication_on_demand.py::test_a_pool_thread_runs_a_coroutine_on_the_loop`, `test_run_on_loop_refuses_the_loop_thread`.

## WebSocket dispatch and page messages

`on_websocket` is implemented. It first refuses a non-running server before
accept, for both WSX and raw applications. At a running server, an application
with `serve_websocket` receives the raw ASGI triple and the mount-relative path.
Otherwise `WsxConnection.serve` owns the handshake and the message loop.
`send_message(page_id, path, data)` addresses a bound page; `True` means a write
to the socket, not execution by the client.

See [WebSocket status](../055_websocket/status.md) for Origin, identity,
registration and concurrency rules.

Claim anchors: [`on_websocket`](../../../src/kajenn/server.py#BaseServer.on_websocket), [`send_message`](../../../src/kajenn/server.py#BaseServer.send_message).

## Startup and bounded shutdown

`Lifespan.startup` first opens every database handler. A handler whose `open`
raises becomes a `FatalBootError`. Then it runs application hooks in
registration order. Ordinary hook exceptions are logged and isolated.
`FatalBootError` on startup, from a hook or from a handler `open`, closes the
handlers already opened, emits `lifespan.startup.failed` and stops the
sequence. Shutdown changes the state to `shutdown_mode` when it is still
running, awaits the request registry drain for at most
`SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (10 seconds), then runs hooks in reverse.
After the hooks it closes every database handler; a `close` that raises is
logged and the next one runs. An already chosen `QUITTING` or `STOPPING` state
is preserved.

`BaseServer.serve` passes `shutdown_timeout_seconds` (default 5.0) to uvicorn's
`timeout_graceful_shutdown`. This bounds uvicorn's connection wait before the
lifespan hooks run; it is separate from the registry drain and is not a total
process-exit deadline. The pool shutdown still waits for executor work.
`AsgiServer.serve` resolves explicit host/port, configured values, then
`127.0.0.1` and port 0. `debug` records a usage mode; the core does not branch on it.

Claim anchors: [`Lifespan`](../../../src/kajenn/lifespan.py#Lifespan), [`FatalBootError`](../../../src/kajenn/lifespan.py#FatalBootError), [`lifespan`](../../../src/kajenn/server.py#BaseServer.lifespan), [`BaseServer`](../../../src/kajenn/server.py#BaseServer), [`shutdown_timeout_seconds`](../../../src/kajenn/server.py#BaseServer.shutdown_timeout_seconds), [`AsgiServer`](../../../src/kajenn/asgi_server.py#AsgiServer).

Behavior evidence: [`startup`](../../../src/kajenn/lifespan.py#Lifespan.startup), [`shutdown`](../../../src/kajenn/lifespan.py#Lifespan.shutdown), [`_run_hook`](../../../src/kajenn/lifespan.py#Lifespan._run_hook), `tests/core/test_db.py::test_a_failed_open_stops_the_startup_and_closes_what_was_opened`, `tests/core/test_lifespan.py::test_a_fatal_startup_failure_stops_the_server`.

## External applications over kbus

Modules: `kbus_mixin.py`, `spawner.py`, `remote_application.py`,
`http_record.py`, `transport_limits.py`.

- **`KBusMixin`** is the first mixin of `AsgiServer`. It peels `role`,
  `kbus_source` and `kbus`.
- **External applications.** `external_applications` lists the mounted
  applications that carry a `spawner`. An application declared with `spawner`
  is built as a `RemoteApplication`.
- **Lifespan with external applications.** When at least one exists, the
  lifespan runs inside `_serve_with_children`. It starts a `kbus.Dispatcher` on
  the `server.kbus` address, or on a unix socket in a fresh 0700 directory.
  It connects a member named `server`. It asks each spawner to `ensure` the
  process `application:<code>` with a fresh URL, without waiting for the join.
  Before the lifespan ending message reaches the server, it stops the processes
  and closes the dispatcher.
- **Join.** A spawned process calls `server.joined` with its well-known paths
  and its pid. `_external_joined` re-indexes those paths through
  `claim_well_known` and records the pid. A malformed `well_known` value and a
  path another application holds are refused with `kbus.Refused`.
- **503 until joined.** `RemoteApplication` answers 503 while its process has
  not joined, and when the process is lost or the request passes
  `request_timeout`.
- **Relaunch.** A member lost while the server runs, and a process that ends
  unrequested, are handed back to the spawner for a relaunch with a new URL.
- **`run_role`** is the life of an application's dedicated process. It opens no
  HTTP listener. It pops `KAJENN_KBUS_PARENT`, runs the lifespan startup,
  connects its member, calls `server.joined`, then waits for SIGTERM, SIGINT or
  the loss of the connection, and runs the shutdown.
- **`SubprocessSpawner`** runs each process as
  `python -m kajenn serve <source> --role <role>`. The URL travels in
  `KAJENN_KBUS_PARENT`, not on the command line. The relaunch backoff starts at
  `RELAUNCH_INTERVAL` (1 s), doubles up to `RELAUNCH_CEILING` (30 s), and
  starts over when the process stayed joined `RELAUNCH_RESET` (60 s). Attempts
  are not limited. `stop` cancels a pending relaunch, terminates the process
  and kills it past `shutdown_timeout`. No test in `tests/` names the three
  relaunch constants.
- **`HttpRecord`** is the binary frame of a forwarded request: `b"HTTP"`, a
  version byte, the metadata length, the metadata as ascii JSON, then the body
  bytes. It encodes an http scope with its body, a response, and a websocket
  scope.
- **`transport_limits`** reads the limits from the environment:
  `KAJENN_FRAME_MAX_BYTES` (default 256 MiB) and `KAJENN_HTTP_MAX_BODY_BYTES`
  (default the frame ceiling). `DEFAULT_WARN_FRAME_SIZE` (1 MiB) has no
  consumer in `src/`.

Claim anchors: [`KBusMixin`](../../../src/kajenn/kbus_mixin.py#KBusMixin), [`external_applications`](../../../src/kajenn/kbus_mixin.py#KBusMixin.external_applications), [`_serve_with_children`](../../../src/kajenn/kbus_mixin.py#KBusMixin._serve_with_children), [`_external_joined`](../../../src/kajenn/kbus_mixin.py#KBusMixin._external_joined), [`run_role`](../../../src/kajenn/kbus_mixin.py#KBusMixin.run_role), [`RemoteApplication`](../../../src/kajenn/remote_application.py#RemoteApplication), [`SubprocessSpawner`](../../../src/kajenn/spawner.py#SubprocessSpawner), [`stop`](../../../src/kajenn/spawner.py#SubprocessSpawner.stop), [`HttpRecord`](../../../src/kajenn/http_record.py#HttpRecord), [`frame_max_size`](../../../src/kajenn/transport_limits.py#frame_max_size), [`DEFAULT_WARN_FRAME_SIZE`](../../../src/kajenn/transport_limits.py#DEFAULT_WARN_FRAME_SIZE), [`RemoteApplication` built](../../../src/kajenn/asgi_server.py#AsgiServer._configured_kwargs).

Behavior evidence: `tests/core/test_external_application.py::test_a_mount_whose_process_has_not_joined_answers_503`, `test_a_killed_process_is_relaunched`, `tests/core/test_well_known_external.py::test_a_join_presenting_a_held_path_is_refused`, `tests/core/test_http_record.py`.

## Remaining design distance

The configuration handler has no general `apply_configuration` mutator or
subscribers that mount/unmount applications. A narrower apply mechanism owned
by one application over its own subtree does not deliver that server-wide
design. Historical D7's empty
socket and August coverage figures describe earlier revisions, not this one.

- **Installed set changed while running.** design.md describes an installed
  set that is not sealed. The code has no method that removes an application.
  The `register_application` docstring states that the set is fixed at
  construction ([server.py](../../../src/kajenn/server.py#BaseServer.register_application)).
- **Internal server.** design.md describes an internal server composed without
  the authentication layer. No such class exists: `AsgiServer` is the only
  shipped composition, and it includes `AuthMixin`
  ([asgi_server.py](../../../src/kajenn/asgi_server.py#AsgiServer)).
- **Server with a parent link.** design.md describes the public server with a
  parent link armed. No constructor takes `parent=`. The code has `role=` and
  `KBusMixin`, which link an application's dedicated process to its server
  ([kbus_mixin.py](../../../src/kajenn/kbus_mixin.py#KBusMixin.__init__)).
- **Removable while running.** design.md describes an application that declares
  whether it can be removed while running. `BaseApplication` has no attribute
  for it ([application.py](../../../src/kajenn/application.py#BaseApplication)).
- **Site index.** design.md describes a site index answering `/`. `demux`
  answers 404 there ([server.py](../../../src/kajenn/server.py#BaseServer.demux)).

Target evidence: [recorded target](decisions.md); this section records unresolved
design distance, not an executable contract.


## Source and test evidence

- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn/lifespan.py](../../../src/kajenn/lifespan.py)
- [src/kajenn/pool.py](../../../src/kajenn/pool.py)
- [src/kajenn/request_registry.py](../../../src/kajenn/request_registry.py)
- [src/kajenn/well_known.py](../../../src/kajenn/well_known.py)
- [src/kajenn/kbus_mixin.py](../../../src/kajenn/kbus_mixin.py)
- [src/kajenn/spawner.py](../../../src/kajenn/spawner.py)
- [src/kajenn/remote_application.py](../../../src/kajenn/remote_application.py)
- [src/kajenn/http_record.py](../../../src/kajenn/http_record.py)
- [src/kajenn/transport_limits.py](../../../src/kajenn/transport_limits.py)
- [tests/core/test_contract.py](../../../tests/core/test_contract.py)
- [tests/core/test_demux.py](../../../tests/core/test_demux.py)
- [tests/core/test_hidden_paths.py](../../../tests/core/test_hidden_paths.py)
- [tests/core/test_well_known.py](../../../tests/core/test_well_known.py)
- [tests/core/test_lifespan.py](../../../tests/core/test_lifespan.py)
- [tests/core/test_db.py](../../../tests/core/test_db.py)
- [tests/core/test_pool.py](../../../tests/core/test_pool.py)
- [tests/core/test_request_registry.py](../../../tests/core/test_request_registry.py)
- [tests/core/test_websocket_raw_seam.py](../../../tests/core/test_websocket_raw_seam.py)
- [tests/core/test_external_application.py](../../../tests/core/test_external_application.py)
- [tests/core/test_http_record.py](../../../tests/core/test_http_record.py)
