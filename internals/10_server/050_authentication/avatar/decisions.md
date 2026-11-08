# Avatar — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this entry SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

---

## Still open

**S1 — applications enriching the avatar: planned work absent from
design.md.** `kajenn-org/kajenn#38` (Declare who authenticates and let each application enrich the avatar (D-SA-6..9)) records D-SA-7..9. Each application adds its own tags
and data to the avatar of its own branch. What it adds never goes back to the
session. The enriched avatar travels in the envelope to a remote application.
design.md describes one identity object read the same way by every surface.

**S2 — the avatar a child process declares: planned work absent from
design.md.** `kajenn-org/kajenn#58` (Never trust the identity a child process declares: signed tickets on the bus, OS isolation of the child) states that the server never rebuilds an avatar from
the `meta["auth"]` a child wrote. A call on behalf of a user carries a ticket
signed by the server. design.md names none of this. [status.md](status.md)
records that `avatar_from_auth` rebuilds the avatar from `meta["auth"]`.

**S3 — the avatar of a Keycloak identity: planned work absent from
design.md.** `kajenn-org/kajenn#96` (Native Keycloak integration: a realm as identity provider for login, bearer tokens, roles and logout) names `sub` as the stable identity, and realm roles,
client roles and groups as the source of tags and claims. design.md names no
provider and no claim mapping.
