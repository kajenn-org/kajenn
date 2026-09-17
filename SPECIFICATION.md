# New Design Specification — the kajenn-* family (core + orchestration)

**Version**: 0.6.1 · **Last Updated**: 2026-09-12 · **Status**: 🔴 DA REVISIONARE

> Founding specification of the redesign. Decided in the design sessions of
> 2026-07-17→19; the critical survey of the current codebase that motivates it
> is `temp/architecture_2026-07-17_overview_and_critique.md` (+ HTML version),
> tracked in the OLD `kajenn` repo. This document is the tracked founding
> document of this repository.

---

## 1. Purpose & method

The redesign starts in a **new repository**, spec-first, developed in steps,
knowing the final shape. This document is the **single source of truth for
decisions**: every decision ratified in discussion is APPENDED to the decision
log below. History is never rewritten — a correction is a new decision that
cites the one it supersedes. Proposals discussed but not explicitly ratified
are marked **◆** (to be confirmed in bulk when this document is reviewed).
Everything else is an open question (Q).

Claims about the current codebase cite verified `file:line` references at
branch `refactor/server-ownership` of the OLD `kajenn` repo.

Guiding principles inherited from 2026-07-17 (already ratified there): routes
all static from boot, config is data not structure; objects always exist,
backends come from config (no `X | None` flipped by flags); work at time of
use; never routing as a registry; extension by subclassing; auth backends as
mixins.

---

## 2. Decision log — ratified

### D1 — Official terminology: public server / internal server
The pair "root server / micro-server" is retired (committed in the old repo,
`e8bfbb0`). The **public server** is the exposed face: it owns auth, sessions,
origin gates. The **internal server** is never exposed; auth and sessions are
None *by design* — whoever fronts it owns them.

### D2 — What the base server owns
Common substrate of every server, always present:

- **one uvicorn loop** (serving implemented once — today it exists twice,
  written differently, in `server.py:653` and `worker.py:126/134`);
- **one monitored thread pool** for blocking work (async handlers stay on the
  loop and never touch it); lazily provisioned, gauges exposed;
- **the channel to the parent** as a server member: born with `parent=...` it
  connects there; without, the server is the progenitor and the channel is
  off. Same object always — never a ghost. (Today the base only stores the
  address string; the actual client is wired from outside by the child
  runner.)
- **the primary app, always present** — answers `/` and everything no mount
  claims; *(superseded by D29: no application is required on the root)*
- **secondary mounts** (dict of apps by URL prefix; may be empty);
- **lifespan** (ordered startup/shutdown of the apps, reverse on shutdown);
- **the request registry** (see D5);
- **`authenticate()` / `session()`** answering "nobody / none" at the base.

### D3 — One demux rule for every server
*First path segment → secondary mount if it exists, otherwise primary app.*
*(demux superseded by D29: four branches, site index on the uncovered root.)*
Mono-app is a **usage**, not a mechanism: the internal server is a base server
used with only the primary. This kills the old dual `get_app` semantics
(finding C5) and must never be reintroduced by package layering.

### D4 — The `_server` app
Automatic, not configured. **Full** on the public server (login, monitor,
openapi, system endpoints). **Minimal** on the internal server (metrics,
commands) — service endpoints are never again injected into the hosted app's
router (old finding F3: `worker.py:113-115` mutating `application.route`).

### D5 — One request registry, in the base
Two duties, identical everywhere: (1) create the right request from the scope
and make it reachable by the running handler ("current request"); *(duty (1)
superseded by D30: the application builds the request, the registry keeps the
"current request" ContextVar)* (2) keep the
picture of in-flight requests. What differs between servers is the **consumer**
of the picture: the monitor on the public server, the occupancy sensor on the
internal one.

**One mechanism only for "current request", owned by the registry instance.**
The old repo tracks it twice in parallel — the registry's internal ContextVar
(`request.py:756`) and a module-level global ContextVar (`request.py:90`) —
written by different call sites. The module-level global goes away.

