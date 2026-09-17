# Middleware — tech notes

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE


## Renewal evidence — 2026-09-08

The current implementation is recorded in [status](status.md), verified against
`2465fcc`. The August notes below are archival: their line references, coverage
numbers, test paths, missing-consumer assertions and proposed next steps are not
fresh measurements or current work authorization. The original detailed status
can be recovered with `git show 2465fcc:internals/10_server/030_middleware/status.md`.

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

For whoever works ON this entry, not for whoever reads about the middleware
chain. The working trail: what decided what, what is easy to look for and not
find, and what the next person needs to know before touching it.

## Classification and position

**A shelf** — a technical stratum. Nobody asks for "middleware"; they ask for a
login, an access log, or cross-origin access, and the middleware chain is where
those live.

Fifth and last of the server skeleton. It assumes
[020 applications](../020_applications/README.md) — the exceptions it answers are the
ones the route resolution raises — and it is where two later entries put their
own layer: sessions and authentication own a layer each and are described
elsewhere.

## Who stands on this

| They lean on it as | Entries |
|---|---|
| the layer that puts their thing on the request | [040 sessions](../040_sessions/README.md), [050 authentication](../050_authentication/README.md) |
| the middleware chain that answers what they raise | [020 applications](../020_applications/README.md) |
| the other side of its login challenge | [090 server-application](../090_server-application/README.md) |
| the thing it is confused with | [025 routing system](../025_routing-system/README.md) |
| the boundary its scope filter draws | [20_spa/030 channel](../../20_spa/030_channel/README.md) |

A change to the ordering numbers reaches every request on the machine. A change
inside one layer reaches only what that layer does.

## The boundary with 040 and 050

Two of the five layers are the visible half of entries that come later:
`SessionMiddleware` is where [040 sessions](../040_sessions/README.md) touches a
request, and `AuthMiddleware` is where
[050 authentication](../050_authentication/README.md) does. This entry owns
**where they sit in the middleware chain and what they leave on the request**;
what a session *is* and how an identity is resolved are theirs.

The line to hold when auditing those two: `session.py` and
`authentication.py` are 137 and 48 lines and are largely described here already
— the substance is in `session/` and `auth/`, which this entry does not touch.

## The working trail

**Founding decisions** — D7 (SPECIFICATION.md:100) puts middleware explicitly
outside phase 0; D17 (SPECIFICATION.md:229) makes every capability a mixin,
which is what answers **Q2** (SPECIFICATION.md:698) — *"middleware chain: in
the base or only on the public server?"* — in the general rather than by
choosing one of its two horns.

**D24** (SPECIFICATION.md:421) is the reason `SessionMiddleware` issues a
cookie on exactly one branch: the login attaches an identity in place and the
session id never changes, so no login-time cookie exists.

**`5b567a3`** (2026-08-14) restored the challenge negotiation — 401 for the
anonymous, 403 for the known — and it is the same commit
[020 applications](../020_applications/README.md) cites for the other half of the pair.

## Traps

- **`default_registry()` lives in the package module, not in `base.py`.** It
  cannot live in `base.py`: the concrete layers subclass `BaseMiddleware`, so
  importing them there is a cycle. The module says so at
  `middleware/__init__.py:33-35`.
- **The chain is built once, in the constructor.** Changing the switches on a
  running server does nothing — there is no rebuild path, and nothing watches
  the configuration. Relevant when the live-configuration worksite reaches this
  entry.
- **The middleware chain never sees a WebSocket or a lifespan scope.** The
  mixin's `__call__` sends them to `super()`. Anything written here that assumes
  it sees all traffic is wrong. Friction S2.
- **`Response.ERROR_MAP` is not the middleware chain's error mapping.** The
  middleware chain's is the explicit branching in `_error_response`. `set_error`
  is called only for an `HTTPException`, which never reaches `ERROR_MAP`. Do not
  "fix" a status by editing that table: nothing in production reads it.
  Friction S1.
