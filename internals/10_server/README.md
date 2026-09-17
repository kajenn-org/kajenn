# 10_server — the machine

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

The machine every installation runs on, from `BaseServer` upward. It is the
server object itself, the applications it hosts and how a request finds one, the
description an installation is assembled from, and the capability mixins stacked
on the base — authentication, sessions, the middleware chain, the second
transport, storage, background work. The numbered folders are the reading order: no entry needs a concept that
comes later.

| Entry | In one line |
|---|---|
| [010 server](010_server/README.md) | the ground: the server object, the applications it hosts, how a request finds one, ordered start and stop |
| [015 configuration](015_configuration/README.md) | the tree every entry reads its own words from: layers, read stack, subscribers |
| [020 applications](020_applications/README.md) | `RoutedApplication` and the routing tree · [openapi](020_applications/openapi/README.md) · [mcp](020_applications/mcp/README.md) |
| [025 routing system](025_routing-system/README.md) | what a routing class is: the tree, the filtered walk, and the plugins armed on it |
| [030 middleware](030_middleware/README.md) | the uniform middleware chain every request passes |
| [040 sessions](040_sessions/README.md) | per-user server-side state between requests |
| [050 authentication](050_authentication/README.md) | 401 vs 403 · [avatar](050_authentication/avatar/README.md) · [tags](050_authentication/tags/README.md) |
| [055 websocket](055_websocket/README.md) | the second transport: the server holds the connection, every message is a request |
| [060 storage](060_storage/README.md) | the only access to the filesystem, through storage nodes |
| [065 db](065_db/README.md) | databases mounted through the recipe, no backend in the core |
| [070 tasks](070_tasks/README.md) | work that is no HTTP request |
| [080 task-thermometers](080_task-thermometers/README.md) | see a batch move, stop it politely |
| [090 server-application](090_server-application/README.md) | the `_server` app and its sections · [monitor](090_server-application/monitor/README.md) · [inspector](090_server-application/inspector/README.md) |
| [110 cli](110_cli/README.md) | drive installations from the shell |
| [120 restart](120_restart/README.md) | born here; enriched by spa → subcommanders → kube |
