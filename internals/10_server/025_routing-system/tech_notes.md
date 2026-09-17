# Routing system — tech notes

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE


## Renewal evidence — 2026-09-08

The current implementation is recorded in [status](status.md), verified against
`2465fcc`. The August notes below are archival: their line references, coverage
numbers, test paths, missing-consumer assertions and proposed next steps are not
fresh measurements or current work authorization. The original detailed status
can be recovered with `git show 2465fcc:internals/10_server/025_routing-system/status.md`.

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
`tests/core/`, `tests/server_app/` and `tests/docs/`; `tests/core/x/` still
contains an initializer.

## August audit trail (preserved)

For whoever works ON this entry, not for whoever reads about plugins. The
working trail: what decided what, what is easy to look for and not find, and
what the next person needs to know before touching it.

## Classification and position

**A shelf** — a technical stratum. Nobody asks for "a routing system"; they ask
for a URL that calls a method, for authorization, for a schema.

It was `025_plugins` until 2026-08-24, when the owner made the routing system
its subject and put the plugins after it. Two reasons, both worth keeping: the
plugins were unreadable without the tree they arm, and
[020 applications](../020_applications/README.md) was carrying two subjects — what an
application is, and what a routing tree is — which is why its friction tail grew
to twice any other entry's.

Fourth in reading order **and it comes after what it explains**, deliberately:
020 says an application *is* a routing class, and this entry then explains a
routing class in full. That inversion is the owner's, recorded as §1 of
[design.md](decisions.md).

## Who stands on this

| They lean on it as | Entries |
|---|---|
| the mechanism that arms their reader of the tree | [020 applications / openapi](../020_applications/openapi/README.md), [020 applications / mcp](../020_applications/mcp/README.md) |
| the filter half, applied during resolution | [050 authentication](../050_authentication/README.md) |
| the section it reads its switches from | [015 configuration](../015_configuration/README.md) |
| the thing it is confused with | [030 middleware](../030_middleware/README.md) |

A change to arming reaches every routed application. A change to the shipped
dialect plugin reaches only what publishes a schema.

## Most of the subject is in another repository

The tree, the walk, the three filters and the plugin base class are
genro-routes'. This package owns the arming, one dialect plugin and the
configuration section. When a claim here needs checking, the file is usually
under `genro-routes/src/genro_routes/`, not under `src/kajenn/` — and
`status.md` names which.

**All three filters have a live consumer, and they are not where you would
look.** Only tags are passed by the HTTP dispatch. **Channel is the MCP
engine's** (`mcp/engine.py:205` and `:292`) — that face walks the same tree with
its own channel, which is the whole mechanism behind "one tree, several
surfaces". Capabilities are the OpenAPI translator's, published as `x-requires`
(`translator.py:245-247`). Searching `routed_application.py` for the other two
finds nothing, and concluding they are unused is the mistake this note exists
to prevent — it is the mistake this entry's first draft made.

## The boundary with 020/openapi — read this before editing anything

`src/kajenn/plugins/openapi/` holds **two different subjects**, and only
one is this entry's:

- `plugin.py` — the router plugin. **This entry.**
- `translator.py` (307 lines) and `router_openapi` in `__init__.py` — the
  dialect that turns the neutral description into an OpenAPI document.
  **[020 applications / openapi](../020_applications/openapi/README.md).**

The test file splits the same way: of the 32 items in `tests/test_plugins.py`,
the eleven in `TestTranslator` (`:255`) and `TestRouterOpenapi` (`:311`) are
the dialect's. Whoever audits 020/openapi should take them, and should not be
surprised to find them under a plugin-shaped filename.

## The working trail

**Founding decisions** — D17 (capabilities are mixins, amending D2's closed
list), D16 (cooperative init), D26 (`pydantic`/`openapi` fixed structure, and
the reason: per-entry controls must always apply), the D22 scope ruling
(SPECIFICATION.md:363) that keeps the dialects in the core.

**The two rules with no D-entry** — no import side effect, and no module-level
mutable registry. They are the coding rules', and the modules state them in
their own contracts (`plugin_mixin.py:35-38`,
`plugins/openapi/plugin.py:35-37`). Searching the specification for them finds
nothing; they are real all the same.

## Traps

