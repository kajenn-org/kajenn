# Tags

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/050_authentication__tags.md` — DIVERGENT, 0 CONVERGE / 0 DIVERGE / 0 SILENT.

Authorization tags name permissions carried by an avatar. Routes declare the
tags they require: `AUTH_ADMIN` protects users, groups, tags and tokens on
`_auth`; `SERVER_ADMIN` protects tasks and the monitor on `_server`. Public
login entry points on `_auth` and
any section attached from outside the core do not inherit those rules.

Per-section tags in configuration are a design direction, with the standing
constraint that the monitor's gate is never weakened.

> [Server application](../../090_server-application/README.md).
