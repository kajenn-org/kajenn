# Server — tech notes

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE


## Renewal evidence — 2026-09-08

The current implementation is recorded in [status](status.md), verified against
`2465fcc`. The August notes below are archival: their line references, coverage
numbers, test paths, missing-consumer assertions and proposed next steps are not
fresh measurements or current work authorization. The original detailed status
can be recovered with `git show 2465fcc:internals/10_server/010_server/status.md`.

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

For whoever works ON this entry, not for whoever reads about the server. The
working trail: what decided what, what is easy to look for and not find, and
what the next person needs to know before touching it.

## Classification and position

**A shelf** — a technical stratum, not a need a user or an administrator has.
Its entry says nothing about being one: the classification is an editorial
fact and lives here.

First entry of the first world. It **assumes nothing**: every concept it uses
is defined in its own page. Everything else in the dossier assumes it.

## Who stands on this

| They lean on it as | Entries |
|---|---|
| a capability mixin stacked on the server | [025 routing system](../025_routing-system/README.md), [030 middleware](../030_middleware/README.md), [040 sessions](../040_sessions/README.md), [050 authentication](../050_authentication/README.md), [060 storage](../060_storage/README.md), [070 tasks](../070_tasks/README.md) |
| an application it hosts | [020 applications](../020_applications/README.md), [090 server-application](../090_server-application/README.md) |
| the thing that supplies its shape | [015 configuration](../015_configuration/README.md) |
| the thing that boots it | [110 cli](../110_cli/README.md) |
| the thing that tears it down and rebuilds it | [120 restart](../120_restart/README.md) |

A change to the demux, to the application contract or to the four members
reaches all of these. A change inside a capability mixin reaches none.

## The working trail

**Founding decisions** — SPECIFICATION.md §2: D1 (public/internal server),
D2 (what the base owns), D3 (one demux rule), D4 (the `_server` app),
D5 (one request registry), D6 (no auth by construction), D7 (phase 0 and the
application contract). Then D16 (cooperative init), D17 (capabilities are
mixins, amending D2's channel clause), D18 (slots policy), D19 (usage levels),
D22 (the core is the complete mono-process server).

**The ruling that is not in the log — read this before searching.** Commit
`a1a8f7e` (2026-07-25, *"application identity (code + mount) and the
four-branch demux"*) introduced `code`/`mount`, the fixed application set and
all four demux branches, as a BREAKING CHANGE. It was **never appended to
SPECIFICATION.md**. Searching the specification for the 307, for `default`, or
for a server with no root application finds nothing — the only record is that
commit message. D23 (SPECIFICATION.md:398) exists precisely to reinstate the
rule that every ratified decision is appended, and the same session's other
rulings did get logged (the CLI entry at SPECIFICATION.md:817 cites "W2c,
decided 2026-07-25"). This is open friction S1/S2 in
[design.md](decisions.md).

**Later ratifications that touch this entry** — 2026-07-29 (config layer,
SPECIFICATION.md:772): the server reads itself from its configuration and an
explicit kwarg wins per kwarg. 2026-07-30 (SPECIFICATION.md:817): the CLI, and
with it the `serve` path whose host/port precedence is still untested (friction
S10).

**The parked direction that block 8 of `design.md` depends on** — D23,
SPECIFICATION.md:418-419: the two-stage live-config architecture (config as a
live object, `apply_configuration`, hot/cold changes) "stays parked as a future
macro". `decisions.md` §4 builds on it; nothing of it exists in code.

## Traps

- **`apply_configuration` does not exist.** Zero occurrences in `src/` and
  `tests/`; the configuration handler declares no mutator. The live-config
  machinery underneath *does* exist and is unused — see the closing section of
  [status.md](status.md) for the exact symbols.
- **`register_application` looks public and is not.** Two call sites in the
  whole repository, both inside a constructor.
- **`tests/x/` is empty, repo-wide.** Every test is classified as a contract
  test, so every failure is by rule a STOP. Friction S12; not this entry's to
  resolve.

## Before the next step is written

`decisions.md` is 🔴. The interview is `temp/interview_010_server.md`, twelve
turns; three of them (T1, T2, T3) decide things that bind all 31 entries and
should be settled before any other entry is audited.

The obvious content of `steps/step_01/` is the live-configuration mount and
unmount of design §4 — but it cannot be drafted until frictions S5 (what falls
away with immobility) and S6 (who subscribes, and what a refused change looks
like) are settled, because they decide what the step must preserve.
