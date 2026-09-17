# Inspector

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

The diagnostic surface an application attaches under `_server/<name>`: its own
read-only view of what it holds while it runs. `ServerApplication` supplies
the attach point and the routing; the application supplies the section, the
gate it installs behind, and the routes. A section attached this way carries no
authentication rule of its own.
