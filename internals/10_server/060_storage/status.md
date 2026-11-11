# Storage — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Synchronous storage capability

`StorageMixin` calls `set_sync` before the rest of the construction chain runs,
then builds the `StorageManager` it owns (storage_mixin.py:88-93). Node
operations are synchronous in that inherited context.

`storage=` accepts a list of genro-storage mount configurations, handed to
`configure()` as given (storage_mixin.py:95-109). An omitted or empty list
produces the default layout: two local mounts, `site:` and `home:`, both on the
working directory read at construction (storage_mixin.py:111-121). Tests:
`test_default_storage_is_the_two_mounts_on_the_deployment_directory`,
`test_an_empty_mount_list_falls_back_to_the_default_layout`.

A built `StorageManager` passed as `storage=` is refused: the construction
raises (`test_a_built_manager_is_not_a_configuration_value`). The module
docstring (storage_mixin.py:27) and the class docstring (storage_mixin.py:84)
still say such a manager is adopted.

A composition without `StorageMixin` has no storage attribute
(`test_base_server_has_no_storage_attribute`).

Claim anchors: [`StorageMixin`](../../../src/kajenn/storage_mixin.py#StorageMixin), [`_build_storage`](../../../src/kajenn/storage_mixin.py#StorageMixin._build_storage), [`_default_mounts`](../../../src/kajenn/storage_mixin.py#StorageMixin._default_mounts), [`DEFAULT_SITE_MOUNT`](../../../src/kajenn/storage_mixin.py#DEFAULT_SITE_MOUNT), [`DEFAULT_HOME_MOUNT`](../../../src/kajenn/storage_mixin.py#DEFAULT_HOME_MOUNT).

## The default layout in a recipe

`BaseConfiguration.storage_section` writes the `storage` section with
`storage_key` and hands it to `storage_mounts` (config/builder.py:183-185).
`AsgiConfigBuilder.storage_mounts` writes `DEFAULT_SITE_MOUNT` and
`DEFAULT_HOME_MOUNT` as two `local` recipe lines with absolute `base_path`
values (config/builder.py:117-137). `site:` is on the working directory read
when the recipe runs. `home:` is on `site_home`, and on the folder of `site:`
when `site_home` is unset.

`site_home` is the folder the recipe also writes as `site(home=...)` through
`site_section` (config/builder.py:99-115; the element is config/elements.py:109).
A recipe that declares neither `site_name` nor `site_home` writes no `site`
element. A site overrides `storage_mounts` to declare another layout.

Tests: `test_recipe_storage_section_serves_declared_mounts`,
`test_a_key_only_recipe_section_still_serves_the_default_layout`.

Claim anchors: [`site_section`](../../../src/kajenn/config/builder.py#AsgiConfigBuilder.site_section), [`storage_mounts`](../../../src/kajenn/config/builder.py#AsgiConfigBuilder.storage_mounts), [`storage_key`](../../../src/kajenn/config/builder.py#BaseConfiguration.storage_key), [`site`](../../../src/kajenn/config/elements.py#AsgiServerGrammar.site).

## Encryption per write

Encryption is requested per write, with `encrypted=True`. `storage_key=` is the
key material, handed to `configure(storage_key=...)` (storage_mixin.py:108). It
is comma-separated Fernet keys, each optionally prefixed `<domain>:`; the first
key of a domain encrypts and all of them decrypt (storage_mixin.py:33-38). An
encrypted file on disk starts with the line `#GNRE1:`, so a read declares
nothing (storage_mixin.py:40-43). On a recipe, `BaseConfiguration.storage_key`
is the hook a site sets to a resolver (config/builder.py:163).

- Omitted key material leaves encryption dormant, and an `encrypted=True` write
  raises (`test_encrypted_write_without_key_material_raises`,
  `test_omitted_storage_key_leaves_encryption_dormant`).
- Key material that resolves empty raises at construction
  (`test_empty_storage_key_raises_at_construction`).
- Encrypted and plain writes share one directory tree
  (`test_encrypted_and_plain_writes_share_one_tree`).

Claim anchors: [`StorageMixin`](../../../src/kajenn/storage_mixin.py#StorageMixin), [`storage_key`](../../../src/kajenn/config/builder.py#BaseConfiguration.storage_key).

## Storage consumers

- The user store writes every record with `encrypted=True`
  (auth/user_store.py:229-233).
- The API-key store writes every record with `encrypted=True`
  (auth/api_key_store.py:236-240).
- `AuthMixin._require_storage` hands a `{mount, prefix}` store the server's
  storage, and raises `RuntimeError` when the server has none
  (auth/mixin.py:212-223). The store is built with that storage
  (auth/mixin.py:200-201).
- The task spool writes plain on the `site` mount under `batches/`
  (tasks/spool.py:62-63, 134-139). `LocalTaskExecutor` builds it over
  `server.storage` (tasks/executor.py:82).
- `FileTaskStore` writes plain under `tasks/` on the `site` mount, or on the
  mount of `tasks(mount=...)` (tasks/store.py:127, 157-165;
  tasks/manager.py:92).

Claim anchors: [`_require_storage`](../../../src/kajenn/auth/mixin.py#AuthMixin._require_storage), [`FileTaskStore`](../../../src/kajenn/tasks/store.py#FileTaskStore).

## Existing direct filesystem consumers

The storage-node rule is a design policy, not a literal description of every
filesystem call in the repository. `MemorySessionStore.save_snapshot` and
`load_snapshot` use `Path` and `pickle` directly (session/store.py:152-174), and
CLI/reload support reads local files. Those exceptions remain visible; this
documentation task does not migrate them or silently approve new ones.

Claim anchors: [`SessionStore`](../../../src/kajenn/session/store.py#SessionStore), [`save_snapshot`](../../../src/kajenn/session/store.py#MemorySessionStore.save_snapshot), [`StorageMixin`](../../../src/kajenn/storage_mixin.py#StorageMixin).

## Source and test evidence

- [src/kajenn/storage_mixin.py](../../../src/kajenn/storage_mixin.py)
- [src/kajenn/config/builder.py](../../../src/kajenn/config/builder.py)
- [src/kajenn/session/store.py](../../../src/kajenn/session/store.py)
- [tests/core/test_storage_mixin.py](../../../tests/core/test_storage_mixin.py)
- [tests/core/test_session.py](../../../tests/core/test_session.py)
