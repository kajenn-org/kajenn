# Server application (`_server`)

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/090_server-application.md` — DIVERGENT, 0 CONVERGE / 0 DIVERGE / 2 SILENT.

`AsgiServer` automatically mounts `ServerApplication` at `/_server`.
The application hosts login methods and the users, tokens, tasks and monitor
sections. Login entry points are public. Users, tokens and tasks require
`SUPERADMIN`; the monitor requires `SERVER_ADMIN`. A denied caller receives
401 when anonymous and 403 when identified.

An application outside the core attaches its own diagnostic section through
`attach_section`, and the mounting is the gate. That gate does not confer the
monitor's `SERVER_ADMIN` rule: an attached section has no route authorization
rule of its own.

> [Inspector](inspector/README.md) · [Authentication](../050_authentication/README.md).
