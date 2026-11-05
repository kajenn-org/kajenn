# Task thermometers — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Snapshot and live progress

`TaskManager.publish_progress(task_id, data)` requires an active task, writes
`progress.json` through its spool and publishes the same data to the descriptor's
launching session when one exists. `EventHub` fans out through bounded queues,
dropping oldest intermediate events for a slow subscriber. No subscribers means
no live publication, while the spool snapshot remains available.

`TaskSpool.request_cancel` writes a marker and `is_cancelled` reads it. A batch
must poll and honor that signal itself; the progress API does not interrupt it.
The folder's terminal position records the outcome. SSE push tests cover the
snapshot/live boundary; there is no durable per-event replay log in the hub.

Claim anchors: [`TaskManager`](../../../src/kajenn/tasks/manager.py#L67), [`publish_progress`](../../../src/kajenn/tasks/manager.py#L160), [`EventHub`](../../../src/kajenn/tasks/hub.py#L47), [`TaskSpool`](../../../src/kajenn/tasks/spool.py#L115), [`request_cancel`](../../../src/kajenn/tasks/spool.py#L265), [`is_cancelled`](../../../src/kajenn/tasks/spool.py#L272).

## Hub queue bound

`QUEUE_MAXSIZE` is `256` events per subscriber queue (`src/kajenn/tasks/hub.py:44`).
A full queue drops its oldest event before the new one is put
(`src/kajenn/tasks/hub.py:81-90`).

Tests: `tests/core/test_event_hub.py::TestBackpressure::test_full_queue_drops_oldest`.

## MCP push channel: subscriber and `Last-Event-ID` baseline

The hub subscriber is the GET branch of `McpTransport` in
`src/kajenn/applications/mcp.py`, not `src/kajenn/mcp/engine.py`.
`McpTransport.open_stream` (`src/kajenn/applications/mcp.py:243-260`) opens the
SSE stream for one `Mcp-Session-Id`. With a `Last-Event-ID` header,
`_event_source` first replays the current `progress.json` of the session's
pending and active tasks (`_baseline`, `src/kajenn/applications/mcp.py:300-316`),
then follows the hub queue. Settled tasks are not replayed. The engine only
advertises `experimental.push` in its `initialize` answer
(`src/kajenn/mcp/engine.py:99-101`).

A server with tasks disabled answers the GET with 405
(`src/kajenn/applications/mcp.py:253-254`).

Tests: `tests/core/test_mcp_push.py::TestSessionId::test_get_without_tasks_is_405`,
`::TestBaseline::test_reconnect_replays_snapshot`, `::TestBaseline::test_fresh_connect_has_no_baseline`,
`::TestInitializeAdvertisesPush::test_capability_advertised`.

## Distance from design.md

- The stop request read by the batch. design.md lines 10-11 say the batch reads
  the stop request at its own pace. `TaskSpool.is_cancelled` has no caller in
  `src/`. `LocalTaskExecutor.execute` calls the handler with the stored params
  only (`src/kajenn/tasks/executor.py:124-127`): the handler does not receive
  its `task_id`, so it cannot ask the spool about its own marker
  (kajenn-org/kajenn#4).
- The `stopping` state. design.md lines 11-12 say an intermediate `stopping`
  state stays visible. `STATUSES` is `pending`, `active`, `terminated`,
  `aborted` (`src/kajenn/tasks/spool.py:68`). No state records a stop request
  in progress.
- Progress from a running batch. design.md line 24 draws a running batch
  calling `TaskManager.publish_progress`. Only tests call it
  (`tests/core/test_mcp_push.py`, `tests/server_app/test_tasks_section.py`);
  no code in `src/` does. A handler without
  its `task_id` has no argument to pass.

## Source and test evidence

- [src/kajenn/tasks/manager.py](../../../src/kajenn/tasks/manager.py)
- [src/kajenn/tasks/spool.py](../../../src/kajenn/tasks/spool.py)
- [src/kajenn/tasks/hub.py](../../../src/kajenn/tasks/hub.py)
- [src/kajenn/applications/mcp.py](../../../src/kajenn/applications/mcp.py)
- [src/kajenn/mcp/engine.py](../../../src/kajenn/mcp/engine.py)
- [tests/core/test_task_manager.py](../../../tests/core/test_task_manager.py)
- [tests/core/test_task_spool.py](../../../tests/core/test_task_spool.py)
- [tests/core/test_event_hub.py](../../../tests/core/test_event_hub.py)
- [tests/core/test_mcp_push.py](../../../tests/core/test_mcp_push.py)
