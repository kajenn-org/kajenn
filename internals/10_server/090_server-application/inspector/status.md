# Inspector — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## The attach point for a diagnostic section

`ServerApplication.attach_section(section, name)` links a `RoutingClass` into
the `_server` app under `name` and records it in `sections`, so its endpoints
answer at `/_server/<name>/...` and the `index` descriptor lists it.
`tests/server_app/test_server_application.py` attaches a section of its own and
asserts both the registration and the routing.

A section that arrives this way carries no `auth_rule`, so the monitor's
`SERVER_ADMIN` rule does not extend to it and it must not be described as
protected by it. Whoever attaches the section decides who may read it, by
deciding whether to attach it at all.

The core keeps no list of attachable sections and imports none. Further
per-application panels remain an open direction, not part of this read
surface.

Claim anchors: [`ServerApplication`](../../../../src/kajenn_server_app/server_app.py#L159), [`attach_section`](../../../../src/kajenn_server_app/server_app.py#L269), [`sections`](../../../../src/kajenn_server_app/server_app.py#L260).

## Source and test evidence

- [src/kajenn_server_app/server_app.py](../../../../src/kajenn_server_app/server_app.py)
- [tests/server_app/test_server_application.py](../../../../tests/server_app/test_server_application.py)
