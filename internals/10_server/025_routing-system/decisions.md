# Routing system — decisions

**Version**: 0.7 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

**The routing system, with the work finished.** Read this as a report from the
day everything described here is running: it says what a routing class, a walk
and a plugin *are*, and never what they lack. What the code holds is
[status.md](status.md)'s subject.

Every voice carries its source. A voice sourced to the owner and a date was
decided in conversation before it reached any register.

The **open frictions** are the closing section, and they are the one place here
that compares this arrival with the present. Each carries a **family tag** in
brackets, because these frictions are settled in one grouped pass across the
server skeleton rather than one entry at a time (owner, 2026-08-23).

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

**S8 [code-past-design] — the declared HTTP method is a filter, and the design
names it publishing only.** [design.md](design.md) §1 says `openapi_method` is
read "by the reader that publishes it". The code also passes the request's
method as the `openapi_method` filter on HTTP requests (`method_filters`,
`routed_application.py:332-344`). The `openapi` plugin's `deny_reason` refuses
an entry declaring another method (`plugins/openapi/plugin.py:99-112`), and
`execute` answers 405 with `Allow` (`routed_application.py:266-268`). Test:
`tests/core/test_route_method.py::TestDeclaredMethod::test_get_on_a_post_route_is_405_with_allow_and_never_runs`.
The design states three filters; the code walks HTTP requests with a fourth.
The same distance is recorded in [020 applications](../020_applications/decisions.md)
as the fifth way a request does not reach its handler.

**S9 [tense] — §5 is written in the tense of the implementation.**
[design.md](design.md) §5 says "Today there is one, `openapi`" about the dialect
plugins this package ships. The overview forbids "today" in `design.md`.

**S10 [owner-2026-11-03] — capabilities: a fact of the installation or of the
request.** [design.md](design.md):112 and :121 say capabilities are "what this
installation can do" and "what the installation has rather than who is asking".
The owner statement says routes depend on capabilities and are always computed
dynamically. Open question: are capabilities carried by each request, or are
they the installation's and only re-read at every walk?

**S11 [owner-2026-11-03] — plugins configured dynamically: what changes, and
where.** [design.md](design.md):63 and :249 say a tree is armed once, at the
first look after installation. The owner statement says plugins can be
configured dynamically. Open questions:
- Does a change reach a tree already armed, and from which request on?
- Is a change made for a whole tree, or also for a single entry? genro-routes
  `Router.set_plugin_enabled(method_name, plugin_name, enabled)` works per
  entry or on `"_all_"`.
- Can a change switch off one of the fixed pair (`pydantic`, `openapi`) of §6?
  The recipe refuses it (`plugin_mixin.py:110`); genro-routes accepts
  `set_plugin_enabled("_all_", "pydantic", False)`.

**S12 [owner-2026-11-03] — unmounting: how a branch leaves a tree.**
[design.md](design.md):57 says a tree is "grown by attaching others"; no line
describes removal. The owner statement says applications can be mounted and
unmounted. genro-routes has `remove_branch(name)` and `detach_instance`; no code
in `src/kajenn` calls them. Open question: what the design says about removing
a branch, and what becomes of a request in flight on it.

**S13 [code-past-design] — the channel is supplied on every face.**
[design.md](design.md):129 says "HTTP dispatch supplies none". `auth_filters`
passes `channel_channel` for every face (`routed_application.py`, see
[status.md](status.md)). The owner statement does not mention the channel.
