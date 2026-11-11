# Tasks — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Server-owned task manager

`TaskMixin` lazily creates `TaskManager` and starts it around ASGI lifespan.
It is enabled by default in `AsgiServer`; `tasks=False` disables it. This is a
capability on the server, not a separately mounted task application.
`/_server/tasks` is its administration surface.

`TaskScheduler` discovers task metadata in mounted routing trees and runs due
schedules from `FileTaskStore`. A duplicate task name is excluded; an orphaned
record is retained without execution. Each schedule avoids overlap with its own
running call. Runs are system calls without HTTP middleware or caller auth
filters. `every`, `cron` and `at` schedules compute future instants without
backfilling downtime.

Claim anchors: [`TaskMixin`](../../../src/kajenn/tasks/mixin.py#TaskMixin), [`TaskManager`](../../../src/kajenn/tasks/manager.py#TaskManager), [`tasks`](../../../src/kajenn/tasks/mixin.py#TaskGrammar.tasks), [`tasks`](../../../src/kajenn/tasks/mixin.py#TaskMixin.tasks), [`TaskScheduler`](../../../src/kajenn/tasks/scheduler.py#TaskScheduler).

## The `tasks` configuration element

`server(...).tasks(enabled, tick_seconds, mount)` is declared by `TaskGrammar`
(`src/kajenn/tasks/mixin.py:68-86`) and composed into `AsgiServerGrammar`
(`src/kajenn/config/elements.py:83`). Its attributes reach `TaskMixin` as the
`tasks=` dict. `enabled` defaults to `True`. `tick_seconds` replaces the
scheduler tick (`src/kajenn/tasks/manager.py:94-95`). `mount` replaces the
`site` mount of `FileTaskStore` (`src/kajenn/tasks/store.py:150-153`). The
element accepts no child.

Tests: `tests/core/test_config.py::TestTasksConfig::test_tasks_disabled_via_recipe`,
`::test_tuning_reaches_scheduler_and_store`, `::test_a_child_under_tasks_is_rejected_by_the_grammar`.

## Scheduler tick, default schedules and run log

`TICK_SECONDS` is `30.0` (`src/kajenn/tasks/scheduler.py:61`). A route that
declares `task_every` or `task_cron` in its metadata gets a code-default record
whose `code` is the task name (`sync_defaults`, `src/kajenn/tasks/scheduler.py:165-200`).
An existing record wins over the code default. A route declaring both
`task_every` and `task_cron` is logged as an error and gets no record.
`run_now(code)` fires a schedule at once and answers `started`, `done` or
`running` (`src/kajenn/tasks/scheduler.py:276-309`). Every run appends one line
to the JSONL log `tasks/logs/<task_name>.jsonl`, capped at `LOG_CAP = 200`
lines (`src/kajenn/tasks/store.py:62`).

Tests: `tests/core/test_task_scheduler.py::TestScan::test_duplicate_task_name_excluded`,
`::TestTick::test_no_overlap`, `::TestTick::test_orphan_record_never_runs`,
`::TestRunNow::test_run_now_inline_when_no_loop`, `::TestRunNow::test_run_now_started_on_live_loop`,
`::TestRunNow::test_run_now_running_skips`.

## `TaskStore` contract and `FileTaskStore`

`TaskStore` is the contract of the schedule records (`src/kajenn/tasks/store.py:65`).
`FileTaskStore` implements it with one JSON file per record at
`<mount>:tasks/<code>.json` (`src/kajenn/tasks/store.py:127-165`). Records are
written plain, not encrypted.

Tests: `tests/core/test_task_store.py::TestMountChoice::test_default_mount_is_site`,
`::TestMountChoice::test_explicit_mount_override_wins`.

## Batch spool and execution

`TaskSpool` stores pending, active, terminated and aborted task folders on
storage. `LocalTaskExecutor` resolves mount plus node path against the live
server and settles the folder once. It owns no pool. An async handler runs on
the loop. A sync handler runs through `server.run_sync`, which hands it to the
server's `WorkPool` (`src/kajenn/tasks/executor.py:124-127`,
`src/kajenn/server.py:398-407`). The single logical executor worker is `local`.
Relaunching a settled batch requires a new id; distributed batch execution is
not supplied by this core task manager.

The worker loop polls `pending/` every `POLL_SECONDS = 0.5`
(`src/kajenn/tasks/manager.py:64`, `:134-141`). It claims each pending task with
`assign` and launches `execute` as its own loop task.

No code in `src/` creates a batch. `TaskSpool.create` and `new_descriptor` have
no caller in `src/`. Their callers are in `tests/core/test_task_spool.py`,
`tests/core/test_task_executor.py`, `tests/core/test_task_manager.py`,
`tests/core/test_mcp_push.py`, `tests/core/test_db.py` and
`tests/server_app/test_tasks_section.py`.

