# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""User store tests (core 1b Phase 4): contract suite + file backend specifics.

The contract suite is PARAMETRIZED over FACTORIES (invariant §5.9): callables
returning a fresh configured store over the SAME mount, so a future db backend
plugs into the SAME suite. Today the only backend is ``FileUserStore`` over an
a tmp ``site`` mount with key material installed (records are ciphertext at rest).
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from genro_storage.exceptions import StorageError

from tests.storage_support import site_storage

from kajenn import FileUserStore, UserStore

# --- store contract suite (parametrized over FACTORIES, §5.9) ---


def _file_factory(tmp_path):
    """A factory building fresh ``FileUserStore``s over one shared encrypted tmp mount."""
    key = Fernet.generate_key().decode()

    def make(**kwargs):
        storage = site_storage(tmp_path)
        storage.set_encryption_keys(key)
        return FileUserStore(storage, **kwargs)

    return make


STORE_FACTORIES = [_file_factory]


@pytest.fixture(params=STORE_FACTORIES)
def store_factory(request, tmp_path):
    """A callable returning a fresh configured user store."""
    return request.param(tmp_path)


def _record(store, identity="alice", password="secret", tags=None, enabled=True):
    """Build a persistable record hashing the password through the store."""
    return {
        "identity": identity,
        "password_hash": store.hash_password(password),
        "tags": ["staff"] if tags is None else tags,
        "enabled": enabled,
    }


class TestUserStoreContract:
    def test_is_a_user_store(self, store_factory) -> None:
        assert isinstance(store_factory(), UserStore)

    def test_save_get_roundtrip(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice", tags=["staff", "ops"]))
        fetched = store.get("alice")
        assert fetched is not None
        assert fetched["identity"] == "alice"
        assert fetched["tags"] == ["staff", "ops"]

    def test_get_unknown_returns_none(self, store_factory) -> None:
        assert store_factory().get("nobody") is None

    def test_load_all_returns_every_record(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice"))
        store.save(_record(store, "bob"))
        assert {r["identity"] for r in store.load_all()} == {"alice", "bob"}

    def test_load_all_empty_when_no_users(self, store_factory) -> None:
        assert store_factory().load_all() == []

    def test_delete_removes_record(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice"))
        assert store.delete("alice") is True
        assert store.get("alice") is None

    def test_delete_absent_returns_false(self, store_factory) -> None:
        assert store_factory().delete("ghost") is False

    def test_save_update_overwrites_in_place(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice", tags=["staff"]))
        store.save(_record(store, "alice", tags=["admin"]))
        fetched = store.get("alice")
        assert fetched is not None
        assert fetched["tags"] == ["admin"]
        assert len(store.load_all()) == 1

    def test_verify_right_password_returns_record(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice", password="s3cret"))
        result = store.verify("alice", "s3cret")
        assert result is not None
        assert result["identity"] == "alice"

    def test_verify_wrong_password_returns_none(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice", password="s3cret"))
        assert store.verify("alice", "wrong") is None

    def test_verify_unknown_identity_returns_none(self, store_factory) -> None:
        assert store_factory().verify("ghost", "whatever") is None

    def test_verify_disabled_user_fails(self, store_factory) -> None:
        store = store_factory()
        store.save(_record(store, "alice", password="s3cret", enabled=False))
        assert store.verify("alice", "s3cret") is None


class TestPasswordHashing:
    def test_hash_differs_per_salt(self, store_factory) -> None:
        store = store_factory()
        assert store.hash_password("same") != store.hash_password("same")

    def test_check_password_roundtrip(self, store_factory) -> None:
        store = store_factory()
        stored = store.hash_password("secret")
        assert store.check_password("secret", stored) is True
        assert store.check_password("nope", stored) is False

    def test_check_password_malformed_hash_is_false(self, store_factory) -> None:
        assert store_factory().check_password("secret", "garbage") is False

    def test_check_password_malformed_params_is_false(self, store_factory) -> None:
        """A 4-part hash with a broken params section yields False (item 7)."""
        stored = "scrypt$badparams$c2FsdA==$aGFzaA=="
        assert store_factory().check_password("secret", stored) is False

    def test_check_password_non_integer_params_is_false(self, store_factory) -> None:
        stored = "scrypt$n=big,r=8,p=1$c2FsdA==$aGFzaA=="
        assert store_factory().check_password("secret", stored) is False

    def test_check_password_missing_param_key_is_false(self, store_factory) -> None:
        stored = "scrypt$m=1,r=8,p=1$c2FsdA==$aGFzaA=="
        assert store_factory().check_password("secret", stored) is False


# --- FileUserStore specifics (persistence + ciphertext at rest) ---


def _encrypted_storage(tmp_path, key):
    """The site storage over ``tmp_path`` with ``key`` installed as at-rest key material."""
    return site_storage(tmp_path, storage_key=key)


class TestFileUserStore:
    def test_record_survives_a_new_store_on_the_same_mount(self, tmp_path) -> None:
        key = Fernet.generate_key().decode()
        store = FileUserStore(_encrypted_storage(tmp_path, key))
        store.save(_record(store, "carol", tags=["ops"]))
        fresh = FileUserStore(_encrypted_storage(tmp_path, key))
        restored = fresh.get("carol")
        assert restored is not None
        assert restored["identity"] == "carol"
        assert restored["tags"] == ["ops"]
        assert fresh.verify("carol", "secret") is not None

    def test_on_disk_payload_is_ciphertext(self, tmp_path) -> None:
        key = Fernet.generate_key().decode()
        store = FileUserStore(_encrypted_storage(tmp_path, key))
        store.save(_record(store, "dave", password="s3cret"))
        raw = (tmp_path / "users" / "dave.json").read_bytes()
        assert raw.startswith(b"#GNRE1:")       # the self-describing envelope
        assert b"dave" not in raw
        assert b"scrypt" not in raw
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)
        assert Fernet(key).decrypt(raw.split(b"\n", 1)[1])

    def test_encrypted_write_without_keys_raises_on_save(self, tmp_path) -> None:
        store = FileUserStore(site_storage(tmp_path))
        with pytest.raises(StorageError):
            store.save(_record(store, "erin"))
