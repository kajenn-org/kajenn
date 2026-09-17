# Tags

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Authorization tags name permissions carried by an avatar. Routes declare the
tags they require: `SUPERADMIN` protects the core users, tokens and tasks
sections; `SERVER_ADMIN` protects the monitor. Public login entry points and
the separately mounted SPA inspector do not inherit those rules.

Per-section tags in configuration are a design direction, with the standing
constraint that the monitor's gate is never weakened.

> [Server application](../../090_server-application/README.md).
