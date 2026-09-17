# Storage

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/060_storage.md` — DIVERGENT, 0 CONVERGE / 1 DIVERGE / 0 SILENT.

The only door to the filesystem. Access goes through storage nodes on logical
volumes, and it is pinned synchronous: `StorageMixin` calls `set_sync()`, and a
storage node call here is never awaited.
