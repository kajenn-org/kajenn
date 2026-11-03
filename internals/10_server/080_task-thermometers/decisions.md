# Task thermometers (termometri) — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

## Still open

**S1 [cancel] — kajenn-org/kajenn#4 "Settle the tasks spool claims: boot-time orphans and the cancel marker".**
design.md says the stop request is a signal the batch reads at its own pace.
`TaskSpool.request_cancel` writes the marker
(`src/kajenn/tasks/spool.py:265`), called by `TasksSection.cancel`
(`src/kajenn/server_app/server_sections/tasks_section.py:240`).
`TaskSpool.is_cancelled` (`src/kajenn/tasks/spool.py:272`) has no caller in
`src/`. The issue asks whether `LocalTaskExecutor.execute` reads the marker
while the task runs, and at which granularity, or whether `is_cancelled` is
dropped. The same issue covers the `active/` folders never settled at boot,
which belong to [070 tasks](../070_tasks/decisions.md).
