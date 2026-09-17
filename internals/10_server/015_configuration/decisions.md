# Configuration — decisions

**Version**: 0.5 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

**Configuration, with the work finished.** Read this as a report from the day
everything described here is running: it says what a configuration *is*, and
never what it lacks. What the code holds is [status.md](status.md)'s subject.

Every voice carries its source. A voice sourced to the owner and a date was
decided in conversation before it reached any register.

The **open frictions** are the closing section — the one place here that
compares this arrival with the present. When it is empty, the design stands on
its own.

---

## Still open

**S1 [live-config] — the whole of §6 is unbuilt.** The code has a read door and no more:
`apply_configuration` has zero occurrences in `src/` and `tests/`, the handler
declares no mutator, and nothing subscribes to anything (searches in
[status.md](status.md)). The machinery it would stand on exists and is unused —
the tree is already a `Bag` subclass and `Bag.subscribe` already exists.

This is the largest distance between arrival and present here, and it is the
same subject as S5/S6 of [010 server](../010_server/decisions.md) seen from the
other side: there the question is what falls away when immobility goes, here it
is what has to be built.
