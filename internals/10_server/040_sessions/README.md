# Sessions

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/040_sessions.md` — DIVERGENT, 0 CONVERGE / 1 DIVERGE / 0 SILENT.

Per-user server-side state between requests, and its persistence: `MemorySessionStore`,
delta-check persistence, a pickle snapshot per named instance, the session cookie
and its lifetime, and the avatar the session holds.
