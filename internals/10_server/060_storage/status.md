# Storage — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Synchronous storage capability

`StorageMixin` owns a `StorageManager` and calls `set_sync` before construction
continues. Node operations are synchronous in that inherited context. An
omitted or empty mount list produces one local `site:` mount on the current
working directory; a supplied manager is adopted. Storage configuration passes
through the storage grammar.

Encryption is requested per write. `storage_key` configures key material;
credential stores request encryption while task operational data stays plain.
A composition without `StorageMixin` has no storage attribute.

Claim anchors: [`StorageMixin`](../../../src/kajenn/storage_mixin.py#L74).

## Existing direct filesystem consumers

The storage-node rule is a design policy, not a literal description of every
filesystem call in the repository. `FreezeHandler` and worker parcel methods
use Path/OS/pickle directly. Session snapshot persistence also uses Path and
pickle, while CLI/reload support reads local files. The declared freezer
exception remains visible; this documentation task does not migrate these
consumers or silently approve new exceptions.

Claim anchors: [`FreezeHandler`](../../../src/kajenn_orchestra/orchestration/freeze_handler.py#L88).

## Source and test evidence

- [src/kajenn/storage_mixin.py](../../../src/kajenn/storage_mixin.py)
- [src/kajenn/config/builder.py](../../../src/kajenn/config/builder.py)
- [src/kajenn/session/store.py](../../../src/kajenn/session/store.py)
- [src/kajenn_orchestra/orchestration/freeze_handler.py](../../../src/kajenn_orchestra/orchestration/freeze_handler.py)
- [src/kajenn_orchestra/orchestration/spa_worker.py](../../../src/kajenn_orchestra/orchestration/spa_worker.py)
- [tests/core/test_storage_mixin.py](../../../tests/core/test_storage_mixin.py)
- [tests/core/test_session.py](../../../tests/core/test_session.py)
- [tests/spa/orchestration/test_orchestration_freeze_handler.py](../../../tests/spa/orchestration/test_orchestration_freeze_handler.py)
