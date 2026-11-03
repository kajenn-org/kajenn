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

No ratified decision other than the three owner voices below. See
[open_decisions.md](open_decisions.md).

## Routes computed per request on tags, capabilities and channel

**Source: Owner, 2026-11-03.** The routes a request sees are computed for that
request. The walk filters on three facts of the request: the caller's tags
(`auth`), the capabilities of the environment the request runs in (`env`), and
the channel it arrived through (`channel`). Capabilities are a fact of the
request's environment, not of the installation. Every face supplies its
channel. Accounted for in [design.md](design.md) §2.

## Router plugins configured while the server runs

**Source: Owner, 2026-11-03.** Router plugins are configured while the server
runs: a plugin can be plugged, enabled, disabled or given new options without a
restart. Arming at the first look after installation stays; it is the first
arming, not the only one. Accounted for in [design.md](design.md) §7.

## Branch removal while the server runs

**Source: Owner, 2026-11-03.** An application is mounted and unmounted while the
server runs; a branch is removed from a tree as it is attached. Accounted for in
[design.md](design.md) §1.

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

**S10 [readme] — README.md describes the walk and the arming as before
2026-11-03.** [README.md](README.md) says the walk filters on "the
installation's capabilities", and lists "when a tree is armed — once, on the
first look after installation". [design.md](design.md) §2 and §7 now say the
capabilities of the environment the request runs in, and plugins configured
while the server runs. README.md was not edited in the 2026-11-03 pass.
