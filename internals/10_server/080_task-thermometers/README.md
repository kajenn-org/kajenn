# Task thermometers (termometri)

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/080_task-thermometers.md` (2026-09-17) — not verifiable: decisions.md carried no entry, so the table has 0 rows.

Whoever launches a long batch sees it move while it runs, and stops it politely
without corrupting its work. `TaskManager.publish_progress` writes the
`progress.json` snapshot on the spool and publishes the same data on the live
event channel in one paired call; a stop request is a signal the batch reads at
its own pace.
