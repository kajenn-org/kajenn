# Server application (`_server`)

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

`AsgiServer` automatically mounts `ServerApplication` at `/_server`.
The application hosts login methods and the users, tokens, tasks and monitor
sections. Login entry points are public. Users, tokens and tasks require
`SUPERADMIN`; the monitor requires `SERVER_ADMIN`. A denied caller receives
401 when anonymous and 403 when identified.

The optional inspector belongs to `kajenn_orchestra`.
`SpaApplication` attaches it to `/_server/inspector` during startup when
`GNR_ASGI_INSPECTOR` is enabled. That mounting gate does not confer the
monitor's `SERVER_ADMIN` rule: the inspector has no route authorization rule.

> [Inspector](inspector/README.md) · [Authentication](../050_authentication/README.md).
