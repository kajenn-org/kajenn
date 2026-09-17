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

"""API key store tests (core 1b Phase 5): contract suite + file backend specifics.

The contract suite is PARAMETRIZED over FACTORIES (invariant §5.9): callables
returning a fresh configured store over the SAME mount, so a future db backend
plugs into the SAME suite. Today the only backend is ``FileApiKeyStore`` over
a tmp ``site`` mount with key material installed (records are ciphertext at rest).
"""

from __future__ import annotations

import json
import time

import pytest
from cryptography.fernet import Fernet

from genro_storage.exceptions import StorageError

from tests.storage_support import site_storage

from kajenn import ApiKeyStore, FileApiKeyStore
from kajenn.auth.api_key_store import API_KEY_PREFIX

# --- store contract suite (parametrized over FACTORIES, §5.9) ---


def _file_factory(tmp_path):
    """A factory building fresh ``FileApiKeyStore``s over one shared encrypted tmp mount."""
    key = Fernet.generate_key().decode()

    def make(**kwargs):
        storage = site_storage(tmp_path)
        storage.set_encryption_keys(key)
        return FileApiKeyStore(storage, **kwargs)

    return make


STORE_FACTORIES = [_file_factory]


@pytest.fixture(params=STORE_FACTORIES)
def store_factory(request, tmp_path):
    """A callable returning a fresh configured api key store."""
    return request.param(tmp_path)


def _key_id(key: str) -> str:
    """Extract the embedded ``key_id`` out of a ``gak_<key_id>_<secret>`` key."""
    return key.removeprefix(API_KEY_PREFIX).partition("_")[0]


class TestApiKeyStoreContract:
    def test_is_an_api_key_store(self, store_factory) -> None:
        assert isinstance(store_factory(), ApiKeyStore)

    def test_load_all_empty_when_no_keys(self, store_factory) -> None:
        assert store_factory().load_all() == []

    def test_get_unknown_returns_none(self, store_factory) -> None:
        assert store_factory().get("nobody") is None

    def test_delete_absent_returns_false(self, store_factory) -> None:
        assert store_factory().delete("ghost") is False

    def test_issue_returns_a_prefixed_key(self, store_factory) -> None:
        assert store_factory().issue("ci-deploy", ["deploy"]).startswith(API_KEY_PREFIX)

    def test_issue_verify_roundtrip(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", ["deploy"])
        record = store.verify(key)
        assert record is not None
        assert record["label"] == "ci-deploy"
        assert record["tags"] == ["deploy"]

    def test_load_all_returns_every_issued_key(self, store_factory) -> None:
        store = store_factory()
        store.issue("one", [])
        store.issue("two", [])
        assert {r["label"] for r in store.load_all()} == {"one", "two"}

    def test_verify_wrong_secret_fails(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", [])
        assert store.verify(key + "x") is None

    def test_verify_unknown_key_id_returns_none(self, store_factory) -> None:
        assert store_factory().verify(f"{API_KEY_PREFIX}deadbeef_somesecret") is None

    def test_verify_malformed_key_returns_none(self, store_factory) -> None:
        assert store_factory().verify("not-a-key-at-all") is None

    def test_revoked_key_fails_instantly(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", [])
        assert store.revoke(_key_id(key)) is True
        assert store.verify(key) is None

    def test_revoke_absent_returns_false(self, store_factory) -> None:
        assert store_factory().revoke("ghost") is False

    def test_expired_key_fails(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", [], expires_at=time.time() - 10)
        assert store.verify(key) is None

    def test_unexpired_key_succeeds(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", [], expires_at=time.time() + 3600)
        assert store.verify(key) is not None

    def test_disabled_key_fails(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", [])
        record = store.get(_key_id(key))
        assert record is not None
        record["enabled"] = False
        store.save(record)
        assert store.verify(key) is None

    def test_delete_removes_record(self, store_factory) -> None:
        store = store_factory()
        key = store.issue("ci-deploy", [])
        key_id = _key_id(key)
        assert store.delete(key_id) is True
        assert store.get(key_id) is None


# --- FileApiKeyStore specifics (persistence + ciphertext at rest) ---


def _encrypted_storage(tmp_path, key):
    """The site storage over ``tmp_path`` with ``key`` installed as at-rest key material."""
    return site_storage(tmp_path, storage_key=key)


class TestFileApiKeyStore:
    def test_record_survives_a_new_store_on_the_same_mount(self, tmp_path) -> None:
        key_material = Fernet.generate_key().decode()
        store = FileApiKeyStore(_encrypted_storage(tmp_path, key_material))
        key = store.issue("ci-deploy", ["deploy"])
        fresh = FileApiKeyStore(_encrypted_storage(tmp_path, key_material))
        record = fresh.verify(key)
        assert record is not None
        assert record["label"] == "ci-deploy"

    def test_on_disk_payload_is_ciphertext(self, tmp_path) -> None:
        key_material = Fernet.generate_key().decode()
        store = FileApiKeyStore(_encrypted_storage(tmp_path, key_material))
        key = store.issue("ci-deploy", ["deploy"])
        raw = (tmp_path / "api_keys" / f"{_key_id(key)}.json").read_bytes()
        assert raw.startswith(b"#GNRE1:")       # the self-describing envelope
        assert b"ci-deploy" not in raw
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)
        assert Fernet(key_material).decrypt(raw.split(b"\n", 1)[1])

    def test_encrypted_write_without_keys_raises_on_issue(self, tmp_path) -> None:
        store = FileApiKeyStore(site_storage(tmp_path))
        with pytest.raises(StorageError):
            store.issue("ci-deploy", [])
