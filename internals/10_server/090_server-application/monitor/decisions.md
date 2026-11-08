# Monitor — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

No ratified decision. See [open_decisions.md](open_decisions.md).

## Still open

**S1 [server GUI] — proposal "Channel faces on every routed application, one _meta segment, and the server GUI as a separate component".**
The proposal `xmldocs/proposal/channels-meta-and-server-gui.md` is dated
2026-11-08 with status `accepted`, on develop. It places the
browser interface of `_server` in a component of the repository
`kajenn-serverapp-gui`, enabled by `gui(gui_class=...)` on the server element
and served at `/_server/ui/` (d-gui-switch, d-gui-shape). The component reads
only through the REST routes of `_server`. The proposal does not name the monitor
page. Its Consequences say kajenn-orchestra registers its pool pages on the GUI
component. The owner decides whether the GUI component is the page that reads
`snapshot`, `panels` and `panel`.

**S2 [page] — design.md says the section renders the page; the section serves only its data.**
design.md says the page is "rendered by the `_server/monitor` section".
`MonitorSection` serves three JSON or JavaScript routes, `snapshot`, `panels`
and `panel`, and no page. Its docstring says the page "is a front-end's, not
this section's" (`src/kajenn/server_app/server_sections/monitor_section.py:27-28`).
`/_server/monitor/` answers 404
(`tests/server_app/test_server_monitor.py::TestNoMonitorPage::test_the_section_root_is_gone`).
The docstring of that test class still reads "D-SA-3: the monitor page is
gramlot's". The owner decides the wording of design.md.
