# Middleware

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/030_middleware.md` — DIVERGENT, 5 CONVERGE / 8 DIVERGE / 13 SILENT.

A middleware is a layer wrapped around the dispatch. It sees the request before
the server decides who will serve it, and it sees the answer on the way out: it
can look, it can add, it can answer instead, and it can refuse — for every HTTP
request, whichever application the request was going to. That is what keeps
questions with one machine-wide answer out of the applications: authentication,
cross-origin reads, what to do when something raises, whether a request deserves
a line in a log. Each layer declares one integer, `middleware_order`, and the
chain sorts itself by it, lowest outermost. This core ships five — errors,
logging, cors, session, auth.

Its parts:

- **the middleware chain and its order** — one number per layer, and why the
  order is the design
- **assembly** — built once, from an explicit list, with no global registry
- **the five** — what each one does, and what it puts on the request
- **the outermost layer** — the one that turns a raised exception into an answer
- **what the middleware chain does not see** — and why that is a decision, not
  an omission
- **what a site writes** — the switches, and what the capabilities arm by themselves
- **writing one** — the base class, the two attributes, and how it is installed
