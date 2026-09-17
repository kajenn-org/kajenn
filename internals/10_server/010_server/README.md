# Server

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/010_server.md` — DIVERGENT, 0 CONVERGE / 8 DIVERGE / 39 SILENT.

A server is one object behind one port. It holds the list of programs installed
on it — the **applications** — and for every request that arrives it decides
which one answers, hands the request over, and stays out of the way. It never
looks inside an application: that ignorance is what lets an application be
written, replaced or removed without the server changing at all. Around that one
decision it owns the few things the applications must share — when they start and
stop, where their blocking code runs, and the live picture of what the machine is
doing right now.

Its parts:

- **the composition** — the server is a chain: a lean base, layers above it
- **the applications** — what it hosts, and the two things it knows about each
- **the demux** — how one request finds its one application
- **the registry** — which request am I serving, and what else is in flight
- **the lifespan** — who starts first, who stops last
- **the work pool** — where blocking code runs so the loop stays free
- **the three scope types** — how uvicorn's three kinds of traffic enter
- **the configuration** — where the whole shape comes from
