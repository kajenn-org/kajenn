# Monitor — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Application contributions and admin gate

`MonitorSection` supplies the monitor snapshot and the panel descriptors
through the `SERVER_ADMIN`-guarded core surface. It serves no page: the
page that composes the panels is a front-end's
(`src/kajenn/server_app/server_sections/monitor_section.py:27-28`), and the
section root is a 404. It asks each application for
`app_snapshot` and `app_panel`; an optional `panel_source` provides its client
panel code. `BaseApplication` supplies a generic snapshot and panel fallback.
The section asks; it never imports the application it asks. An application
outside the core contributes its projection through the same three methods.

The current application projections are what these contributors return.
Historical monitor/workbench branches, full pre-refactoring panel parity and
Prometheus export are not established by this implementation. The local MkDocs
internals reader is a separate developer documentation tool.

Tests: `tests/server_app/test_server_monitor.py::TestMonitorGate`,
`::TestNoMonitorPage::test_the_section_root_is_gone`, `::TestPanels`, `::TestPanelModule`.

Claim anchors: [`MonitorSection`](../../../../src/kajenn/server_app/server_sections/monitor_section.py#MonitorSection), [`app_snapshot`](../../../../src/kajenn/application.py#BaseApplication.app_snapshot), [`app_panel`](../../../../src/kajenn/application.py#BaseApplication.app_panel), [`BaseApplication`](../../../../src/kajenn/application.py#BaseApplication).

## Contributors: applications and sections

`monitor_contributors` is one map of everything the monitor shows
(`src/kajenn/server_app/server_sections/monitor_section.py:133-140`):

- `monitored_apps`: every application keyed by its mount, except the `_server`
  application itself (`:87-97`);
- `monitored_sections`: every attached section that declares both
  `app_snapshot` and `app_panel`, keyed `_server/<name>`, except the monitor
  section (`:99-113`).

No section shipped in `src/kajenn/server_app/server_sections/` declares
`app_snapshot`, so `monitored_sections` is empty on a bare server.
`snapshot`, `panels` and `panel` all read the same map.

Tests: `tests/server_app/test_server_monitor.py::TestSnapshot::test_server_app_is_not_a_tab`,
`::TestPanels::test_panels_and_snapshot_agree`.

## `server_facts`

`snapshot` answers `{"server": server_facts, "apps": ...}`. `server_facts`
carries `host` and `port` as configured (`config_host`, `config_port`), not the
bound socket, plus `pid`, the sorted application mounts and the sorted section
names (`src/kajenn/server_app/server_sections/monitor_section.py:115-131`).

Tests: `tests/server_app/test_server_monitor.py::TestSnapshot::test_server_facts`.

## Pool metrics not shown

`WorkPool.metrics` returns `{"total", "busy"}` (`src/kajenn/pool.py:65`). No
code in `src/` reads it: the monitor snapshot does not include it. Its readers
are in `tests/core/test_pool.py`.

## Source and test evidence

- [src/kajenn/server_app/server_sections/monitor_section.py](../../../../src/kajenn/server_app/server_sections/monitor_section.py)
- [src/kajenn/application.py](../../../../src/kajenn/application.py)
- [src/kajenn/pool.py](../../../../src/kajenn/pool.py)
- [tests/server_app/test_server_monitor.py](../../../../tests/server_app/test_server_monitor.py)
