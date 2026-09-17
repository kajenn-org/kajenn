# Database

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/065_db.md` — DIVERGENT, 0 CONVERGE / 0 DIVERGE / 0 SILENT.

An installation mounts its databases through the recipe, and the core knows no
concrete backend. A `database` in the configuration names a `db_class`, which
builds the real db from connection parameters, and optionally a
`db_handler_class` for lifecycle and delegation; the concrete db classes live
outside the core.
