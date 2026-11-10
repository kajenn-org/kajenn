# Storage — technical notes

**Version**: 0.1 · **Last Updated**: 2026-11-03 · **Status**: working trail

## Docstrings to correct

`StorageMixin` refuses a built `StorageManager` passed as `storage=`: the test
`test_a_built_manager_is_not_a_configuration_value` expects a raise, and
`_build_storage` hands the value to `configure()` as a mount list
(storage_mixin.py:95-109). Two docstrings still describe the earlier behaviour:

- the module docstring, storage_mixin.py:27: "a ``StorageManager`` instance → adopted as-is";
- the class docstring, storage_mixin.py:83-84: "a ``StorageManager`` instance (adopted)".

Both lines are to be removed from the list of shapes `storage=` accepts. The
source is not edited by the documentation audit of 2026-11-03.
