# Monitor

**Version**: 0.1 · **Last Updated**: 2026-08-22 · **Status**: 🔴 DA REVISIONARE

**The need.** An admin asks "what is my server doing right now?" and must get ONE page that answers for every mounted app at once.

One page over every mounted app, rendered by the `_server/monitor`
section through the `app_snapshot` / `app_panel` / `panel_source` contract
that every `BaseApplication` can implement.

Interactions: server-application (hosts it) · every app implementing the contract · orchestration (pool projection).
