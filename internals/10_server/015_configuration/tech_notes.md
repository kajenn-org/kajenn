# Configuration — tech notes

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE


## Renewal evidence — 2026-09-08

The current implementation is recorded in [status](status.md), verified against
`2465fcc`. The August notes below are archival: their line references, coverage
numbers, test paths, missing-consumer assertions and proposed next steps are not
fresh measurements or current work authorization. The original detailed status
can be recovered with `git show 2465fcc:internals/10_server/015_configuration/status.md`.

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

For whoever works ON this entry. The working trail: what decided what, what is
easy to look for and not find, and what the next person needs to know.

## Classification and position

**A shelf** — a technical stratum, not a need an administrator names as such.

It was moved here on 2026-08-23. It used to be `100_configuration`, next to
last in the world, on the stated reason *"describe an installation once — every
recipe word is defined by now"*. That reason held for the **vocabulary** and
was wrong for the **mechanism**: nine entries came before it saying "the
configuration declares it" to a reader who had not yet been told what a
configuration is. The owner cut the knot by splitting the job rather than the
folder — the mechanism early, here; the vocabulary distributed to whoever owns
each section; and every entry closing with a whole runnable recipe instead of a
fragment. `100_configuration` therefore no longer exists (`git mv` at
`015_configuration`, so its history follows).

## Who stands on this

Everything that reads a value, which is nearly everything. The ones whose own
words live in this tree:

| Section of the tree | Owned by |
|---|---|
| `server`, `session` | [010 server](../010_server/README.md), [040 sessions](../040_sessions/README.md) |
| `server.tasks` | [070 tasks](../070_tasks/README.md) — declares its own grammar |
| `middleware` | [030 middleware](../030_middleware/README.md) |
| `authentication` (+ 6 children) | [050 authentication](../050_authentication/README.md), login surface to [090 server-application](../090_server-application/README.md) |
| `storage` | [060 storage](../060_storage/README.md) — a mount point, no vocabulary here |
| `applications` | [020 applications](../020_applications/README.md); each entry's children are the app's own |
| `databases` | [065 db](../065_db/README.md) |
| `plugins` | [025 routing system](../025_routing-system/README.md) |
| `applications.<code>.orchestration` | [20_spa/020 orchestration](../../20_spa/020_orchestration/README.md) — the SPA front's whole pool subtree, NOT a top-level section |

A change to the read stack or to the layering reaches all of them. A change to
one section's words reaches only its owner.

## The working trail

**The founding ratification** — SPECIFICATION.md:772, *Ratified 2026-07-29
(config layer refounded on genro-builders contrib/config)*: the four-layer read
stack, an application reading its own prefix, and "explicitly passed kwargs
win, wholesale per kwarg". This is the entry's main source and it is worth
reading in full before touching anything.

**D15** (SPECIFICATION.md:171) — one config is the whole site, each process
materializes its role's projection. Half superseded: the `Projection` object was
removed, and the CLI ratification (SPECIFICATION.md:817) records the
consequence — `--role`/`--app` lost their meaning with it. So D15's *principle*
stands and its *mechanism* is gone; do not go looking for `Projection`.

**D23** (SPECIFICATION.md:418-419) — the live-config architecture parked as a
future macro. This is what §6 of the design builds on, and the reason §6 is
entirely unbuilt.

**The pool clause** — `elements.py:55-58`, and behind it R11 amended and R12
superseded on 2026-08-18 (`temp/design_m4_2026-08-18.md:5`, :186, :201): a pool
belongs to the application that owns it, so several SPA fronts on one server
are legitimate. The earlier reading had `commander` as a top-level section; the
owner corrected it. `test_config.py:885` is the guard.

## Traps

- **The dialect is not in this package.** `ConfigBuilder`, `ConfigHandler` and
  the four-layer read contract live in
  `genro-builders/src/genro_builders/contrib/config/`. Reading only
  `kajenn/config/` gives the grammar and the layering policy, not the
  reading machinery. `handler.py:13-17` of the contrib package carries the
  layering contract (`Bag.update`, lowest first, datastore not merged).
- **`apply_configuration` does not exist.** Zero occurrences in `src/` and
  `tests/`. Neither does any mutator on the handler. Searching for how a
  configuration is written at runtime finds nothing because nothing writes it —
  see the closing section of [status.md](status.md) for what *does* exist
  underneath (`SourceBag`, `Bag.subscribe`).
- **`openapi` is declared and read by nobody.** The grammar validates it
  (elements.py:362); the only other mention is the handler docstring saying it
  is skipped (handler.py:49). Do not assume a consumer exists somewhere.
- **Two reading rules, and the grammar picks.** A node with a CLOSED signature
  is read attribute by attribute *through the handler*, so signature defaults
  and resolvers are honoured; a node with open `**kwargs` is read in bulk
  through `builder.runtime_values` (handler.py:25-31). Adding an attribute to
  the wrong kind of node changes how it is read, not just where it lives.
- **`storage_mounts` anchors to the cwd at recipe time** (builder.py:133), and
  writes it absolute because the local backend rejects a relative string. A
  test that changes directory between building and reading will not see what it
  expects.

## Before the next step is written

`decisions.md` is 🔴, with seven open frictions. Four of them (S1-S4) are the
whole of §6 — the live tree — and they are the same subject as S5/S6 of
[010 server](../010_server/decisions.md) seen from the other side: there the
question is what falls away when immobility goes, here it is what has to be
built. **They should be settled together, in one conversation, or the two
entries will drift.**

S3 and S4 are the ones with real design content: whether a write is validated
before it touches the tree or after the notification, and what happens when one
subscriber complies and another fails. S4 has a precedent worth reading first —
the partially-applied-fold problem recorded as F48/F49 in the orchestration
register.

And S7 is a debt, not a defect: the recipe at the foot of `design.md` must stay
executable. Writing it found two real defects in it that reading had not (a
non-existent import, a storage path the backend refuses). The test that runs
every entry's recipe is decided and deferred — see the note in
`internals/00_overview/README.md`.
