# Tasks

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/070_tasks.md` (2026-09-17) — not verifiable: decisions.md carried no entry, so the table has 0 rows.

Work that is no HTTP request: schedules, batches and spooled runs that survive
and are accounted for. The scheduler holds the cron-like plans, the spool is the
on-disk truth of every run, and the executor and manager drive them; the
subsystem is supplied by the server's `TaskMixin` and is administered through
the `_server/tasks` section.