`TaskManager.publish_progress` pairs the spool snapshot with live session-keyed
hub publication. `TasksSection.cancel` writes the spool `cancel` marker through
`TaskSpool.request_cancel` (`src/kajenn/server_app/server_sections/tasks_section.py:240`).
`TaskSpool.is_cancelled` has no caller in `src/`. No running task reads the
marker, and the task runs to its end (kajenn-org/kajenn#4).

A task folder left under `active/` by a worker that stopped is never settled.
The only `settle` call in `src/` is in `LocalTaskExecutor.execute`
(`src/kajenn/tasks/executor.py:137`). No code sweeps `active/` at boot
(kajenn-org/kajenn#4).

Claim anchors: [`TaskSpool`](../../../src/kajenn/tasks/spool.py#TaskSpool), [`LocalTaskExecutor`](../../../src/kajenn/tasks/executor.py#LocalTaskExecutor), [`TaskManager`](../../../src/kajenn/tasks/manager.py#TaskManager), [`publish_progress`](../../../src/kajenn/tasks/manager.py#TaskManager.publish_progress).

## Task runs in the request registry

`LocalTaskExecutor.execute` registers each run in `server.requests` as an item
with scope type `task` and path `<mount>/<node_path>`
(`src/kajenn/tasks/executor.py:119-121`). The cleanups run in a nested
`try`/`finally`, and `unregister` runs after them in every case
(`src/kajenn/tasks/executor.py:132-136`). A database taken by the handler is
released at the end of the run.

Tests: `tests/core/test_db.py::TestRelease::test_a_task_releases`.

## Lifecycle events `started` and `settled`

`LocalTaskExecutor` publishes `{"type": "started"}` before the run and
`{"type": "settled", "outcome", "error"}` after it on `server.tasks.hub`
(`src/kajenn/tasks/executor.py:118`, `:138`). Each event carries the `task_id`.
A descriptor with `session_id` `None` publishes nothing.

Tests: `tests/core/test_mcp_push.py::TestExecutorBridge::test_lifecycle_events_stream`,
`::test_no_session_no_publish`.

## `/_server/tasks` routes

`TasksSection` declares 12 routes, all `auth_rule="SUPERADMIN"`
(`src/kajenn/server_app/server_sections/tasks_section.py:82-253`):

- schedules: `list`, `create`, `update`, `enable`, `disable`, `run_now`, `delete`, `logs`;
- spool: `spool_list`, `progress`, `cancel`, `result`.

A server with `tasks=False` answers every route with `{"error": "Tasks are disabled"}`
and status 200 (`src/kajenn/server_app/server_sections/tasks_section.py:52`).

Tests: [tests/server_app/test_tasks_section.py](../../../tests/server_app/test_tasks_section.py).

## Cancellation of a sync handler on the pool

A sync task handler runs through `WorkPool.run` (`src/kajenn/pool.py:103-146`).
When the caller is cancelled, work still queued is withdrawn. Work already
running is awaited to its end, and then `CancelledError` is raised again. An
exception raised by that work is logged on `kajenn.pool`.

The running work is awaited with `asyncio.wait`, not `asyncio.shield`, so the
late exception is reported once, on the `kajenn.pool` log (commit 2d396e0).

Tests: `tests/core/test_pool.py::TestCancellation`. CI runs Python 3.11 only.

## Distance from design.md

- Spooled runs. design.md line 5 names "spooled runs that must survive and be
  accounted for". The code has no sender: `TaskSpool.create` and
  `new_descriptor` have no caller in `src/` (search in "Batch spool and
  execution" above).
- Runs accounted for after a stop. design.md line 5 asks that runs be
  accounted for. A folder left under `active/` by a worker that stopped stays
  there: no code settles it at boot (`src/kajenn/tasks/executor.py:137` is the
  only `settle` call in `src/`; kajenn-org/kajenn#4).

## Source and test evidence

- [src/kajenn/tasks/mixin.py](../../../src/kajenn/tasks/mixin.py)
- [src/kajenn/tasks/manager.py](../../../src/kajenn/tasks/manager.py)
- [src/kajenn/tasks/scheduler.py](../../../src/kajenn/tasks/scheduler.py)
- [src/kajenn/tasks/schedule.py](../../../src/kajenn/tasks/schedule.py)
- [src/kajenn/tasks/spool.py](../../../src/kajenn/tasks/spool.py)
- [src/kajenn/tasks/executor.py](../../../src/kajenn/tasks/executor.py)
- [src/kajenn/tasks/store.py](../../../src/kajenn/tasks/store.py)
- [src/kajenn/server_app/server_sections/tasks_section.py](../../../src/kajenn/server_app/server_sections/tasks_section.py)
- [tests/core/test_task_manager.py](../../../tests/core/test_task_manager.py)
- [tests/core/test_task_scheduler.py](../../../tests/core/test_task_scheduler.py)
- [tests/core/test_task_executor.py](../../../tests/core/test_task_executor.py)
- [tests/core/test_task_spool.py](../../../tests/core/test_task_spool.py)
- [tests/core/test_task_schedule.py](../../../tests/core/test_task_schedule.py)
- [tests/core/test_task_store.py](../../../tests/core/test_task_store.py)
- [tests/server_app/test_tasks_section.py](../../../tests/server_app/test_tasks_section.py)