- **`level` on the access log is a severity, not a threshold** — and a
  misspelled one silently becomes INFO. Frictions S4 and S5.
- **The `next` of the login redirect is validated, not echoed.** It goes
  through `safe_next_path` (imported from `..auth`). Anyone touching that path
  is touching an open-redirect defence.
- **A layer may not answer after the answer has started.** `ErrorMiddleware`
  tracks it and re-raises. A new layer that wraps `send` must not swallow that
  signal.
- **An error response does not pass through the inner layers' `send`
  wrappers.** `ErrorMiddleware` answers on the `send` it received
  (errors.py:93). So a layer that adds a header on the way out adds nothing to
  an error response — including the cross-origin header, without which a
  browser cannot read the error at all. Friction S9.
- **`middleware_default = False` on the session and identity layers is
  misleading.** Their own mixins `setdefault` them on, so on an `AsgiServer`
  they are in the middleware chain regardless of the description. Reading the
  class attribute alone gives the wrong answer about a shipped server.
- **`errors=False` is writable and nothing refuses it.** With the outermost
  layer gone, raises escape the server. Friction S10.
- **The middleware grammar element has five named parameters and no
  `**kwargs`.** Unlike the plugins section, it physically cannot name a sixth
  middleware. Friction S3.

## What was verified live while writing this

Five probes, all driving composed servers at the ASGI level:

- the full recipe: the chain walked outwards gives the five in declared order,
  and every row of the answer table is the probe's own output — including
  the same 401 answered as a **401 with a login URL** to a JSON caller and as a
  **302 to the login page** to a browser;
- the access log's `level` option: `WARNING` and `debug` resolve as written,
  while **`verbose` and `nonsense` both silently become INFO** (friction S4);
- **an error response against an ordinary one**, same route family, same
  origin, same new session: the 200 carries the cross-origin header and the
  cookie, the 404 carries neither (friction S9);
- **`errors=False`**: accepted by the grammar, the layer is dropped, and a
  raised `HTTPNotFound` escapes the server uncaught (friction S10);
- **a middleware of my own**: `middleware_order = 250`, installed with
  `middleware=` plus `middleware_registry=`, lands between logging and cors and
  stamps every answer. That probe is what `design.md`'s block 7 is written from.

Each is a dozen lines to reproduce.

## Before the next step is written

`decisions.md` is 🔴 with eleven frictions, all tagged by family. None is settled
here: they join the grouped pass over the skeleton (010, 015, 020, 025, 030)
that the owner chose on 2026-08-23. **With this entry the skeleton is complete,
so that pass can begin.**

**Four of the eleven are cross-entry** and are written in the same words on
both sides: S1 with [020 applications](../020_applications/README.md) S14, S2 with
[20_spa/030 channel](../../20_spa/030_channel/README.md) and 020's S6, S3 with
[015 configuration](../015_configuration/README.md) S6 and
[025 routing system](../025_routing-system/README.md) S3, S11 with 020's S18.

**S1 turned out smaller than it looked.** Written from 020 it read as two
competing mappings; read from here it is one live path and one table with no
production caller. That halves the question: it is not "which applies when" but
"does the dead one go or start being used".

**S6 pairs with 025's S5.** Whether the middleware chain is per-machine and
whether plugins are per-server are the same question asked of the two extension
points, and answering one without the other would leave the framework saying
different things about scope depending on which tool you reached for.

**S9 and S10 are the two the blind reader found that execution had not**, and
they are the heaviest here. S9 makes the error negotiation of §4 unreadable to
exactly the caller it was built for; S10 lets one configuration word remove the
layer every raise in the codebase assumes. Neither is a documentation defect:
both close by changing code.

The obvious content of `steps/step_01/` here is the untested paths, and one of
them is worth more than a coverage number: **`logging.py:83-86`, the log line
written when a request fails**. It is the line an operator reads first when
something breaks in production, and nothing proves it is written.
