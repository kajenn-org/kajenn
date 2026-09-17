# Task thermometers — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
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

Claim anchors: [`TaskManager`](../../../src/kajenn/tasks/manager.py#L68), [`publish_progress`](../../../src/kajenn/tasks/manager.py#L160), [`EventHub`](../../../src/kajenn/tasks/hub.py#L47), [`TaskSpool`](../../../src/kajenn/tasks/spool.py#L116), [`request_cancel`](../../../src/kajenn/tasks/spool.py#L266), [`is_cancelled`](../../../src/kajenn/tasks/spool.py#L273).

## Source and test evidence

- [src/kajenn/tasks/manager.py](../../../src/kajenn/tasks/manager.py)
- [src/kajenn/tasks/spool.py](../../../src/kajenn/tasks/spool.py)
- [src/kajenn/tasks/hub.py](../../../src/kajenn/tasks/hub.py)
- [src/kajenn/mcp/engine.py](../../../src/kajenn/mcp/engine.py)
- [tests/core/test_task_manager.py](../../../tests/core/test_task_manager.py)
- [tests/core/test_task_spool.py](../../../tests/core/test_task_spool.py)
- [tests/core/test_event_hub.py](../../../tests/core/test_event_hub.py)
- [tests/core/test_mcp_push.py](../../../tests/core/test_mcp_push.py)
