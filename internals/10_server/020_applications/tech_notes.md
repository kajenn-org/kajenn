# Applications — tech notes

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE


## Renewal evidence — 2026-09-08

The current implementation is recorded in [status](status.md), verified against
`2465fcc`. The August notes below are archival: their line references, coverage
numbers, test paths, missing-consumer assertions and proposed next steps are not
fresh measurements or current work authorization. The original detailed status
can be recovered with `git show 2465fcc:internals/10_server/020_applications/status.md`.

The five complete server/configuration/application/routing/middleware recipes
are now collected by `tests/test_documentation_recipes.py`; the documentation
renewal verified construction in isolated subprocesses. Other code blocks are
contextual fragments unless explicitly identified as a complete recipe. A
constructed server is not proof that every request or lifecycle path succeeds.

Historical interviews were recovered in the original repository's
`temp/internal_doc/` directory, including `interview_010_server.md` (moved there
on 2026-08-29). These are local archives, not files promised in a new checkout
and not owner ratifications by themselves. Do not recreate absent answers or
start implementation steps from an old scaffold. Current tests live under
`tests/core/` and `tests/spa/`; their `x/` directories still contain initializers.

## August audit trail (preserved)

For whoever works ON this entry, not for whoever reads about applications. The
working trail: what decided what, what is easy to look for and not find, and
what the next person needs to know before touching it.

## Classification and position

**A feature**, and the one a consumer of this framework actually writes. Every
other entry of `10_server` is something they configure or inherit; this is the
class they subclass. The entry's own pages say nothing about being a feature —
the classification is editorial and lives here.

Third in reading order, and it assumes the two before it: the identity triplet
and the demux are [010 server](../010_server/README.md)'s, the recipe and the read door
are [015 configuration](../015_configuration/README.md)'s. It defines the routing tree,
which four later entries assume.

## Who stands on this

| They lean on it as | Entries |
|---|---|
| the tree they arm a capability onto | [025 routing system](../025_routing-system/README.md) |
| the dispatch the middleware chain wraps, and the exceptions they answer | [030 middleware](../030_middleware/README.md) |
| the request their identity rides on | [050 authentication](../050_authentication/README.md), [040 sessions](../040_sessions/README.md) |
| a lens on the same tree | [openapi](openapi/README.md), [mcp](mcp/README.md) |
| the base class they subclass | [090 server-application](../090_server-application/README.md), [20_spa/010 spa-application](../../20_spa/010_spa-application/README.md) |
| the streaming they push over | [070 tasks](../070_tasks/README.md) (SSE push), [090 inspector](../090_server-application/inspector/README.md) |

A change to the four obligations reaches every one of them. A change to the
argument reconciliation reaches the two lenses, because they share the method.

## The working trail

**Founding decisions** — D7 (the app-side contract, phase 0, defined by its
tests), D16 (cooperative init), D18 (slots), D4 (no service endpoint injected
into a hosted router). Then D23's wave rulings (handlers are pure), D25
(declarative branches, invariant 10 enforced), D26 (`pydantic`/`openapi` fixed
structure), and the scope ruling at SPECIFICATION.md:363 that puts the complete
OpenAPI and MCP applications in the core rather than above it.

**The invariants that bind this entry** — SPECIFICATION.md §5, numbers 2
(thread-correct teardown), 3 (a denied node answers natively, never falls
through), 9 (one contract-test suite across implementations of one interface)
and 10 (never routing as registry). All four are recorded from defects in the
old implementation, and each one is worth reading before changing the dispatch.

**Delivery commits** — `53b4e38` (2026-07-21, core 1c: request, response,
plugins, the two lenses), `c360f60` (2026-07-24, core 1e: SSE over streaming),
`a1a8f7e` (2026-07-25: `code` + `mount`), `0dff4ed` (2026-08-12:
`route_cleanup`), `5b567a3` (2026-08-14: 401 for the anonymous, 403 for the
known).

**The ruling that is not in the log** — the same one that bites
[010 server](../010_server/README.md): `a1a8f7e` was never appended to
SPECIFICATION.md, so §4's app-side contract still names `mount_name`. Do not
search the specification for `code`/`mount` on the application side; the only
record is that commit message. Friction S1.

## Traps

- **`route_cleanup` looks dead and is not.** No override anywhere in `src/`,
  and the only tests are the ones that assert it is called. Its production
  consumer is in another repository:
  `genropy-asgi/src/genropy_asgi/proxy/genropy_proxy.py:69`. Same discipline as
  `wsgi_app` — a seam whose filler is the bridge.
- **The live request does not reach a hosted application's handler.** The
  injection everyone remembers is `ServerApplication.bind_kwargs`
  (`server_app.py:200`), not the base one, and the parameter is `_request`, not
  `request`. Searching `routed_application.py` for the seam D23 ratified finds
  nothing. Friction S2.
- **`request.path` is already mount-relative.** The server stripped the prefix
  before the handover, so an application resolving `request.path` against its
  own tree is correct, and anything reconstructing an absolute URL from it is
  not.
