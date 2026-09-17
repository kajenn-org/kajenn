# Monitor — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Application contributions and admin gate

`MonitorSection` supplies the monitor snapshot and the panel descriptors
through the `SERVER_ADMIN`-guarded core surface. It serves no page: the
management pages are gramlot's (D-SA-3), and the section root is a 404. It asks each application for
`app_snapshot` and `app_panel`; an optional `panel_source` provides its client
panel code. `BaseApplication` supplies a generic snapshot and panel fallback.
The section asks; it never imports the application it asks. An application
outside the core contributes its projection through the same three methods.

The current application projections are what these contributors return.
Historical monitor/workbench branches, full pre-refactoring panel parity and
Prometheus export are not established by this implementation. The local MkDocs
internals reader is a separate developer documentation tool.

Claim anchors: [`MonitorSection`](../../../../src/kajenn_server_app/server_sections/monitor_section.py#L74), [`app_snapshot`](../../../../src/kajenn/application.py#L170), [`app_panel`](../../../../src/kajenn/application.py#L180), [`BaseApplication`](../../../../src/kajenn/application.py#L86).

## Source and test evidence

- [src/kajenn_server_app/server_sections/monitor_section.py](../../../../src/kajenn_server_app/server_sections/monitor_section.py)
- [src/kajenn/application.py](../../../../src/kajenn/application.py)
- [tests/server_app/test_server_monitor.py](../../../../tests/server_app/test_server_monitor.py)
