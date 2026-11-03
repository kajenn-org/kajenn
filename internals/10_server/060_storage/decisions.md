# Storage — decisions

**Version**: 0.1 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

No ratified decision. See [open_decisions.md](open_decisions.md).

## Still open

**S1 [store-db] — kajenn-org/kajenn#59, "Asqueel databases in server and
application configuration", item 5.** The issue asks that a `users`/`tokens`
`store_class` receive the name of a registered database (`db="betadb"`) and
that kajenn pass it that database. The store is built today with the server's
storage as its first argument and the remaining descriptor options as keywords
(auth/mixin.py:200-201); no code resolves a `db` option to a registered
database. The issue records no decision.
