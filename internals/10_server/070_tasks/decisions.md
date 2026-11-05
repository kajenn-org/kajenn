# Tasks — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

## Still open

**S1 [mount] — design.md says the task subsystem mounts like any other app; the code composes it into the server.**
design.md line 8 says "Mounts like any other app". The task subsystem is
`TaskMixin`, composed into `AsgiServer` (`src/kajenn/asgi_server.py:109-118`).
It has no mount and no application code. Its only HTTP surface is
`TasksSection`, attached by `ServerApplication` under `tasks`
(`src/kajenn/server_app/server_app.py:248`). README.md already says "supplied
by the server's `TaskMixin`". The owner decides which of the two sentences
design.md keeps.

**S2 [cancel · orphans] — kajenn-org/kajenn#4 "Settle the tasks spool claims: boot-time orphans and the cancel marker".**
`TasksSection.cancel` writes the `cancel` marker
(`src/kajenn/server_app/server_sections/tasks_section.py:240`).
`TaskSpool.is_cancelled` (`src/kajenn/tasks/spool.py:272`) has no caller in
`src/`, so a cancelled task runs to its end. A task folder left under `active/`
by a worker that stopped is never settled: the only `settle` call in `src/` is
`src/kajenn/tasks/executor.py:137`. The issue asks two decisions: whether a
boot sweep settles `active/` orphans as `aborted`, and whether
`LocalTaskExecutor.execute` reads `is_cancelled` while the task runs.

**S4 [route names] — kajenn-org/kajenn#8 "Rename the list routes of the server sections".**
`TasksSection.list` (`src/kajenn/server_app/server_sections/tasks_section.py:83`)
publishes the path `/_server/tasks/list`. The issue asks the owner to name the
replacement. The rename changes a public URL and the OpenAPI document.
