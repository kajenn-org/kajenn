# Applications — decisions

**Version**: 0.6 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

**The application layer, with the work finished.** Read this as a report from
the day everything described here is running: it says what an application
*is*, and never what it lacks. What the code holds is [status.md](status.md)'s
subject, and the road between the two is written one step at a time under
`steps/`.

Every voice carries its source. A voice sourced to the owner and a date was
decided in conversation before it reached any register.

The **open frictions** are the closing section, and they are the one place
here that compares this arrival with the present. When that section is empty,
this design stands on its own.

---

No ratified decision other than the owner statement below. See
[open_decisions.md](open_decisions.md).

## Owner statement of 2026-11-03

**Source: Owner, 2026-11-03.** Translated from Italian, verbatim: routes depend
on auth tags and capabilities and are always computed dynamically. Plugins can
be configured dynamically, and applications can even be mounted and unmounted.

This voice is recorded as given. What it implies for the text of
[design.md](design.md) is not decided: the lines it contradicts, and the
questions it leaves open, are the frictions below.

## Still open

**S17 [code-past-design] — five ways a request does not reach its handler, not
four.** [design.md](design.md) §7 and [README.md](README.md) list four answers
settled before the handler body runs: 404, 401, 403, 400. The code settles a
fifth during resolution. An HTTP request on a method other than the one the
entry declares with `openapi_method` answers 405 with an `Allow` header, and the
handler does not run (`routed_application.py:266-268`, `method_filters`
`:332-344`). Test:
`tests/core/test_route_method.py::TestDeclaredMethod::test_get_on_a_post_route_is_405_with_allow_and_never_runs`.
The same distance is recorded in [025 routing system](../025_routing-system/decisions.md)
as a fourth filter on the walk.

**S18 [code-past-design] — a 401 is a bare challenge, and the design sends a
browser to a login page.** [design.md](design.md) §7 says that a browser that
gets the 401 "lands on a login page". The error middleware answers a 401 with
the bare status and the `WWW-Authenticate` challenge, and never points a caller
at a login page (`middleware/errors.py:32-36`). The comment above
`ROUTER_ERRORS` (`routed_application.py:134-141`) repeats the design's wording.

**S19 [owner-2026-11-03] — §1 keeps a restart for an application that cannot
move.** [design.md](design.md) §1 says an application that cannot be taken
away and put back "does not claim it can, and its change waits for a restart".
The owner statement of 2026-11-03 says applications can even be mounted and
unmounted. The design does not state why such an application cannot
be made movable, which the overview's first rule (D32) requires where a restart
is accepted.

**S20 [owner-2026-11-03] — tags as the only filter of an ordinary
application.** [design.md](design.md):248-249 says tags are "the only one an
ordinary application uses". The owner statement says routes depend on auth tags
and capabilities and are always computed dynamically. Open question: what
step 3 says about capabilities for an ordinary application.
