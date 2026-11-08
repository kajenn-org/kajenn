# Server — decisions

**Version**: 0.6 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

**The ground floor, with the work finished.** Read this document as a report
from the day everything described here is running: it says what the server
*is*, in the present tense, and never what it lacks. What the code holds at
any given moment is [status.md](status.md)'s subject, and the road between the
two is written one step at a time under `steps/`.

Every voice carries its source. A voice sourced to the owner and a date was
decided in conversation before it reached any register.

The **open frictions** are the closing section, and they are the one place
here that compares this arrival with the present — because settling them is
what lets this document be ratified. When that section is empty, the design
stands on its own.

---

No ratified decision. See [open_decisions.md](open_decisions.md).

## Still open

The ids continue after S12, the highest id the August audit trail in
[tech_notes.md](tech_notes.md) cites.

**S13 [installed-set] — `register_application` declares the installed set fixed at construction.**
The `register_application` docstring states "the set of applications is fixed
at construction" ([server.py](../../../src/kajenn/server.py#L244)). The module
docstring repeats it ([server.py](../../../src/kajenn/server.py#L20)).
[design.md](design.md) §2 and §8 describe an installed set that is not sealed
and changes while the server runs. The owner statement of 2026-11-03 (translated, verbatim) says
applications can even be mounted and unmounted. D32 (SPECIFICATION.md:954)
requires the reason to be written where a static limit is accepted. The
docstring gives no reason. No method of `BaseServer` removes an application.
genro-routes 0.30.1 has `BaseRouter.remove_branch` and
`BaseRouter.detach_instance`. Neither is called in `src/` or `tests/`.
The owner settled the target on 2026-11-08 in
`xmldocs/proposal/live-applications.md`:
applications are mounted disabled, enabled on `ready`, switched off and removed
while the server runs, and never moved. The code still has the limit.

**S14 [lifespan] — a failing startup can stop the boot.**
[design.md](design.md) §5 states that a hook that fails is recorded and the
sequence continues. `Lifespan.startup` stops at a `FatalBootError` raised by an
`on_startup` hook, and answers `lifespan.startup.failed`
([lifespan.py](../../../src/kajenn/lifespan.py#L122)). A database handler whose
`open` raises becomes a `FatalBootError` too, before any hook runs.

**S15 [demux] — well-known and hidden paths are decided before the mounts.**
[design.md](design.md) §3 states one rule that reads only the first path
segment, in five branches. `demux` first sends a path matching
`WELL_KNOWN_PATTERN` to the application that declared it, or answers 404
"unimplemented well-known URL". It then answers 404 to any other first segment
starting with `.` ([server.py](../../../src/kajenn/server.py#L535)). Both
branches come before the mount branch. They arrived with commit `3986e8c`
(2026-11-02). design.md does not describe them.
