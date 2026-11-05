# Configuration — decisions

**Version**: 0.5 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

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

**S1 [live-config] — the whole of §5 is unbuilt.** The code has a read door and no more:
`apply_configuration` has zero occurrences in `src/` and `tests/`, the handler
declares no mutator, and nothing subscribes to anything (searches in
[status.md](status.md)). The machinery it would stand on exists and is unused —
the tree is already a `Bag` subclass and `Bag.subscribe` already exists.

This is the largest distance between arrival and present here, and it is the
same subject as S5/S6 of [010 server](../010_server/decisions.md) seen from the
other side: there the question is what falls away when immobility goes, here it
is what has to be built.

The ids below continue after S7, the highest id the August audit trail in
[tech_notes.md](tech_notes.md) cites.

**S8 [grammar] — the server declares ten sections, not eight.**
[design.md](design.md) §4 and §6 state "eight sections and no more".
`AsgiServerGrammar.configuration` declares ten:
`site`, `server`, `middleware`, `authentication`, `channels`, `storage`,
`applications`, `databases`, `plugins` and `openapi`
([elements.py](../../../src/kajenn/config/elements.py#L93)). `site` and
`channels` are the two that design.md does not list.

**S9 [live-config] — kajenn-org/kajenn#75 is absent from this entry.**
Open issue kajenn-org/kajenn#75 "Live configuration: take it into account for
future development" asks to examine the live configuration for future
development. It records that `StorageManager.configure` resolves every
`BagResolver` once, so a mount does not follow a change of its configuration.
Neither this register nor [design.md](design.md) cites it. S1 covers the same
subject.
