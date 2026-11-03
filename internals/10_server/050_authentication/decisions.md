# Authentication — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

No ratified decision. See [open_decisions.md](open_decisions.md).

---

## Still open

**S1 — who authenticates, and each application enriching the avatar: planned
work absent from design.md.** `kajenn-org/kajenn#38` (Declare who authenticates and let each application enrich the avatar (D-SA-6..9)) records D-SA-6..9. The server
configuration declares which application authenticates. Each application adds
its own tags and data to the avatar of its own branch, after the verdict and
before routing. The enriched avatar travels to a remote application.
design.md names none of this. [status.md](status.md) records one verdict per
scope, set by `authenticate_scope`.

**S2 — the identity a child process declares is trusted as written: planned
work absent from design.md.** `kajenn-org/kajenn#58` (Never trust the identity a child process declares: signed tickets on the bus, OS isolation of the child) states that the server never takes
`meta["auth"]` from a child at face value. A call on behalf of a user carries
a ticket signed by the server. A call without a ticket runs as the child's own
identity. design.md names none of this. [status.md](status.md) records that
`avatar_from_auth` rebuilds `meta["auth"]` into `scope["auth"]` with no
verification.

**S3 — Keycloak as identity provider: planned work absent from design.md.**
`kajenn-org/kajenn#96` (Native Keycloak integration: a realm as identity provider for login, bearer tokens, roles and logout) asks for a Keycloak realm as identity provider for login, bearer
tokens, roles and logout. design.md names no provider. [status.md](status.md)
records the generic OIDC method.
