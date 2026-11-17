# Server application (`_server`)

**Version**: 0.3 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/090_server-application.md` — DIVERGENT, 0 CONVERGE / 0 DIVERGE / 2 SILENT.

`AsgiServer` automatically mounts `ServerApplication` at `/_server`.
The application hosts tasks, monitor, databases, k8s, applications and metrics
sections. Tasks and the monitor require `SERVER_ADMIN`. Login and identity
management belong to the separately mounted `_auth` application; its users,
groups, tags and tokens require `AUTH_ADMIN`.

An application outside the core attaches its own diagnostic section through
`attach_section`, and the mounting is the gate. That gate does not confer the
monitor's `SERVER_ADMIN` rule: an attached section has no route authorization
rule of its own.

> [Inspector](inspector/README.md) · [Authentication](../050_authentication/README.md).
