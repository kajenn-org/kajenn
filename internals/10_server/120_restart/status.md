# Soft and hard restart — current state

**Version**: 0.3 · **Last Updated**: 2026-09-12 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## State, drain and uvicorn shutdown

The server distinguishes `RUNNING`, `QUITTING` and `STOPPING`. HTTP dispatch
refuses new work outside RUNNING; WebSocket handshake refusal applies before
accept to both WSX and raw applications. `Lifespan.shutdown` selects the
shutdown mode, drains registered requests with a 10-second bound, then invokes
application hooks in reverse order.

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
server starts leaving: the MCP push channel and the SPA inspector stream both
end by themselves, unsubscribing on their own `finally`, instead of being
cancelled when the grace runs out. Measured 2026-09-12 on a real
`kajenn serve` with an SSE stream open: SIGTERM to process exit, 0.36 s.

Claim anchors: [`Lifespan`](../../../src/kajenn/lifespan.py#L89), [`shutdown_timeout_seconds`](../../../src/kajenn/server.py#L402), [`UvicornServer`](../../../src/kajenn/server.py#L104).

## SPA save and lazy restoration

`SpaApplication.on_shutdown` calls the commander's `quit` path for QUITTING and
`stop` for a dry shutdown. `SpaCommander.quit` gathers users into reboot state
and persists its routing registers. `adopt_frozen_registers` restores frozen
placement information; users wake lazily through the usual request path.
The global store is not part of that restored state.

`reloading.factory` selects QUITTING for a reload child. Session snapshots are
separate and can preserve complete live sessions for a named instance.

Claim anchors: [`SpaApplication`](../../../src/kajenn_orchestra/spa_app.py#L517), [`on_shutdown`](../../../src/kajenn_orchestra/spa_app.py#L1002), [`quit`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L1710), [`SpaCommander`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L494), [`adopt_frozen_registers`](../../../src/kajenn_orchestra/orchestration/spa_commander.py#L1642).

## Remaining restart target

The general owner-directed hard/soft restart ceremony, user notices,
administrative command and `execv` sequence are not delivered as a single
server command. The earlier blanket claim that none of restart exists is
obsolete: the state/drain, reload survival and SPA persistence pieces above
are implemented. Kubernetes and subcommander reconstruction remain proposals.

Target evidence: [recorded target](design.md); this paragraph records unresolved
design distance, not an executable contract.


## Source and test evidence

- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [src/kajenn/lifespan.py](../../../src/kajenn/lifespan.py)
- [src/kajenn/reloading.py](../../../src/kajenn/reloading.py)
- [src/kajenn/session/mixin.py](../../../src/kajenn/session/mixin.py)
- [src/kajenn_orchestra/spa_app.py](../../../src/kajenn_orchestra/spa_app.py)
- [src/kajenn_orchestra/orchestration/spa_commander.py](../../../src/kajenn_orchestra/orchestration/spa_commander.py)
- [tests/core/test_reloading.py](../../../tests/core/test_reloading.py)
- [tests/core/test_lifespan.py](../../../tests/core/test_lifespan.py)
- [tests/core/test_sse.py](../../../tests/core/test_sse.py)
- [tests/core/test_mcp_push.py](../../../tests/core/test_mcp_push.py)
- [tests/spa/test_inspector_section.py](../../../tests/spa/test_inspector_section.py)
- [tests/spa/orchestration/test_orchestration_no_speculative_birth.py](../../../tests/spa/orchestration/test_orchestration_no_speculative_birth.py)
- [tests/spa/orchestration/test_orchestration_foundations_e2e.py](../../../tests/spa/orchestration/test_orchestration_foundations_e2e.py)
- [tests/spa/orchestration/test_orchestration_spa_commander.py](../../../tests/spa/orchestration/test_orchestration_spa_commander.py)
