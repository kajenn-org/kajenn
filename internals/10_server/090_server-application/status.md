# Server application (`_server`) — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## A declared system application

`ServerApplication` is declared like any other application, with the code
`_server` (D-SA-10): nothing in `AsgiServer` mounts it, and a server that does
not declare it exposes no `/_server/…`. `ServerApplication` extends `OpenApiApplication` and attaches auth, users,
tokens, tasks and monitor sections. Its public descriptor lists attached names.
The password method and the OIDC providers of its own `oidc=` kwarg are
registered under the auth section.

The monitor declares `SERVER_ADMIN`; users, tokens and tasks declare
`SUPERADMIN`. The login surface is public. `_request` injection comes from the common
`RoutedApplication.bind_kwargs`, not a private server-app override.

Claim anchors: [`AsgiServer`](../../../src/kajenn/asgi_server.py#L90), [`ServerApplication`](../../../src/kajenn_server_app/server_app.py#L111), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L111), [`bind_kwargs`](../../../src/kajenn/routed_application.py#L272).

Behavior evidence: [`MonitorSection`](../../../src/kajenn_server_app/server_sections/monitor_section.py#L74), [`UsersSection`](../../../src/kajenn_server_app/server_sections/users_section.py#L61), [`TokensSection`](../../../src/kajenn_server_app/server_sections/tokens_section.py#L57), [`TasksSection`](../../../src/kajenn_server_app/server_sections/tasks_section.py#L60).

## SPA inspector and unfinished administration

The inspector is not imported by the core server application. A SPA front
attaches its own inspector at startup when `GNR_ASGI_INSPECTOR` is present.
That diagnostic surface has no route auth rule; its mounting gate differs from
the monitor's `SERVER_ADMIN` protection.

Per-section configurable tags, a plugin configuration page, general dynamic
application installation and monitor/workbench proposals are not delivered by
the current section list.

Behavior evidence: [`on_startup`](../../../src/kajenn_orchestra/spa_app.py#L631), [`InspectorSection`](../../../src/kajenn_orchestra/inspector_section.py#L61).

## Source and test evidence

- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn_server_app/server_app.py](../../../src/kajenn_server_app/server_app.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn_orchestra/spa_app.py](../../../src/kajenn_orchestra/spa_app.py)
- [src/kajenn_orchestra/inspector_section.py](../../../src/kajenn_orchestra/inspector_section.py)
- [tests/server_app/test_server_application.py](../../../tests/server_app/test_server_application.py)
- [tests/server_app/test_server_monitor.py](../../../tests/server_app/test_server_monitor.py)
- [tests/spa/test_inspector_section.py](../../../tests/spa/test_inspector_section.py)
