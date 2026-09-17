# Tasks — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
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

Claim anchors: [`TaskMixin`](../../../src/kajenn/tasks/mixin.py#L90), [`TaskManager`](../../../src/kajenn/tasks/manager.py#L68), [`tasks`](../../../src/kajenn/tasks/mixin.py#L77), [`tasks`](../../../src/kajenn/tasks/mixin.py#L113), [`TaskScheduler`](../../../src/kajenn/tasks/scheduler.py#L64).

## Batch spool and execution

`TaskSpool` stores pending, active, terminated and aborted task folders on
storage. `LocalTaskExecutor` resolves mount plus node path against the live
server, runs async handlers on the loop and sync handlers through its pool,
and settles the folder once. The single logical executor worker is `local`.
Relaunching a settled batch requires a new id; distributed batch execution is
not supplied by this core task manager.

`TaskManager.publish_progress` pairs the spool snapshot with live session-keyed
hub publication. Cancellation is cooperative through the spool marker; it is
not forced interruption of arbitrary Python code.

Claim anchors: [`TaskSpool`](../../../src/kajenn/tasks/spool.py#L116), [`LocalTaskExecutor`](../../../src/kajenn/tasks/executor.py#L64), [`TaskManager`](../../../src/kajenn/tasks/manager.py#L68), [`publish_progress`](../../../src/kajenn/tasks/manager.py#L160).

## Source and test evidence

- [src/kajenn/tasks/mixin.py](../../../src/kajenn/tasks/mixin.py)
- [src/kajenn/tasks/manager.py](../../../src/kajenn/tasks/manager.py)
- [src/kajenn/tasks/scheduler.py](../../../src/kajenn/tasks/scheduler.py)
- [src/kajenn/tasks/schedule.py](../../../src/kajenn/tasks/schedule.py)
- [src/kajenn/tasks/spool.py](../../../src/kajenn/tasks/spool.py)
- [src/kajenn/tasks/executor.py](../../../src/kajenn/tasks/executor.py)
- [tests/core/test_task_manager.py](../../../tests/core/test_task_manager.py)
- [tests/core/test_task_scheduler.py](../../../tests/core/test_task_scheduler.py)
- [tests/core/test_task_executor.py](../../../tests/core/test_task_executor.py)
- [tests/core/test_task_spool.py](../../../tests/core/test_task_spool.py)
- [tests/core/test_task_schedule.py](../../../tests/core/test_task_schedule.py)
