# Server application (`_server`) — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

No ratified decision. See [open_decisions.md](open_decisions.md).

## Still open

**S1 [D-SA-10] — D-SA-10 "nothing mounts `_server`" is superseded, and this register does not record it.**
SPECIFICATION.md, "Ratified 2026-10-10 (the management application is always
present)", states "D-SA-10 superseded": `AsgiServer` mounts
`ServerApplication()` itself when no application has code `_server`, and the
dedicated process of an application mounted with `spawner="subprocess"` mounts
none. The code does this at `src/kajenn/asgi_server.py:138-139`:
`if role is None and not any(app.code == "_server" for app in kwargs["applications"])`
appends `ServerApplication()`. The tests are
`tests/core/test_server_app_always_present.py` (`TestTheKwargsRoad`,
`TestTheConfigurationRoad`, `TestTheRoleProcess`). The status.md of 2026-09-08
still read "nothing in `AsgiServer` mounts it" (D-SA-10). The docstring of
`tests/server_app/test_config_server_app.py:15` still reads "declared like any
other (D-SA-10)". The supersession has no voice in this register.

**S2 [sections] — design.md names four sections; the code attaches databases too.**
design.md says the application hosts "the users, tokens, tasks and monitor
sections". `ServerApplication.__init__` also attaches `DatabasesSection` under
`databases` (`src/kajenn/server_app/server_app.py:250`), gated `SERVER_ADMIN`
(`src/kajenn/server_app/server_sections/databases_section.py:55`). The owner
decides whether design.md names the databases section and its gate.

**S3 [route names] — kajenn-org/kajenn#8 "Rename the list routes of the server sections".**
The issue names three `list` routes: tasks
(`src/kajenn/server_app/server_sections/tasks_section.py:83`), tokens
(`src/kajenn/server_app/server_sections/tokens_section.py:84`) and users
(`src/kajenn/server_app/server_sections/users_section.py:94`). A fourth one,
`DatabasesSection.list` (`src/kajenn/server_app/server_sections/databases_section.py:80`),
publishes `/_server/databases/list` and is not in the issue. The issue paths
read `src/kajenn_server_app/`, the package before it moved into `kajenn.server_app`.
The owner names the replacement.

**S4 [proposal] — proposal "Channel faces on every routed application, one _meta segment, and the server GUI as a separate component".**
The proposal `xmldocs/proposal/channels-meta-and-server-gui.md` is dated
2026-10-28 with status `review`. It is on the branch
`docs/channels-meta-server-gui-proposal`, not on develop. Its decided points
that touch `_server`:

- `ServerApplication` inherits from `RoutedApplication` and gains an MCP face
  (d-faces);
- the OpenAPI document moves from `_meta/schema_json` to
  `_meta/openapi/schema_json`, and `_meta` is the only reserved segment (d-segment);
- the recipe enables a GUI with `gui(gui_class=...)` on the server element; the
  core never names the GUI package (d-gui-switch);
- `ServerApplication` builds the component as `gui_class(self)` and passes every
  request on `ui/...` to it, at `/_server/ui/` (d-gui-shape);
- extension GUI pages register on the GUI component when it exists; extension
  REST routes keep `attach_section` (d-ext-pages).

The attribute name `gui_class` (d-gui-name) is `proposed`, not decided. None of
these is in design.md or in the code.