The ledger of **forwarded** requests (the commander's QUIESCE bookkeeping) is
a different thing with a different name: it tracks work the server did NOT
execute locally, and belongs to the commander app, never to the base.

### D6 — The internal server has no auth by construction
A class property, not a configuration: a wrong config must not be able to arm
auth on a process that must never be exposed.

### D7 — Phase 0: the base server alone, fully tested
Phase 0 delivers ONLY the base server + the **app-side contract** (what the
server requires of an app: ASGI callable, `mount_name`, `server` property,
lifecycle hooks), exercised by a throwaway test app (one sync route, one async
route, one that raises). The real application class comes later; the contract
is born now — the phase-0 tests ARE its definition.

Explicitly out of phase 0: config builder, middleware, real auth/sessions,
`_server`, WSX — for WebSocket only the socket in `__call__` (scope type
websocket → hook) is left, empty.

Phase 1 = the public server (config, middleware, `_server`, auth).
Phase 2+ = orchestration.

### D8 — Two packages
- **kajenn (minimal)**: phases 0+1 — the ASGI toolkit: base + public
  server, app contract, dispatch, config, middleware, auth core, essential
  `_server`.
- **orchestration package** (working name *genro-server*, name open, Q3):
  phase 2+ — SPA model, pool/hub, batch, rich monitor.

Phases map onto packages. The minimal package stabilizes and becomes a firm
dependency; design churn continues upstairs. The upper package uses ONLY the
public API of the lower — if building the SPA requires touching a private of
the minimal package, the contract is wrong and it shows immediately.

### D11 — Form C: one protocol, two placements (the group manager)
The commander speaks **one protocol** to "the manager of a group". Two
implementations:

- **local, in-process**: a component of the commander process that owns the
  `ProcessPool` directly — zero extra processes (today's behavior);
- **remote sub-commander**: the public server of another machine, same
  protocol over TCP.

Hard rules:
- the commander's code **never** contains `if group_is_remote` — polymorphism
  lives entirely in the manager;
- **one contract-test suite runs against BOTH implementations** (the guard
  learned from old finding A1, where the two dispatch engines diverged
  because nothing forced them to stay equal);
- promotion of a group from local to remote is a config change, not code.

Hierarchy consequences: only **public** servers are network-exposed; workers
stay internal on loopback (the flat multi-machine alternative would expose
internal servers across the network, violating D1/D6). User demux becomes
two-stage — the commander knows *which machine/group*, the manager knows
*which worker* — mirroring the URL demux. A machine's death is ONE channel
EOF: blast radius per machine.

### D12 — Registries as three projections (not replicas)
Three projections of the same population, decreasing fidelity going up:

| Level | Knows | To decide |
|---|---|---|
| Commander | user → **group** (+ who is online, identity) | first stage of user demux; global questions |
| Group manager | user → **worker** + load of its processes | distribution INSIDE the group: assignment, rebalance, spawn/scale |
| Worker | user → connections → **pages** (rich items: pending, subscriptions) | execution |

Rules:
1. **single writer per fact** — the worker owns its pages' truth; the manager
   owns user→worker; the commander owns user→group;
2. **sync is the event stream on the channel** (shaped events folded upward,
   with the seq-gate / anti-entropy lessons of the secondary channel);
   never a write from above into a lower level's registry;
3. **no level skips a level** — the commander never touches a worker
   directly; everything passes through the manager (this is what keeps Form C
   alive);
4. per registry, declare the **owning level**: connections live where the
   socket terminates (the top public server); pages live on the worker; the
   user exists at all three levels with different content.

Derived split of duties: rebalance intra-group = manager; moving a user
across groups (version switch) = commander. Occupancy chain becomes three-
tier: worker = sensor, manager = local judge (scales within its ceilings),
commander = budget setter (hands ceilings to groups). Datachange fan-out:
commander → managers with subscribers → interested workers.

### D15 — One config = the whole site; each process materializes its role's projection
The config (a Python builder recipe) describes the **entire site**: apps and
groups, and all the transversal sections — middleware, plugins, databases,
auth, storage. Every process is born with (config, **role**) and materializes
its own slice:

- **root/public**: mounts the plain apps, the SPA/batch commander apps, the
  full `_server`, the middleware chain, auth/session backends;
- **worker of group X of mount Y**: mounts ONLY the hosted app as primary +
  the minimal `_server` + the transversal pieces it needs (its mount's
  `databases`, its router's plugins) — never the public middleware, never the
  commanders;
- **batch worker**: same config, role batch (this is how the old repo's
  `BatchExecutor` already works: `AsgiServer(config_path)`);
- **remote sub-commander**: same config, role "machine B" — mounts the group
  managers assigned to that machine.

The projection is per-section, not only per-app. Corollary: **dev and prod
use the SAME config** — only numbers (`workers=`) and group runtime specs
change. This closes the old "drip-feed" defect where the SPA worker received
class+kwargs through argv instead of the config, and every new need meant one
more parameter in the pipe.

Sketch:

```python
class ServerConfiguration(AsgiConfigBuilder):
    def main(self, root):
        root.server(host=..., port=...)
        apps = root.applications(default="shop")
        apps.application(code="shop", app_class="myapp:ShopApp")        # plain API: runs on the public server
        erp = apps.application(code="erp",
                               app_class="genro_server:SpaCommander",   # orchestration
                               worker_app_class="myerp:ErpApp")         # what runs in the workers
        g = erp.groups(default="stable")
        g.group(code="stable", workers=2, python=".venvs/stable/bin/python")
        g.group(code="canary", workers=1, python=".venvs/canary/bin/python")
        apps.application(code="batch", app_class="genro_server:BatchApp", workers=1)
```

### Ratified 2026-07-19 (decision session)

**D9 → ratified (choice A).** The minimal package governs URL topology, mounts
included (the mount dict IS the one demux mechanism of D3); the orchestration
package governs process topology. (Was ◆D9.)

**D16 — Extension = subclassing, made real by contract.** Reaffirms principle 7
(2026-07-17): the consumer subclasses server and app (`MyServer(AsgiServer)` +
mixins); the server class is chosen at boot, never in config. The clean split:
**the class says WHO you are** (behavior), **the config says WHAT you are made
of** (data — mounts, groups, backend parameters). Requirements this puts on the
new base (phase-0 contract, with tests): cooperative `__init__`/`on_init`/
lifecycle chains (each layer peels its kwargs and forwards the rest — the old
repo's F2 kwargs-dropping made mixins impossible in practice); declared, stable
override seams. For children, identity never travels as an object (principle
7): a custom internal-server class travels as a dotted name in the group's
runtime spec — data, not identity.

**D17 — Capabilities are mixins; communication is the first one.**
(Amends the channel clause of D2; refines the channel part of ◆D10.)
The base server is born WITHOUT channels. The **communication capability** is
a mixin defined in the minimal package that holds BOTH sides as member objects
constructed by its cooperative init: `parent_channel` (armed by `parent=`, at
lifespan startup) and `children_channel` (the hub, armed by whoever needs it).
Shipped compositions: public server = base + communication (+ auth, ...);
internal server = base + communication; **the sub-commander is the public
server class with `parent=` armed** — no new class. An unarmed side fails
explicitly ("not armed"); a class without the mixin simply lacks the attribute
(a different type, not a ghost — P4 targets runtime-config ghosts, not static
class composition). Consumers of the capability type against a protocol
("has parent_channel/children_channel"), never against concrete classes.
Registration through the hub makes the registrant a **child in the tree even
when not spawned by us** (communication ≠ process lifecycle: the remote
sub-commander is started by its machine, yet registers as a child).
The CommunicationMixin is the first REAL proof of the D16 cooperative
contract: it must build its members and hook the lifespan without the base
knowing it — if the chains don't hold this, they fail in phase 0, not phase 2.

**D18 — Slots policy.** `__slots__` only on high-cardinality objects
(requests, register items, events/frames, config nodes); NEVER on servers,
managers, commanders, apps — single/low-count complex objects where slots are
only an obstacle (the old repo has it backwards: a 21-slot singleton server,
and the AuthCore `__slots__=()` fragility).

**POSTPONED 2026-07-19 — group runtime spec (was ◆D10b).** The group runtime
spec (`python=`/`code=`/`env=`, backends venv/path/pyz/OCI) concerns only the
complete server and deserves a broader discussion: parked, to be resumed with
the orchestration-phase design (see Q9). The alternatives examined (declarative
multi-backend spec vs venv-only vs OCI-first) are recorded in the 2026-07-19
session; recommendation on the table was the declarative form with venv-only
initial implementation.

**D19 — Chains of simple objects, usage levels.** Both hierarchies — servers
AND applications — are built as chains of simple objects (lean base →
capability mixins → shipped compositions). The two packages expose distinct
**usage levels**, each usable on its own: bare base server (embed an app, no
channels) → public server (config, auth, `_server`) → orchestration (groups,
SPA, batch) → multi-machine hierarchy. A consumer enters at the level they
need and extends with the same gesture the framework itself is built with.

### Ratified 2026-07-19 (decision session, part 2)

**D20 — Dev = same topology as prod (was ◆D13, choice A).** Ratified as
written in §3: dev runs the shape of prod with minimal numbers (2 processes:
public with in-process manager + 1 worker). The "single" dies as a class and
becomes a configuration (1 group × 1 worker) — this resolves the suspended
single/multi doubt of 2026-07-14 by elimination. Step-debugging: the internal
server runs STANDALONE from the same config — no special inline mode, ever.

**D21 — Selective reload with the daemon-parity target (was ◆D14, amended).**
The primitive and the three triggers stand as written in §3 (retire+respawn;
dev watcher / ops command / deploy version switch; dev reload = degenerate
rolling update). The amendment sets the TARGET: **live pages survive ANY
restart** — parity with the old daemon, achieved WITHOUT a daemon: state
survives process *generations*, not inside an eternal process.

- **Level 1 — worker restart**: page-state transport worker→worker (register
  CONTENT serialized, rebuilt by the new process); ONE primitive shared with
  move/rebalance — reload is "move every user to the future worker". The
  WebSocket stays up on the public server: invisible to the browser.
- **Level 2 — full restart / ordered abort (Ctrl-C)**: EVERY register at
  EVERY level (public: connections/sessions/users; manager: user→worker;
  worker: pages) serializes its own CONTENT on shutdown (data, not objects;
  TTL) and rehydrates at boot — single writer per level, per D12; the
  channel's anti-entropy realigns the projections. The client **re-attaches
  by page_id** (reconnect with backoff + session cookie): re-attach is a
  RIGHT of the protocol, not an error path. No page reload, browser data
  untouched.
- **Hard crash** (kill -9, segfault): shutdown hooks don't run — best
  effort via periodic register checkpoint (dev), explicit fresh-page
  fallback. Never a silently broken rehydration.

Constraints born now (cheap at birth, expensive to retrofit): registers hold
**serializable content by construction** (never live objects as truth);
schema incompatibility on rehydration → explicit fresh page. The parked
register-persistence design of 2026-07-12 (pickle of register CONTENT, TTL)
is promoted from single-SPA case to general mechanism. The shared-code
caveat of the triggers stands, but the full restart no longer kills pages.

**App placement table → ratified as-is** (see §3). Q7 (pooled mode for
stateless apps) stays parked; the table does not preclude it.

**Q4 resolved — the `kajenn-*` family with a documentation umbrella.**

- The current `kajenn` repo dies as a CODE repo and remains the
  **documentation repo of the family** (issue history, docs/architecture,
  RTD). The transformation happens AFTER the Q5 salvage; until then the
  already-decided freeze applies. The code stays in git history forever.
- New repo **`genropy/genro-asgi-core`** → `sub-projects/kajenn-core`
  (org verified 2026-07-19: the family lives on `genropy` — kajenn and
  genropy-asgi remotes — not on `softwellsrl`, which hosts only the meta
  repo); dist name **`kajenn-core`**, import name **`kajenn_core`**
  (repo = dist = import, ecosystem convention; a `kajenn.*` namespace
  would be shadowed by the old regular `kajenn` package wherever the two
  dists share an environment); version starts at **0.1.0** — a rewrite does
  not inherit 0.13.x. PyPI names `kajenn-core` and `kajenn-server`
  verified free 2026-07-19.
- PyPI **`kajenn` stays parked at 0.13.0** (today's dependents keep
  resolving it); it may later become a meta-package installing
  core + orchestration — decided when both exist.
- **Q3 rescoped**: only the suffix within the family remains open
  (`kajenn-<suffix>`; note: "server" would mislead — the servers live
  in core). Decided at phase 2.

**Annotation to D8/D19 — the family is open.** Future spin-offs
(`kajenn-spa`, `kajenn-task`, ...) are expected and welcome. They
are born as DIRECTORIES with a public API inside the orchestration package
(`spa/`, `task/`, `pool/`) and are promoted to repos when their shared
substrate (hub, pool, manager, registries) has stabilized as a public API:
**the split follows stability, never anticipates it**. The orchestration
package's internal layout is drawn from day one as if the cuts were coming.
A concrete hint that the cut lines are unknown today: "task" already has two
souls — the spool (state=position, nearly standalone) and the batch
commander (coupled to pool/hub/manager). Smaller repos DO help LLM-assisted
work (focused context, small public API, short suites) — but only when the
boundary is a STABLE contract; an unstable boundary costs lockstep version
bumps at every turn.

### Ratified 2026-07-19 (decision session, part 3)

**D22 — The cut restated: core is the complete mono-process async server;
the orchestration package is defined by two primitives.** (Extends ratified
D9; amends the scope of placement-table row 1; gives the spool soul of the
D8/D19 annotation its home.)

**Core (`kajenn-core`) targets the Starlette/FastAPI territory**:
everything a modern async web framework offers within ONE process, complete
and usable on its own — the mono-process async server:

- URL topology (D3/D9): primary app, mounts, the one demux;
- authentication and HTTP sessions (identity BETWEEN requests);
- middleware chain and plugins;
- application base classes AND the complete OpenAPI and MCP applications
  (widens placement-table row 1, which said "base classes" only);
- the full `_server`;
- storage AND db handlers — the transversal config sections of D15
  (databases, storage) get their server-side counterparts in core;
- the task model + local execution: the spool (state=position, invariant
  §5.7), task lifecycle, terminal batch_id — executed in-process on the
  core's own pool/loop (background-task style).

**The orchestration package is defined by two primitives** — everything in
it derives from them:

1. the USER as the load-partition key (user→group→worker): sticky routing,
   the D12 three-projection registries, rebalance/move, the Form C manager;
2. PAGE_ID as the unit of living state: page registries, datachange,
   subscriptions, per-page ordering, the D21 re-attach by page_id.

The survival line makes the cut testable: what must survive a worker's
death lives in core (sessions, logins, spool state on storage); what dies
and is rebuilt with its process lives in orchestration (pages, pending,
subscriptions) — the same line D21 draws.

**Batch is the degenerate case — workers without users**: it shares the
process machinery (pool/hub/spawn, dedicated workers) with neither
primitive. Its MODEL (spool, states) lives in core; its distributed
execution (commander app, dedicated workers) in orchestration.

Consequence for §7: phase 1 widens and is re-phased into internal core
steps (config+middleware+auth/sessions → storage+db → OpenAPI/MCP apps +
plugins → full `_server` → tasks local); phase 0 is untouched. Q3 (the
orchestration suffix) stays open; the two-primitive identity strengthens
`spa` as a candidate name, with batch riding along.

### Ratified 2026-07-21/22 (implementation sessions, core 1d wave 1)

**D23 — Wave record-keeping: the per-wave rulings are part of this log.**
Since D22 four implementation waves were ratified and delivered
(1a `cc85eef`, 1b `4b8000d`, 1c `53b4e38`, 1d-wave-1 on branch
`feat/phase-1d-server-login`) with their decisions recorded in per-wave plan
files only — against the §1 rule that every ratified decision is APPENDED
here. This entry reinstates the rule and incorporates the wave rulings by
reference (the per-wave plan files and each wave's `.claude/REVIEW.md`
remain the detailed record). The 1d-wave-1 rulings, compressed:

- `_server` = ONE `ServerApplication(OpenApiApplication)` with a
  full/minimal `profile` chosen by the server (D4/D6) — not two classes;
  auto-mounted by a hook at the end of `AsgiServer.__init__`, so a
  hand-built server gets it too ("automatic, not configured").
- Dual-mode login = TWO routes (`login` JSON POST + `login_page` HTML GET),
  never in-handler `Accept` sniffing; the user-record key is `identity`.
- Handlers are PURE (the old ambient `server.request`/`server.response`
  never returns); a handler needing the live request DECLARES a `request`
  parameter injected by `bind_kwargs`. *(The parameter concession is
  withdrawn by D31: handlers never receive the request; effects are
  returned, the dispatch executes them.)*
- The login-cookie policy was first ratified as "option A" (middleware
  emits on session change) and superseded the same day by D24.
- The two-stage live-config architecture (config as live object,
  `apply_configuration`, hot/cold changes) stays parked as a future macro.

**D24 — Login attaches identity in place: the session id never changes.**
(Supersedes the wave-plan "option A" ruling.) A session is a CONTINUUM: the
login is an event that enriches it with an identity, not a replacement.
`Session.attach_avatar(avatar)` mutates the existing session; id, `data`
and `meta` are untouched — whatever an anonymous visitor accumulated (a
cart, a history) survives the login, and the cookie the client already
holds stays valid. `promote_session(request, avatar)` delegates to it. The
`SessionMiddleware` issues a cookie ONLY when a new anonymous session is
created; no login-time cookie exists. Session-fixation defense is upstream
cookie hardening (HttpOnly, SameSite, the token is never read from the
URL), NOT id rotation — the codebase does not accumulate ceremony against
threats its cookie discipline already forecloses. Open point for 5b:
`FileSessionStore` persists only at creation (post-creation mutations —
cart, avatar — live in the process cache only); decide the write-back seam.

**D25 — Invariant 10 enforced; `attach_instance` is transitional and will
be removed.** The wave initially attached the zero-route `PasswordMethod`
to the routing tree (the exact §5.10 anti-pattern, under compliant-looking
prose); now enforced in code: `AuthSection.register()` links a method's
router ONLY when it owns routes — a route-less method lives in the ordered
registry that feeds `login_methods`, never in the tree. On the attach API:
the 2026-07-17 ruling STANDS, non-negotiable — `attach_instance` will be
removed in favor of declarative branches (`add_branches` factory specs).
Verified on genro-routes 0.28.0: branch specs default to `lazy=False` —
branches are EAGER BY DEFAULT (materialized at first tree access, or
immediately when declared after the eager pass), so the declarative model
satisfies "routes exist from boot" with no lazy opt-in needed in 1d; a
lazy branch is still describable from its CLASS in `nodes()`. Migration
rulings: the parent rides the spec (`{"cls": X, "params": {"application":
self}}` — the rule-7 dual relationship as data); the `register()`/
`attach_section` contract redesign (built instances → cls+params recipes)
is part of the migration design; the 4 call sites (`openapi.py:84`,
`openapi.py:122`, `server_app.py:140`, `auth_section.py:90`) migrate in
Macro 5b together with OIDC, which lands directly on branches.

**D26 — The `_server` profile flag is removed; the internal server is a
subclass.** (Supersedes the D23 wave-ruling bullet — "one
`ServerApplication` with a full/minimal `profile` chosen by the server".)
The profile was dormant code for an absent consumer — a runtime branch on a
shared class for a distinction that has no live second case; `7630055`
removed it. The future internal server will be a SUBCLASS overriding only
what it needs, not a flag: this is the more principled form — D16 ("the
class says WHO you are", extension = subclassing) and D6 (minimal surface by
construction) rather than a profile switch. The 5a-bis REVIEW items are all
closed by this point (`0bc609d` #6, `99ce7c3` #12, `360a4f9` #13, `7867420`
#14; #9/#11 ratified without code); as part of #6, `pydantic` and `openapi`
became FIXED server structure (armed on every router), so per-entry OpenAPI
controls always apply.

### Ratified 2026-07-23 (implementation sessions, core 1d wave 3)

**D27 — The OIDC login contract: authorization-code + PKCE, one method per
provider.** OIDC is a `redirect` `AuthMethod` (`OidcMethod`), one instance per
configured provider, registered on the `ServerApplication` from the `oidc()`
config elements (after `PasswordMethod`) and, unlike password, OWNING routes —
so it enters the routing tree (Invariant 10) under `/_server/auth/oidc:<code>/`
with a `start` and a `callback` route. The contract, ratified points:

- **PKCE always on (S256), the client_secret optional.** Every authorization
  request carries an S256 code challenge; a public client (no secret) works. The
  `client_secret`, when configured, stays inside the provider config, is sent
  only in the token exchange, and is never logged nor exposed by the descriptor
  (which carries id/kind/label/start-url ONLY — no client_id, issuer, or secret).
- **Discovery is lazy and cached per instance.** Provider metadata is fetched
  from `<issuer>/.well-known/openid-configuration` at FIRST use, never at
  construction — a server booting while its provider is unreachable must not die.
- **`start` mints and stores; `callback` converges.** `start` mints a fresh
  `state` + PKCE `verifier`, stores them and the `safe_next_path`-guarded `next`
  under `oidc.<method_id>.*` in the session `data` (`mark_dirty`), and 302s to the
  authorization endpoint. `callback` checks the `state` (mismatch/missing → 400,
  never retried — CSRF binding), exchanges the code on the token endpoint (PKCE
  verifier + secret when set), and validates the `id_token`: RS256 signature
  against the provider JWKS (`jwt.PyJWKClient`, no new dependency — pyjwt +
  cryptography are runtime deps), audience = the client_id, issuer = the
  configured issuer. Any verification failure → 400.
- **Identity and tags are per-provider config.** The avatar identity is the
  `identity_claim` claim (default `email`), the tags the provider's configured
  `tags` (default `[]`). Success converges on the SAME outcome every method shares
  (D24): `session.attach_avatar` in place, session id UNCHANGED, one-shot
  `oidc.*` keys cleared, 302 to the stored safe `next`. Errors answer
  `HTTPBadRequest` (400) — coherent with the browser-driven flow, not a JSON body
  a browser would render as text.

**D28 — Server-side login lockout: a store-backed counter with exponential
backoff (REVIEW #9).** The password login enforces a lockout whose failure
counter LIVES ON the user's store record (`failed_attempts` int,
`last_failed_at` epoch float) — store-backed, so it survives restarts and is
shared across processes on a shared store. After `max_attempts` consecutive
failures (default 5) further attempts are refused until
`backoff * 2**(failed_attempts - max_attempts)` seconds (base default 30) have
passed since the last failure; a success resets the counter. Attempts refused
DURING the lock window do NOT increment the counter nor touch `last_failed_at`
— anti-DoS: an attacker hammering a locked identity cannot extend a legitimate
user's lock. Known-identity failures surface the server-computed
`remaining_attempts`; unknown identities have no record, hence no counter and no
such field. The policy is tuned by the config's `login(max_attempts=, backoff=)`
element. Per-IP rate limiting is a FUTURE middleware concern, not this handler's;
OIDC never routes through this path, so an OIDC login leaves the counter
untouched.

---

## 3. Decision log — discussed, unopposed (◆ confirm in bulk at review)

### ◆D9 — The package cut line
kajenn governs the **topology of URLs** (how many apps in ONE process —
mounts included: the mount dict IS the one demux mechanism of D3, so it stays
in the minimal package). The orchestration package governs the **topology of
processes** (pool, spawn, forwarding, SPA roles). A mono-app minimal package
would force the upper package to add multi-app from outside = the old dual
dispatch reborn.

### ◆D10 — The channel cut and the group runtime spec
**In the minimal package**: the frame protocol (tiny, zero deps) and the
**child side** (`ChannelClient`) — knowing how to BE a child is part of what
a server IS (D2). **In the orchestration package**: the hub (parent side),
the `ProcessPool` (spawn, supervision, relaunch, scale), the judgments
(occupancy, rebalance, move). The minimal knows how to *be* a child; the
orchestration knows how to *have* children. Dependency direction: the upper
hub imports the protocol from below, never the reverse.

**A group declares a runtime SPEC** (`python=`, `code=`, `env=`); spawn is a
function from that spec. Same-machine backends of the same abstraction:
current interpreter | venv (strongest isolation; exists today —
`pool.py:126` `executable or sys.executable`, `python=` in the group grammar)
| code path via `PYTHONPATH` (same deps only) | zipapp `.pyz` (single
immutable artifact; C extensions need shiv/pex extraction) | OCI (future,
already foreseen). Multi-machine insurance, four sentences: *group =
declarative runtime spec; spawner = replaceable actuator (local today, the
sub-commander of D11 tomorrow); channel address = uds|tcp (tcp already in the
old code, `channel.py:39`); storage = a service with backends.* Never
implicit localhost in new code. Artifacts that travel (pyz, OCI) are the
natural bridge to remote machines — venvs do not travel.

### ◆D13 — Dev = same topology as prod, minimal numbers
Process counts:

| Configuration | Processes | Who |
|---|---|---|
| Dev minimum (parity) | **2** | public (commander + in-process manager) + 1 worker |
| Dev, 2 groups | 3 | public + 1 worker per group |
| Prod, 1 machine, G groups × W workers | 1 + ΣW | managers in-process cost nothing |
| Prod + batch | +1 | the dedicated batch worker |
| Multi-machine | 1 + Σ(1 + W_m) | 1 sub-commander (that machine's public) + its workers |

The strongest reason is **serialization**: in-process, objects pass by
reference (mutation aliasing "works" until a process boundary appears);
multi-process dev exercises the channel, ordering, outbox, worker death,
forwarding, sticky cookies from day one. Consequence: **the "single" dies as
a class and becomes a configuration** (1 group × 1 worker) — resolving the
suspended doubt on the single/multi dual track; the exclusive single code was
there to save one ~100MB process. Step-debugging: run the internal server
**standalone from the same config** (it IS a server rebuilt from config) —
no special inline mode, ever.

### ◆D14 — Selective worker reload
One primitive — the pool's retire+respawn (already the supervision mechanism)
— with three triggers: (1) **dev**: a watcher on the app sources (dirs derived
from the group's runtime spec); (2) **ops**: an endpoint/CLI "restart group X
workers"; (3) **deploy**: the group version switch, graceful variant (spawn
new, wait REGISTER, retire old — blue/green). Dev reload is the degenerate
case of the production rolling update.

Survives a worker restart: sessions and logins (public server), browser
WebSocket connections (terminate on the public), commander/manager registries
(the manager reassigns), sticky routing, config. Reset: worker-local state
(pages, pending, subscriptions). Caveat: a change to **shared** code
(config.py, framework, public middleware) requires the full restart — the
watcher must distinguish app dirs from the rest, or dev hides errors again.

### ◆ App placement table

| App type | Code lives in | Runs on | Configured by |
|---|---|---|---|
| Plain API (REST/OpenAPI/MCP) | user package; base classes in kajenn | directly on the public server | a mount in the config |
| SPA (stateful) | user app code; commander app + manager in orchestration pkg | commander app on the public; app code in the workers | a mount with a `groups` block |
| Batch | commander app in orchestration pkg | commander app on the public; execution in batch workers | a mount in the config |
| `_server` | kajenn (essential) / rich panels in orchestration pkg | automatic: full on public, minimal on internal | not configured: it is there |

---

## 4. The base server contract

```
BASE SERVER (common to every server)
├── uvicorn loop                 (the engine serving requests)
├── thread pool                  (one, for blocking work; async stays on the loop)
├── channel to the parent        (connected if parent= given; off if progenitor)
├── PRIMARY app                  (ALWAYS present: answers "/" and the unclaimed rest)
├── secondary mounts             (apps by URL prefix; may be empty)
├── lifespan                     (starts/stops apps in order, reverse on shutdown)
├── request registry             (current request + in-flight picture, D5)
└── authenticate() / session()   (base answers: nobody / none)
```

```
PUBLIC SERVER (the exposed one)            INTERNAL SERVER (the pool child)
= BASE +                                   = BASE +
├── real auth (from config)                ├── nothing: auth/sessions stay
├── real sessions (store)                  │   "nobody" BY CONSTRUCTION (D6)
├── storage                                └── minimal "_server"
├── middleware chain                           (metrics and commands only)
└── full "_server" app
    (login, monitor, openapi)
```

URL examples — public server, primary `shop`, secondary `api`:

```
GET /                    → shop      (primary)
GET /prodotti/12         → shop      (no mount claims "prodotti")
GET /api/ordini          → api       (secondary mount)
GET /_server/monitor     → _server
```

Internal server hosting one SPA app:

```
GET /                    → the hosted app  (primary, only one)
GET /_server/metrics     → the worker's minimal _server
```

**App-side contract (born in phase 0, tested with a throwaway app):**
an app is an ASGI callable with `code` and `mount` (its identity — D29;
the contract predates `a1a8f7e` and said `mount_name`), a `server` property assigned by
the server at mount (ownership channel, one direction), lifecycle hooks
(`on_startup`/`on_shutdown`, sync or async), and the dispatch protocol toward
the server (thread pool for sync handlers, registry for the current request).

**Phase-0 test checklist:**
- boot on port 0; primary answers `/` and everything unclaimed;
- demux: secondary mount → its app; everything else → primary;
- lifespan: startup in order, shutdown in reverse, one app's error does not
  block the others;
- pool: sync handler runs on the pool (thread identity asserted), async on
  the loop, lazy provisioning, teardown;
- registry: current request correct under two CONCURRENT requests (the
  ContextVar test); in-flight count;
- channel: child connects to a fake hub over UDS, REGISTER, EOF behavior;
- `authenticate()`/`session()` answer "nobody/none";
- the websocket socket in `__call__` exists and is empty.

---

## 5. Invariants & lessons to carry over (with sources in the old repo)

1. **Loop affinity** of semaphores/pools: build lazily on the running loop
   (`base.py:175-190`).
2. **Thread-correct teardown**: a thread-local resource is released on the
   thread that opened it (`make_callable` contract,
   `asgi_application.py:131-173`).
3. **Demux security invariant**: a node that exists but is denied answers
   natively with its 401/403 — it NEVER falls through to the hosted app
   (`spa_application.py:117-152`).
4. **Origin gate** on WebSocket handshakes (`handler.py:129-144`).
5. **Identity precedence**: Authorization header wins over session
   (`asgi_application.py:290-309`).
6. **Occupancy chain**: worker = sensor, manager = local judge, commander =
   budget setter (evolution of the ratified elastic-pool contract).
7. **State = position** in the task spool; a state transition is a folder
   move, atomic on one mount (`tasks/spool.py`).
8. **Serialization at process boundaries**: no reference aliasing — what
   crosses a boundary is serialized, and dev topology (◆D13) makes this true
   from day one.
9. **Contract tests on every interface with multiple implementations** — the
   lesson of finding A1 (HTTP and WSX dispatch diverged because nothing
   forced them to stay equal). Applies to: group manager (local/remote),
   storage backends, session stores.
10. **Never routing as registry**: zero-route nodes are never attached
    (old `PasswordMethod`, `auth_method.py:90`).

---

## 6. Open questions

- **Q1** — RESOLVED 2026-09-06 (see `internals/10_server/055_websocket/`):
  ONE dispatch engine with two transports. A WSX message becomes a synthetic
  HTTP request with `method: "WSK"`, resolved on the same routing tree with the
  same filters, so ctx, `on_route_resolved` and the cleanups are the HTTP ones
  and the A1 gaps cannot reopen. The server holds the connection
  (`BaseServer.on_websocket`), the identity is judged once at the handshake, and
  a message for the SPA becomes an ordinary CALL on the worker's lane. Decided
  in the investigation of 2026-09-05/06, tracked in
  [055 websocket](internals/10_server/055_websocket/decisions.md), and BUILT in
  #68 — the six phases of code landed on 2026-09-06/07, and what the code holds
  today is [055 websocket status](internals/10_server/055_websocket/status.md).
- **Q2** Middleware chain: in the base or only on the public server?
- **Q3** — RESCOPED 2026-07-19 (see §2, part 2): the orchestration package
  lives in the `kajenn-*` family; only the suffix is open ("server"
  would mislead — the servers live in core). Decided at phase 2.
- **Q4** — RESOLVED 2026-07-19 (see §2, part 2): family scheme —
  `genropy/genro-asgi-core` (dist `kajenn-core`, import
  `kajenn_core`, 0.1.0); the old repo freezes now and becomes the
  family's documentation repo after the Q5 salvage.
- **Q5** Salvage list, item by item: `executors/pool` + `ChildRunner`, task
  spool, storage, WSX protocol, datastructures, auth core — enter the new
  repo almost as-is after a terminology pass.
- **Q6** Data plane: default is full hierarchy (top → sub-commander → worker;
  the second hop is loopback); direct top→worker forwarding only if latency
  ever demands it (parked).
- **Q7** A "pooled" serving mode for stateless apps (a plain API behind a
  group manager) — emerged from the placement table; not needed now.
- **Q8** Decisions from the architecture doc not absorbed here: ghost stores
  → always-present objects with backends; the "worker"/"broadcast" glossary;
  `Lifespan` vs `ServerLifespan`; plugin-config persistence (write-only
  today); MCP single face; File-store common base.
- **Q9** Group runtime spec (postponed ◆D10b, 2026-07-19): the declarative
  form and its backends — to be discussed with the complete-server design.

---

## 7. Step plan

Re-phased by D22 (core widened to the complete mono-process server; internal
ordering of 1a–1e provisional, refined per-phase at workflow-writing time):

| Step | Delivers | Package |
|---|---|---|
| **Phase 0** | base server + app contract + full test suite (checklist §4) | kajenn-core |
| **Phase 1a** | config (site + role projections), middleware, auth, HTTP sessions | kajenn-core |
| **Phase 1b** | storage + db handlers | kajenn-core |
| **Phase 1c** | application classes: complete OpenAPI + MCP apps, plugins | kajenn-core |
| **Phase 1d** | full `_server` | kajenn-core |
| **Phase 1e** | tasks: spool model + local (in-process) execution | kajenn-core |
| **Phase 2+** | orchestration on the two D22 primitives: SPA commander app, group manager (Form C, both placements), pool/hub, batch distributed execution, rich monitor, selective reload | orchestration pkg (`kajenn-*`) |

Phase boundaries are package boundaries (D7/D8): 0–1e are core, 2+ is the
orchestration package. Phase 0 is the birth act of the new repo, which
forced Q4 first (resolved).

---

## 8. Decision log — later ratifications

### Ratified 2026-07-24 (naming reversal)

**D-rename — the package IS `kajenn`; it continues the PyPI name.**
(Supersedes the naming half of **Q4**, 2026-07-19; the family-scheme and
documentation-umbrella parts of Q4 are unaffected.)

- Dist name and import name are now **`kajenn` / `kajenn`** (was
  `kajenn-core` / `kajenn_core`). The name `kajenn-core` is
  retired everywhere in code, config and repo.
- Version is **0.20.0** and the package **continues the existing PyPI
  `kajenn` line** (parked at 0.13.0), rather than starting a fresh
  `0.1.0` under a new name. 0.20.0 becomes the new latest; `kajenn
  <= 0.13.0` remains resolvable for existing dependents.
- Development status moves **Alpha → Beta**.
- The Q4 concern — a `kajenn.*` namespace shadowed by the old regular
  `kajenn` dist in a shared environment — no longer applies: the old
  project is **frozen** (renamed `genro-asgi-legacy` on GitHub, its CI and
  PyPI-publish workflow removed) and will not publish again. There are no
  two dists publishing under `kajenn`: one name, one continued line.
- GitHub: `genropy/genro-asgi-core` → `genropy/genro-asgi`; the former
  `genropy/genro-asgi` (old code) → `genropy/genro-asgi-legacy`.

The historical Q4/D22 entries above are kept verbatim as the record of why
`-core` was originally chosen; this decision is the correction that cites
them, per §1 (history is never rewritten).

### Ratified 2026-07-29 (config layer refounded on genro-builders contrib/config)

**D-config — the configuration layer is the contrib `config` dialect, the
server reads itself, and values resolve in place.**
(Supersedes the *materialization* half of **D15**, 2026-07-19; D15's premise —
one config describes the whole site — stands unchanged.)

- The dialect is **`AsgiConfigBuilder(ConfigBuilder, AsgiServerGrammar)`** over
  genro-builders 0.22 `contrib/config`: one `configuration` root, overridden
  with the full closed section list, every section a singleton (`[0:1]`) so
  every path is stable and hand-writable. The grammar a class speaks is its
  class attribute `.grammar` (`AsgiServer.grammar`,
  `BaseApplication.grammar = ApplicationGrammar`), and an `application` mounts
  its app's own grammar by reference (`app_class:grammar`).
- **The server is self-configuring**: `AsgiServer(config=source)` builds its own
  `ConfigurationHandler` in `__init__`, derives its constructor kwargs from it,
  instantiates the configured applications and registers the configured
  databases. Explicitly passed kwargs win over configured ones, wholesale per
  kwarg. The door is exposed read-only as `server.config`, callable by path over
  the inherited four-layer read stack (written value → element signature default
  → call-site `default=` → noisy `KeyError`); an application reads its own
  subtree through `app.config(path)`, which prefixes `applications.<code>.` and
  delegates to that same door.
- **Resolvers replace `^pointer`**: a value that comes from outside is a
  `BagResolver` (`EnvResolver`, …) stored where the value would go, resolved at
  READ time. The pointer model resolved once at materialization and silently
  dropped whole layers — `storage_key` was the case that proved it — so
  `resolve_pointers` and `configurable.py` are deleted with the idiom.
- **`materialize()` and `Projection` are removed**, and with them `role=`, the
  `app=` projection parameter and the multi-builder claim machinery
  (`ServerAppConfig`, `config_class`, `_claim_app_configs`). There is one
  direction of dependency: the server builds its handler and asks it for
  values; nothing builds a server from a config. D15's per-role projection is
  superseded — one process reads its own configuration — and the D15 sketch's
  `app_class="pkg:Class"` strings become imported classes.
- **The `auth` section and the separate `_server` config-class dissolve into one
  `authentication` section**: `admin_password` (a resolver — a literal string is
  a boot error), `users`/`tokens`, `login`, `oidc` as a keyed collection of
  `provider(code=…)`, and `credentials` (`basic_user`/`bearer_token`/`jwt`)
  folded by the handler into the exact dicts `AuthCore` already consumes.
  `AuthCore` itself is not touched: only how its values travel changes.
- The `_server` app remains **automatic, not configured** (D4): the
  `authentication` login surface reaches it as forwarded kwargs at mount time.
- Pins: `genro-builders>=0.22.0`, `genro-bag>=0.20.0`.

### Ratified 2026-07-30 (the `kajenn` command)

**D-cli — the package ships a command-line launcher; a `config.py` plus
`kajenn serve` is a complete deployment unit.**
(Supersedes no ratified decision: the absence of a CLI was a *consequence* of the
spec-first rewrite starting from the core, never a decision on the merits. W2c,
decided 2026-07-25.)

- The console entry is **`kajenn = "kajenn.__main__:main"`**, built on
  stdlib `argparse` — no new dependency. Subcommands: `serve`, `apps`,
  `stop <name>`, `remove <name>`; `serve` options `--host`, `--port`,
  `--reload`, `--name`. Exit codes: 0 success, 2 usage, 1 runtime error on one
  stderr line.
- **`serve <source>` resolves in three steps**: an `application=<target>`
  assignment (quickstart — the class is instantiated with no arguments and
  handed to `AsgiServer(applications=[...])`), an existing `.py` path (handed to
  `AsgiServer(config=<absolute path>)`), otherwise a registered name.
  The **config.py contract is the contrib handler's own** — "must define
  exactly one `ConfigBuilder` subclass", which in a kajenn recipe is an
  `AsgiConfigBuilder` — and the command ships **no loader of its own**: the
  handler's error surfaces unchanged. The command puts the config file's
  directory on `sys.path` before loading it (a config.py imports its sibling
  modules; `python -m` grants the working directory, the console script does
  not — the launcher makes the two invocations equivalent). Explicit
  `--host`/`--port` travel as `AsgiServer` kwargs, so D-config's "explicit kwarg
  wins, wholesale per kwarg" rule does the precedence and the command computes
  nothing.
- **The registry is a pointer store, never a copy**: `~/.kajenn/apps/<name>.json`
  holds the source string and the given options, `run/<name>.pid` the pid.
  Relaunching by name always runs the current code. A pidfile is **never
  trusted** — missing, unreadable or naming a dead process all read as "not
  running" — so a crashed server shows as stopped, not as a phantom.
- **`--workers` is excluded**: multi-process supervision is genro-juggler's
  territory. `--debug` is excluded as well (never discussed on the merits).
  `--role`/`--app` have no meaning since D-config removed `Projection`.
- **Two derogations from the coding rules, confined to `__main__.py`**: one
  module-level function (`factory`) and state in the environment
  (`KAJENN_LAUNCHER`, one JSON object: one source key plus explicitly-given
  host/port only). Both are forced by uvicorn's reload supervisor, which accepts
  only an import string and starts a fresh process per restart — nothing of the
  parent survives except the environment. Neither leaves this module, and the
  core (`server.py`, `asgi_server.py`) is untouched by the whole feature.

---

### Ratified 2026-08-24 (demux settled; the site index)

**D29 — No application is required on the site root; the demux has four
branches and the uncovered root serves the site index.**
Commit `a1a8f7e` (2026-07-25) overturned D2's "primary app, always present"
and D3's two-branch demux without a log entry; this entry records the overturn
(per D23) and completes the design. An application is identified by its `code`
and answers under its `mount`; the site root (`mount=""`) is a mount like any
other and may be absent. The demux: first path segment matching a mount → that
application, segment stripped; else the root application, path unchanged;
else, for `/` with a `default` declared → 307 to it, method and body
preserved; else, for a bare `/` → the **site index**, an HTML page the server
itself serves, carrying the kajenn logo and links to the mounted
applications (codes starting with `_` excluded); else 404. The index is
disabled by a configuration switch; disabled, `/` answers 404. Deep unmatched
paths are always 404. The index page and its switch are not yet implemented;
their names follow the `_TBD` rule.

**D30 — Request construction belongs to the application; the registry
records, never builds.**
Amends D5, whose duty (1) — "create the right request from the scope" —
described the old repository's `RequestRegistry.create` factory. Ratified:
the server dispatches the raw ASGI scope; the owning application builds the
`Request` its handlers receive (each surface builds the request it needs);
the registry keeps duty (2) unchanged — the thin in-flight picture — plus the
"current request" ContextVar on the registry instance. The reopened websocket
branch follows the same rule: whoever owns the socket builds its object (the
channel), and the registry may register it as an in-flight item — register,
never construct. Long-lived sockets and millisecond requests do not share a
factory.

**D31 — Handlers never receive the request: the valve of D23 is closed.**
D23's main rule (handlers are PURE, the ambient `server.request` never
returns) stands. Its concession — "a handler needing the live request
DECLARES a `request` parameter injected by `bind_kwargs`" — is withdrawn: it
was never implemented for hosted applications (the injection existed only on
`ServerApplication`, under the name `_request`), its only real consumers were
the three authentication handlers, and a `request` parameter in the signature
breaks surface-agnosticism (the same handler is served by HTTP and MCP).
The seam is replaced by declared effects: an authentication handler RETURNS
its result (the minted `Avatar`, the OIDC flow state); the caller of the
node — kajenn's dispatch, which holds the live `Request` — recognizes the
returned effect and executes it (`session.attach_avatar`, the OIDC parking).
Each surface executes its own effects at its own call point; a test calls the
handler bare and sees the pure value. genro-routes is untouched — the call is
kajenn's. The `_request` injection and the `ServerApplication.bind_kwargs`
override are dismantled accordingly (implementation pending).

### Ratified 2026-08-25

**D32 — Static only where dynamic cannot be had.**
Staticity is never a presupposition and never a goal (owner, 2026-08-23). It
is accepted where making the thing dynamic has not been managed — and where
it is accepted, the reason is written down where the limit is accepted. A
design may not celebrate a fixed set, a boot-time-only decision or a
restart-to-change behaviour as if immobility were a virtue. The principle
governs every design in this repository, the technical dossier included.

**D33 — The `_server` app is declared, not automatic; the core never asks for
user and password (supersedes the "automatic, not configured" half of D4).**
Ratified 2026-09-12, release 0.46.1. The management application lives in its
own top-level package of the distribution, `kajenn_server_app`, and the
core imports nothing from it: it is declared in the configuration like any
other application (code `_server`), with its own grammar elements `login`,
`oidc`, `provider` under the `application` element. The core authenticates by
header credentials and session avatar only; the interactive methods (password,
OIDC) belong to the package, and a hosted application may keep its own login,
as genropy does. The fixed 401 → `/_server/...` redirect leaves the core: a 401
is bare, with `WWW-Authenticate`. Which application authenticates for which
path, and how shared users link by a unique code, is a later decision
(issue #83). The table row of `_server` above and the D4 mention in the wave
record are read through this decision. Register:
`temp/decisioni_serverapp_identita_2026-09-12.md` (D-SA-1..11).
