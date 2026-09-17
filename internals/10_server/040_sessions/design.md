# Sessions

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Sessions hold per-user server-side state between requests and persist it
across process lifecycles.

The server-side session: `MemorySessionStore`, delta-check persistence, pickle
snapshot per named instance (`serve --name`), cookie `Max-Age = ttl × 24`,
the avatar.

Interactions: middleware (session) · authentication.
