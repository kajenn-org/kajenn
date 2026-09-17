# Storage

**Version**: 0.1 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

Filesystem access uses logical volumes and synchronous storage nodes.

Filesystem access goes ONLY through storage nodes: logical volumes
(a named volume and a path within it), pinned synchronous (D22 — `StorageMixin` calls
`set_sync()`; never `await` a storage node here).

Interactions: orchestration (freezer parcels) · sessions (snapshots) · tasks (spool).
