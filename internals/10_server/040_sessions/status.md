# Sessions — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.
Authentication passages updated against `dfc3941`.

## Store, identity and write-back

The shipped store is `MemorySessionStore`, implementing `SessionStore`; there
is no exported `MemoryStore` class. `Session` holds a token, metadata, a data Bag
and keyed avatars. `avatar()` returns the root identity or `None`;
`attach_avatar` writes a slot and marks the session dirty without changing its id.
Handlers mutating the data Bag call `mark_dirty` explicitly. Touching the access
clock is not a dirty-making operation.

`Session.avatars` is a read-only `MappingProxyType` view; `attach_avatar` is its
only writer (session.py:101-104). No method detaches an avatar: a claimed slot
stays claimed for the session's lifetime (session.py:34). A non-positive TTL
makes `is_expired` true (session.py:135-141; test `test_zero_ttl_is_expired`).

`get` drops an expired session and refreshes the clock for a live one. A mass
purge is checked on create after `PURGE_INTERVAL` (300 seconds), without a
background purge task. `SessionMiddleware` only saves dirty sessions and only
issues a cookie on creation; its cookie lifetime is server TTL multiplied by 24.

`/_auth/logout` deletes the supplied session id and answers
`{"status": "ok"}` even when the id is missing or unknown. It declares no
`auth_rule`. Session deletion also occurs when `AuthMixin.authenticate`
receives a refusal from `/_auth/avatar`: the session's stored identity and
group no longer authenticate.

Claim anchors: [`logout`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase.logout), [`authenticate`](../../../src/kajenn/auth/mixin.py#AuthMixin.authenticate).

Claim anchors: [`MemorySessionStore`](../../../src/kajenn/session/store.py#MemorySessionStore), [`SessionStore`](../../../src/kajenn/session/store.py#SessionStore), [`Session`](../../../src/kajenn/session/session.py#Session), [`avatar`](../../../src/kajenn/session/session.py#Session.avatar), [`attach_avatar`](../../../src/kajenn/session/session.py#Session.attach_avatar), [`mark_dirty`](../../../src/kajenn/session/session.py#Session.mark_dirty), [`SessionMiddleware`](../../../src/kajenn/middleware/session.py#SessionMiddleware), [`avatars`](../../../src/kajenn/session/session.py#Session.avatars), [`is_expired`](../../../src/kajenn/session/session.py#Session.is_expired), [`logout`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase.logout).

## Two persistence formats

`dump`/`restore` preserve metadata and keyed avatar identities/groups, excluding
tags and the session data Bag. Restored avatars have no tags; authentication
asks `_auth` for the root identity and group through the credential cache.
`save_snapshot`/`load_snapshot` instead pickle complete live sessions, including
the Bag. `SessionMixin(save_session=...)` loads before
lifespan and saves on exit; named CLI instances wire that snapshot path.
`SessionStore` declares both snapshot methods with default bodies that do nothing
and return `0`. A custom store that subclasses `SessionStore` inherits them; one
that does not must implement them.

The recipe element `session(ttl, store_class, save_path, **params)` lives under
`server` (config/elements.py:228-252). `ttl` becomes `session_ttl`.
`store_class` is instantiated with the remaining `**params`; omitted, the
composition's `MemorySessionStore` applies. `save_path` arms the snapshot.

The authenticating connection link described in the decisions is absent from
`Session`. An identity an application keeps on a connection of its own does
not automatically become a core session avatar.

Claim anchors: [`dump`](../../../src/kajenn/session/store.py#SessionStore.dump), [`dump`](../../../src/kajenn/session/store.py#MemorySessionStore.dump), [`restore`](../../../src/kajenn/session/store.py#SessionStore.restore), [`restore`](../../../src/kajenn/session/store.py#MemorySessionStore.restore), [`save_snapshot`](../../../src/kajenn/session/store.py#MemorySessionStore.save_snapshot), [`load_snapshot`](../../../src/kajenn/session/store.py#MemorySessionStore.load_snapshot), [`SessionMixin`](../../../src/kajenn/session/mixin.py#SessionMixin), [`Session`](../../../src/kajenn/session/session.py#Session), [`save_session`](../../../src/kajenn/session/mixin.py#SessionMixin.save_session), [`SessionStore`](../../../src/kajenn/session/store.py#SessionStore), [`session` element](../../../src/kajenn/config/elements.py#AsgiServerGrammar.session).

## Source and test evidence

- [src/kajenn/session/session.py](../../../src/kajenn/session/session.py)
- [src/kajenn/session/store.py](../../../src/kajenn/session/store.py)
- [src/kajenn/session/mixin.py](../../../src/kajenn/session/mixin.py)
- [src/kajenn/middleware/session.py](../../../src/kajenn/middleware/session.py)
- [tests/core/test_session.py](../../../tests/core/test_session.py)
- [tests/core/test_middleware_std.py](../../../tests/core/test_middleware_std.py)
- [tests/core/test_cli.py](../../../tests/core/test_cli.py)
