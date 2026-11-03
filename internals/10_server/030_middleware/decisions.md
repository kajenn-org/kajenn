# Middleware — decisions

**Version**: 0.6 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

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

**S12 — design.md and README.md describe five layers; the chain has four.**
design.md:45, :147-149, :297 and :382 name an `auth` layer at 450 and
`AuthMiddleware`. README.md:14-15 says the core ships five, `auth` included.
`AuthMiddleware` was removed in 0.4.0 (commit `05411ad`). `default_registry()`
carries `errors`, `logging`, `cors`, `session` (middleware/__init__.py:63-70).
The identity reaches `scope["auth"]` through
`RoutedApplication.authenticate_scope` (routed_application.py:284-298).

**S13 — cors: design.md says nothing is allowed by default; the code allows
every origin and header.** design.md:136-137 says what cors allows is
configured and nothing is allowed by default. `CORSMiddleware` defaults
`allow_origins` and `allow_headers` to `["*"]` (cors.py:62, 66).

**S14 — design.md says a middleware of one's own cannot be named in the
description; the recipe element takes it.** design.md:302-305 says the
section's words are the five core names and no others. The `middleware`
element declares four names and takes `**extra` (config/elements.py:254-268).
A middleware merged through `middleware_registry=` is switched by its own name.

**S15 — design.md names a "probe filter"; no such thing exists in `src/`.**
design.md:284 and :320 name a probe filter as a layer that raises 404. No
class, function or configuration word of that name exists in `src/kajenn/`.

**S16 — design.md gives a browser 401 a 302 to the login page; the code and
design.md itself give the bare 401.** design.md:176-182 says a browser
navigation gets a 302 to the login page and an API caller a body naming the
login URL. design.md:396-398 says the core never points a caller at a login
page (D-SA-4). `ErrorMiddleware` answers an `HTTPException` with its status and
forwards its header pairs (errors.py:95-102, 128-131).
