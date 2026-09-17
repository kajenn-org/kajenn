# Middleware — decisions

**Version**: 0.5 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

**The middleware chain, with the work finished.** Read this as a report from
the day everything described here is running: it says what the chain *is*, and
never what it lacks. What the code holds is [status.md](status.md)'s subject.

Every voice carries its source. A voice sourced to the owner and a date was
decided in conversation before it reached any register.

The **open frictions** are the closing section. Each carries a **family tag**
in brackets: the frictions of the server skeleton are settled in one grouped
pass by family rather than one entry at a time (owner, 2026-08-23).

---

## 5. A 401 is a question, and the form of the question depends on who is asked

**The destination carried back is validated, never echoed.** A `next` that
came in from outside is the classic open redirect, and it is checked before it
is used.

## 7. The middleware chain carries HTTP, and says so

**Source: D7, SPECIFICATION.md:100; the mixin's own contract.** The chain is
walked only by HTTP scopes. The lifespan conversation and WebSocket
connections go straight to the server.

---

# Open frictions

## Evidence follow-up — 2026-09-08

- Current status distinguishes standalone `Response.ERROR_MAP` from the actual
  outer middleware's generic 500 policy; handler-body `TypeError` no longer
  receives the historical sync-only 400 mapping.

The earlier findings below retain their historical wording; the follow-up above
and [current status](status.md) identify what still applies. No decision status
is promoted by this audit.

Scaffolding for the interview, not a register. Each voice carries a **family
tag**; the skeleton's frictions — 010, 015, 020, 025, 030 — are settled in one
grouped pass by family (owner, 2026-08-23).

Interview file: `temp/interview_030_middleware.md`.

**S1 [unread · cross] — the second exception-to-status table has no production
reader.** The response class carries a mapping of `ValueError` and `TypeError`
to 400, `FileNotFoundError` to 404 and `PermissionError` to 403. Its only
production caller is the middleware chain, and it calls it **inside a branch
that already knows the exception is an HTTP one** — which carries its own
status, so the mapping is never consulted. Every other exception takes the
explicit 500 path beside it. Proven in [status.md](status.md); the table is
exercised by tests alone.

So this is not two competing mechanisms, as it first reads: it is one live path
and one table nothing reaches. Either the non-HTTP branch starts consulting it —
which would turn a handler's `ValueError` into a 400, and that is the same
decision as
[020 applications](../020_applications/decisions.md) S5 — or the table goes.
Recorded in the same wording in
[020 applications](../020_applications/decisions.md), friction S14.

**Implementation follow-up, 2026-09-08 (not a new ratification).** The
historical no-WebSocket finding below is superseded by the delivered
`BaseServer.on_websocket`, `WsxConnection` and raw `serve_websocket` seam.
The Origin gate belongs to WSX handshake processing; raw applications own their
handshake policy after the server state gate. Evidence and owner provenance:
[WebSocket decisions](../055_websocket/decisions.md) and [WebSocket status](../055_websocket/status.md).
