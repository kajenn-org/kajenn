# Inspector — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## SPA-owned diagnostic surface

`InspectorSection` lives in `kajenn_orchestra`, with its owning
`SpaApplication` as parent. The front attaches it under `/_server/inspector`
on startup only when `GNR_ASGI_INSPECTOR` is present. A second inspector attach
raises `FatalBootError`. The generic core imports no SPA machinery for it.

Its `page`, `census` and `stream` routes carry no `auth_rule`: mounting is the
diagnostic gate. Therefore it must not be described as protected by the
monitor's `SERVER_ADMIN` rule. The census reads the owning front's commander;
the SSE stream starts with a census and subscribes to observation events,
unsubscribing when the reader leaves. It does not call the hosted site or mint
a site connection merely to inspect the pool.

The September decision moving ownership supersedes the older inspector
contribution-contract plan. Further per-application panels remain an open
direction, not part of this read surface.

Claim anchors: [`InspectorSection`](../../../../src/kajenn_orchestra/inspector_section.py#L61), [`SpaApplication`](../../../../src/kajenn_orchestra/spa_app.py#L517), [`page`](../../../../src/kajenn_orchestra/inspector_section.py#L73), [`census`](../../../../src/kajenn_orchestra/inspector_section.py#L82), [`stream`](../../../../src/kajenn_orchestra/inspector_section.py#L91).

## Source and test evidence

- [src/kajenn_orchestra/inspector_section.py](../../../../src/kajenn_orchestra/inspector_section.py)
- [src/kajenn_orchestra/spa_app.py](../../../../src/kajenn_orchestra/spa_app.py)
- [tests/spa/test_inspector_section.py](../../../../tests/spa/test_inspector_section.py)
- [tests/spa/orchestration/test_orchestration_observation.py](../../../../tests/spa/orchestration/test_orchestration_observation.py)