- **`asyncio.iscoroutinefunction`, never `inspect`'s.** On 3.11 genro-routes
  falls back to the asyncio sentinel and only the asyncio check reads it
  (`routed_application.py:221-222`). Swapping the import silently sends every
  async handler through the thread pool.
- **The `route` property has a side effect.** First access after attachment
  arms the server's plugins (`routed_application.py:143-156`). A debugging line
  that touches `app.route` early changes when arming happens.
- **But touching `route` in the constructor is safe, and looks like it is
  not.** Before attachment `self.server` is `None`, so `arm_router` is not
  found, the branch is skipped and `_armed` stays `False`. Verified on a booted
  server. Every application in the package touches `route` in its own
  constructor — this is the normal shape, not a hazard.
- **Every branch in the package uses the `instance` form.** The `cls` + `params`
  form D25 named as the destination is used nowhere, and the library derives
  the build timing from which form is used. Converting one call site changes
  when that sub-tree exists. Friction S15.
- **`fields is None` is not reachable on a real server.** `pydantic` is in
  `FIXED_PLUGINS` and cannot be switched off, so the no-signature branches of
  `bind_kwargs`/`spread_over_params` only run under a bare `BaseServer`. Do not
  write a test that expects to reach them through `AsgiServer`.

## What was verified live while writing this

Nine probes, all against a composed `AsgiServer` at the ASGI level:

- the `design.md` recipe, whose seven-row answer table is the probe's own output;
- a `POST` with and without `content-type` — `{"sum": 5}` against
  `{"sum": 0}`;
- a `TypeError` in a sync handler body → **400** with the internal message,
  the same bug in an async body → 500, and a `ValueError` in a sync body →
  500;
- `grep` for consumers of `Request.created_at` / `age` / `scope` across
  `src/`, `tests/` and the genropy-asgi bridge — none;
- `_armed` on a booted application: still `False` after the constructor's own
  `add_branches` and `route.plug`, `True` after the first access made once the
  server owns it, with `['auth', 'openapi', 'pydantic']` on the router. The
  constructor's early touch of `route` does **not** consume the guard, and the
  recipe's sub-tree answers 200. A blind reader flagged this as a probable
  defect; it is not one, and the pages now say why;
- `grep` for `"cls"` in `src/` and `tests/` — none, which is what makes the
  D25 migration half done (S15);
- the custom-grammar snippet of README §1, run end to end: a recipe writing
  only `branding(title=…)` reads `Main Store` back and `EUR` from the
  parameter's own declared default;
- `@route(auth_rule="admin")` on a composed server: **401** to an anonymous
  caller, 200 on the unruled route beside it;
- `SseStream` over a two-event async generator: the wire carries
  `retry: 2000`, then `event: takings` / `data: {"today": 42}`, then a bare
  `data:` record.

Whoever reopens frictions S4, S5, S10 or S15 can reproduce each in a few lines
rather than re-deriving them.

## Before the next step is written

`decisions.md` is 🔴 with sixteen frictions, and they do not all belong to the
same conversation. The interview is `temp/interview_020_applications.md`,
fourteen turns; S16 has no turn of its own because it closes by building, not
by deciding.

**S1 and S2 are cheap** and should go first: S1 is one amendment to
SPECIFICATION.md §4 that rides along with 010's S1/S2, and S2 is a placement
plus a name.

**S4, S5, S9 and S14 are one conversation about failure**, not four. All of
them are about a wrong thing reported as the right kind of thing; S9 sits
inside S5, and S14 is likely its mechanical cause. Settling them means deciding
what a 400 body may carry and which of the two mappings owns which case, which
the interview should take as one question.

**S3 and S7 are the ones with real design content**: whether the response
shapes are ratified or conventional, and whether the arrival wants a streaming
request body. S7 in particular cannot be closed by a "no" alone — the overview's
first rule requires the reason to be written where the limit is accepted.

**Four cannot be settled inside this entry.** S6 (the WebSocket entry point) is
recorded in the same wording in
[20_spa/030 channel](../../20_spa/030_channel/README.md), S14 in
[030 middleware](../030_middleware/README.md), S12 in both
[010 server](../010_server/README.md) and [015 configuration](../015_configuration/README.md),
S13 in 015, and S11 in 010. All five were seeded there by this audit and carry
the same text on both sides. Settling one of them means editing two documents
in one change; answering it in only one place is how the dossier would start
disagreeing with itself again.

**S12 is the one to settle first among those**, because §1 of this entry is a
proposal and the other two entries state contracts of their own. Everything
written about the application contract anywhere depends on which list wins.

The obvious content of `steps/step_01/` is the small, self-contained half: the
missing tests (S8, and the streaming handover in particular) and the dead
surface of S10. Neither depends on any of the open questions above, and both
shrink the distance between the two documents without deciding anything. S15 —
converting the four branch call sites to the factory form — is a candidate for
the same step, but only after T10 says which form is the destination, because
the conversion also changes when those sub-trees are built.
