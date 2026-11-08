# Sessions — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

No ratified decision. See [open_decisions.md](open_decisions.md).

---

## Still open

**S1 — the session record carries the authenticating connection id: an owner
voice absent from this register.** The owner stated on 2026-08-24 (interview
on 020_applications S2) that the session record carries the connection id of
the SPA connection that authenticated it. The id is a scalar, written once by
the first application that performs authentication, and cleared when the
session's identity decays. The reverse direction (connection → session) lives
on the `connection_register_item`. The voice is quoted only in
[open_decisions.md](open_decisions.md), row 1. This register has not carried
it since the migration commit `f8a97c9`. `Session.__slots__` holds `_id`,
`_meta`, `_data`, `_avatars`, `_dirty` (session.py:63); no connection id. The
voice is not ratified here.
