# Database

**Version**: 0.1 · **Last Updated**: 2026-08-22 · **Status**: 🔴 DA REVISIONARE

**The need.** An installation mounts its databases through the recipe, without the core knowing any concrete backend.

`db.py` — the core's minimal contract: a `database` in the config names a
`db_class` (builds the real db from connection parameters) and optionally a
`db_handler_class` (default `AsgiDbHandlerBase`, lifecycle + `__getattr__`
delegation). Concrete db classes live OUTSIDE the core.

Interactions: configuration (the `database` words) · the hosted applications that consume the handler.
