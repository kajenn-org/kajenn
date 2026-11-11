# Avatar — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Identity value

`Avatar(identity, tags=None)` is a slotted value object with `identity`, a
normalized list of `tags`, and an extensible `data` Bag. An anonymous request is
represented by `None`, not an anonymous Avatar. Authentication methods return
this common type and `Session` stores it in keyed slots, with `root` the default.

`Request.avatar()` reads the HTTP scope's root auth verdict; other avatar keys
are resolved through the session. Avatar data is distinct from session data.
The type itself steers no placement and creates no proposed
connection-to-session link.

`authenticate_credential` copies the `data` of the authentication route's
answer into `avatar.data` (auth/mixin.py:149-151). `AuthSection.authenticate`
answers `data: {}` (auth_section.py:133-139).

## Avatar on the bus

`avatar_auth` turns an avatar into the bus record `{identity, tags}`. When the
avatar has data, the record adds `data` as TYTX json (remote_application.py:54-59).
`avatar_from_auth` rebuilds the avatar from that record; a record without
`data` leaves the data Bag empty (remote_application.py:62-69). Tests:
`test_kbus_call_preserves_avatar_data`, `test_kbus_call_keeps_typed_values`
(tests/core/test_avatar_data_on_bus.py).

Claim anchors: [`Avatar`](../../../../src/kajenn/session/avatar.py#L31), [`identity`](../../../../src/kajenn/session/avatar.py#L43), [`tags`](../../../../src/kajenn/session/avatar.py#L48), [`Session`](../../../../src/kajenn/session/session.py#L60), [`Request`](../../../../src/kajenn/request.py#L120), [`avatar`](../../../../src/kajenn/session/session.py#L93), [`avatar`](../../../../src/kajenn/request.py#L407), [`avatar_auth`](../../../../src/kajenn/remote_application.py#L54), [`avatar_from_auth`](../../../../src/kajenn/remote_application.py#L62).

## Source and test evidence

- [src/kajenn/session/avatar.py](../../../../src/kajenn/session/avatar.py)
- [src/kajenn/session/session.py](../../../../src/kajenn/session/session.py)
- [src/kajenn/request.py](../../../../src/kajenn/request.py)
- [src/kajenn/remote_application.py](../../../../src/kajenn/remote_application.py)
- [tests/core/test_auth.py](../../../../tests/core/test_auth.py)
- [tests/core/test_session.py](../../../../tests/core/test_session.py)
- [tests/core/test_request.py](../../../../tests/core/test_request.py)
- [tests/core/test_avatar_data_on_bus.py](../../../../tests/core/test_avatar_data_on_bus.py)
