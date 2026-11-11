# Soft and hard restart — current state

**Version**: 0.4 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## State, drain and uvicorn shutdown

The server distinguishes `RUNNING`, `QUITTING` and `STOPPING`. HTTP dispatch
refuses new work outside RUNNING; WebSocket handshake refusal applies before
accept to both WSX and raw applications. `Lifespan.shutdown` selects the
shutdown mode, drains registered requests bounded by `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (10.0),
then invokes application hooks in reverse order, then closes each database
handler.

`shutdown_timeout_seconds` (default 5.0) is passed to uvicorn's graceful
connection shutdown. It prevents an endless connection from indefinitely
postponing lifespan; it does not bound every application hook or pool thread.

The state no longer waits for that timeout. `serve()` boots `UvicornServer`, a
subclass owning the SIGINT/SIGTERM handlers: the first signal calls
`BaseServer.start_leaving` — state to `shutdown_mode` — and only then raises
uvicorn's exit flag, so uvicorn's three steps all run over a server that already
refuses new work. Two consequences, both deliberate (owner, 2026-09-12): a
request arriving on an already open connection during the graceful window reads
503 + `Retry-After` instead of being served, and under uvicorn's own `--reload`
the child process builds no `UvicornServer`, so there the ordered shutdown is
not guaranteed.

`BaseServer.leaving` is the awaitable half of that turn, set by the `state`
setter and never cleared. A source of an endless response reads its queue
through `BaseServer.get_until_leaving`, which answers `None` as soon as the
server starts leaving: the MCP push channel ends by itself, unsubscribing on
its own `finally`, instead of being cancelled when the grace runs out. Any
other endless source reads the same awaitable and ends the same way. Measured 2026-09-12 on a real
`kajenn serve` with an SSE stream open: SIGTERM to process exit, 0.36 s.

Claim anchors: [`Lifespan`](../../../src/kajenn/lifespan.py#Lifespan), [`Lifespan.shutdown`](../../../src/kajenn/lifespan.py#Lifespan.shutdown), [`shutdown_timeout_seconds`](../../../src/kajenn/server.py#BaseServer.shutdown_timeout_seconds), [`UvicornServer`](../../../src/kajenn/server.py#UvicornServer), [`start_leaving`](../../../src/kajenn/server.py#BaseServer.start_leaving), [`get_until_leaving`](../../../src/kajenn/server.py#BaseServer.get_until_leaving).

## The shutdown hook an application saves through

`on_shutdown` is where an application persists what must survive the stop, and
the reason it is told WHY the server is stopping: `kajenn.__main__.factory` selects
QUITTING for a reload child, so an application can save for a return and save
differently for a dry shutdown. The core calls the hook and carries the state;
what is written, and what is read back on the way up, belongs to whoever
implements it.

Session snapshots are separate and can preserve complete live sessions for a
named instance.

Claim anchors: [`on_shutdown`](../../../src/kajenn/application.py#BaseApplication.on_shutdown), [`factory`](../../../src/kajenn/__main__.py#factory), [`FACTORY_TARGET`](../../../src/kajenn/reloading.py#FACTORY_TARGET).

## Relaunch and stop of an external application's process

`SubprocessSpawner` starts the process of each external application as
`python -m kajenn serve <source> --role application:<code>`.

- `relaunch` starts the process of a role again. `KBusMixin` calls it when a
  joined member disconnects (`_member_lost`) and when a process ends without
  `stop` having asked (`on_exit` → `_relaunch_role`). No relaunch is asked while
  `kbus_stopping` is true.
- A relaunch first ends the previous process, then waits until the backoff has
  passed since the previous start. The backoff starts at `RELAUNCH_INTERVAL`
  (1.0 s), doubles at every relaunch up to `RELAUNCH_CEILING` (30.0 s), and
  starts over when the process stayed joined `RELAUNCH_RESET` (60.0 s).
  Attempts are not limited. A relaunch is never queued twice for one role.
- Every relaunch carries a new URL with a new token (`_mint_url`); the previous
  token stops admitting.
- `stop` cancels a pending relaunch, sends SIGTERM, and kills the process past
  `shutdown_timeout`. `KBusMixin` builds every spawner with
  `shutdown_timeout=self.shutdown_timeout_seconds` and stops all external
  processes before the lifespan shutdown message reaches the server
  (`_stop_children`).

Tests: `test_a_killed_process_is_relaunched`,
`test_shutdown_stops_the_spawned_process` in
`tests/core/test_external_application.py`;
`test_a_process_that_does_not_confirm_the_revocation_is_relaunched` in
`tests/core/test_revocation_across_processes.py`. No test exercises the
backoff values.

Claim anchors: [`SubprocessSpawner`](../../../src/kajenn/spawner.py#SubprocessSpawner), [`relaunch`](../../../src/kajenn/spawner.py#SubprocessSpawner.relaunch), [`_relaunch`](../../../src/kajenn/spawner.py#SubprocessSpawner._relaunch), [`stop`](../../../src/kajenn/spawner.py#SubprocessSpawner.stop), [`_stop_children`](../../../src/kajenn/kbus_mixin.py#KBusMixin._stop_children), [`_member_lost`](../../../src/kajenn/kbus_mixin.py#KBusMixin._member_lost), [`_relaunch_role`](../../../src/kajenn/kbus_mixin.py#KBusMixin._relaunch_role).

## Remaining restart target

The general owner-directed hard/soft restart ceremony, user notices,
administrative command and `execv` sequence are not delivered as a single
server command.

design.md lines 9 and 16 describe a delegation to `ServerApplication` and a
soft boot of the base. `src/kajenn/server_app/` holds no restart code: no
restart route, no `execv`, no soft boot. `grep -rni "execv\|notify_user\|soft_boot\|hard_restart" src/`
has no results. The earlier blanket claim that none of restart exists is
obsolete: the state/drain, reload survival and shutdown-hook pieces above are
implemented.

Target evidence: [recorded target](design.md); this paragraph records unresolved
design distance, not an executable contract.


## Source and test evidence

- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [src/kajenn/lifespan.py](../../../src/kajenn/lifespan.py)
- [src/kajenn/reloading.py](../../../src/kajenn/reloading.py)
- [src/kajenn/session/mixin.py](../../../src/kajenn/session/mixin.py)
- [src/kajenn/spawner.py](../../../src/kajenn/spawner.py)
- [src/kajenn/kbus_mixin.py](../../../src/kajenn/kbus_mixin.py)
- [tests/core/test_reloading.py](../../../tests/core/test_reloading.py)
- [tests/core/test_lifespan.py](../../../tests/core/test_lifespan.py)
- [tests/core/test_sse.py](../../../tests/core/test_sse.py)
- [tests/core/test_mcp_push.py](../../../tests/core/test_mcp_push.py)
- [tests/core/test_external_application.py](../../../tests/core/test_external_application.py)
