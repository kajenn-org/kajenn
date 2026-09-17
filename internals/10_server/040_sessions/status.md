# Sessions — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Store, identity and write-back

The shipped store is `MemorySessionStore`, implementing `SessionStore`; there
is no exported `MemoryStore` class. `Session` holds a token, metadata, a data Bag
and keyed avatars. `avatar()` returns the root identity or `None`;
`attach_avatar` writes a slot and marks the session dirty without changing its id.
Handlers mutating the data Bag call `mark_dirty` explicitly. Touching the access
clock is not a dirty-making operation.

`get` drops an expired session and refreshes the clock for a live one. A mass
purge is checked on create after `PURGE_INTERVAL` (300 seconds), without a
background purge task. `SessionMiddleware` only saves dirty sessions and only
issues a cookie on creation; its cookie lifetime is server TTL multiplied by 24.

Claim anchors: [`MemorySessionStore`](../../../src/kajenn/session/store.py#L88), [`SessionStore`](../../../src/kajenn/session/store.py#L53), [`Session`](../../../src/kajenn/session/session.py#L60), [`avatar`](../../../src/kajenn/session/session.py#L93), [`attach_avatar`](../../../src/kajenn/session/session.py#L111), [`mark_dirty`](../../../src/kajenn/session/session.py#L123), [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#L55).

## Two persistence formats

`dump`/`restore` preserve metadata and keyed avatar identities/tags, excluding
the session data Bag. `save_snapshot`/`load_snapshot` instead pickle complete
live sessions, including the Bag. `SessionMixin(save_session=...)` loads before
lifespan and saves on exit; named CLI instances wire that snapshot path.
A custom `SessionStore` used with snapshots must supply the snapshot methods too.

The authenticating connection link described in the decisions is absent from
`Session`. An identity an application keeps on a connection of its own does
not automatically become a core session avatar.

Claim anchors: [`dump`](../../../src/kajenn/session/store.py#L79), [`dump`](../../../src/kajenn/session/store.py#L139), [`restore`](../../../src/kajenn/session/store.py#L83), [`restore`](../../../src/kajenn/session/store.py#L179), [`save_snapshot`](../../../src/kajenn/session/store.py#L152), [`load_snapshot`](../../../src/kajenn/session/store.py#L167), [`SessionMixin`](../../../src/kajenn/session/mixin.py#L55), [`Session`](../../../src/kajenn/session/session.py#L60), [`save_session`](../../../src/kajenn/session/mixin.py#L83), [`SessionStore`](../../../src/kajenn/session/store.py#L53).

## Source and test evidence

- [src/kajenn/session/session.py](../../../src/kajenn/session/session.py)
- [src/kajenn/session/store.py](../../../src/kajenn/session/store.py)
- [src/kajenn/session/mixin.py](../../../src/kajenn/session/mixin.py)
- [src/kajenn/middleware/session.py](../../../src/kajenn/middleware/session.py)
- [tests/core/test_session.py](../../../tests/core/test_session.py)
- [tests/core/test_middleware_std.py](../../../tests/core/test_middleware_std.py)
- [tests/core/test_cli.py](../../../tests/core/test_cli.py)
