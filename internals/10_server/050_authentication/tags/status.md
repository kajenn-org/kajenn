# Tags — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Authorization vocabulary

`RoutedApplication.auth_filters` joins `Avatar.tags` into the auth plugin's
`auth_tags` filter. Route declarations use `auth_rule`; the plugin evaluates
the rule and the application maps anonymous denial to 401 and known-user denial
to 403. No universal hardcoded list of user roles is created by the core.

The monitor uses `SERVER_ADMIN`; users, tokens and task administration use
`SUPERADMIN`.
The inspector is a separate SPA-owned diagnostic surface enabled by
`GNR_ASGI_INSPECTOR`; its routes do not declare `auth_rule`. It must not be
listed as another automatically protected core section. Per-section tag
configuration remains backlog, not a shipped recipe option.

Claim anchors: [`RoutedApplication`](../../../../src/kajenn/routed_application.py#L111), [`auth_filters`](../../../../src/kajenn/routed_application.py#L220), [`Avatar`](../../../../src/kajenn/session/avatar.py#L31), [`tags`](../../../../src/kajenn/session/avatar.py#L48).

Behavior evidence: [`MonitorSection`](../../../../src/kajenn_server_app/server_sections/monitor_section.py#L74), [`UsersSection`](../../../../src/kajenn_server_app/server_sections/users_section.py#L61), [`TokensSection`](../../../../src/kajenn_server_app/server_sections/tokens_section.py#L57), [`TasksSection`](../../../../src/kajenn_server_app/server_sections/tasks_section.py#L60).

## Source and test evidence

- [src/kajenn/routed_application.py](../../../../src/kajenn/routed_application.py)
- [src/kajenn/session/avatar.py](../../../../src/kajenn/session/avatar.py)
- [src/kajenn_server_app/server_sections/monitor_section.py](../../../../src/kajenn_server_app/server_sections/monitor_section.py)
- [src/kajenn_orchestra/inspector_section.py](../../../../src/kajenn_orchestra/inspector_section.py)
- [tests/core/test_routed_application.py](../../../../tests/core/test_routed_application.py)
- [tests/server_app/test_server_monitor.py](../../../../tests/server_app/test_server_monitor.py)
- [tests/spa/test_inspector_section.py](../../../../tests/spa/test_inspector_section.py)