- **`default_plugin_registry` is a function on purpose.** It returns a fresh
  dict per call so that no module-level mutable registry exists. Turning it
  into a constant would be a one-line "simplification" that breaks the rule the
  docstring names.
- **But the registry that actually decides is not ours.** `arm_router` guards
  on `Router.available_plugins()`, which is the routing library's **class-level**
  registry (`genro-routes/core/router.py:116`, `:145`). Our per-server registry
  only decides *which class we would offer*; the library decides which class a
  code resolves to, once, for the whole process. Friction S2.
- **`plugin_registry` cannot come from a configuration.** It is a construction
  kwarg with no read-door helper. Searching `_configured_kwargs` for it finds
  nothing because nothing produces it. Friction S3.
- **Three always-present plugins, two mechanisms.** `pydantic` and `openapi`
  are the server's (`FIXED_PLUGINS`); `auth` is the application's, plugged in
  `RoutedApplication.__init__`. Counting the plugins on a booted tree gives
  three, and D26 mentions two.
- **The per-route option prefix is the plugin code.** `openapi_method`,
  `openapi_tags`, `auth_rule`. There is no registry of those names to grep: the
  prefix is the code and the suffix is whatever `configure` declares.
- **`entry_metadata` contributes nothing when every option is default.** It
  returns `{}` rather than an empty block, so a route with no publishing
  options carries no key at all — do not test for its presence.
- **Two keys, and the reader uses the other one.** A plugin's `entry_metadata`
  output lands at `entry["plugins"][code]["metadata"]`; the raw configuration
  lands at `entry["metadata"]["plugin_config"][code]`. The OpenAPI translator
  reads the second (translator.py:164). Writing a new dialect against the first
  is correct; assuming the shipped one does is not. Friction S4.
- **`_armed` is `False` on a freshly booted server.** Nothing is armed until
  something reads `route`. To inspect a tree without arming it, use
  `RoutingClass.route.fget(app)`.

## What was verified live while writing this

Six probes, all building real servers:

- the `design.md` recipe, whose five-row answer table is the probe's own output —
  including that `@route(openapi_method="delete", openapi_tags="admin")`
  publishes `/drop` as a `DELETE` with `tags: ['admin']`;
- a recipe naming a plugin code nobody registered: `ValueError`,
  *"Unknown plugin 'myown'. Register it first. Available plugins: auth,
  channel, env, logging, openapi, pydantic"* — loud, as the design claims;
- two servers in one process, each with its own class under the code `mine`:
  the first registers, the **second silently gets the first's class** on its own
  tree. Friction S2, reproducible in about twenty lines;
- `grep` for `entry_point` across this package, the routing library and both
  `pyproject.toml` files — nothing, which is friction S1;
- a plugin written from scratch — `BasePlugin` subclass, `plugin_code`,
  `configure`, `entry_metadata` — handed over as `plugin_registry=` and named in
  the description: it arms, and its block lands at
  `entry["plugins"]["owner"]["metadata"]`. That probe is also what showed the
  shipped `openapi` plugin's own block going unread (S4);
- `POST` on a route published as a `GET`: **200**. The verb is documentation
  (S7).

## Before the next step is written

`decisions.md` is 🔴 with seven frictions, all tagged by family. None of them is
settled here: they join the grouped pass over the skeleton (010, 015, 020, 025,
030) that the owner chose on 2026-08-23.

**S1 and S6 are the cheap ones.** S1 edits one line of the overview. S6 is one
sentence stating that three plugins are always present and that two mechanisms
put them there.

**S2 is the one with real content** and it is not only ours: the registry that
decides is the routing library's, so settling it may mean a change there, or a
guard here that refuses to arm a code whose registered class is not the one this
server offers. It shares the *silent* family with S7, and the two are unrelated
in mechanism — grouping them by family will put them side by side anyway.

**S5 — server-wide rather than per-application — is the one to think about
before anything is built on it.** If plugins ever become per-application, the
section moves from the server's vocabulary into each application's, which is a
configuration change of the kind 015 owns.

**S4 turned out to be bigger than a test gap.** The shipped plugin's
contribution has no reader at all, which is why nothing tested it. It closes by
deciding which of the two keys is the contract — so it is not the free step it
first looked like.

That leaves `steps/step_01/` without an obvious free content here. The nearest
is S7, and only in its documentation half: saying in the schema what the server
actually enforces costs nothing and decides nothing.
