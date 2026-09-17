# Tasks

**Version**: 0.1 · **Last Updated**: 2026-08-22 · **Status**: 🔴 DA REVISIONARE

**The need.** An installation runs work that is no HTTP request: schedules, batches, spooled runs that must survive and be accounted for.

The task subsystem: scheduler (cron-like plans), spool (the on-disk
truth of every run), executor and manager. Mounts like any other app;
administered through the `_server/tasks` section.

Interactions: task-thermometers · server-application · storage (spool files).
