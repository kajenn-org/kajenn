# Database

**Version**: 0.2 · **Last Updated**: 2026-10-27 · **Status**: 🟡 rewritten on the shipped contract; ratification pending

Verification: `kajenn-meta/verification/10_server/065_db.md` — the report
predates the shipped handler contract and is refreshed apart.

An installation mounts its databases through the recipe, and the core knows no
concrete backend. A `database` names a `handler_class`, a `DbHandlerBase`
subclass, or takes its section's default. One handler per class builds every
database of that class from its params, gives each call the object it works
on and releases it at the end of the call. Concrete handlers live in the
kajenn-db package.
