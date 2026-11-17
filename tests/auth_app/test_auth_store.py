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

"""Contract tests of ``AuthStore``, parametrized on the store class.

Every store class in ``STORE_CLASSES`` runs the same suite over a real
encrypted ``site:`` mount in a temporary directory. The tests read only what
a caller of the store sees: return values, raised errors, records read back.
"""

from __future__ import annotations

import json

import time

import pytest
from cryptography.fernet import Fernet

from genro_storage.exceptions import StorageError

from tests.storage_support import MemoryAuthStore, site_storage

from kajenn.auth_app import AuthStore, FileAuthStore

STORE_CLASSES = [FileAuthStore]


@pytest.fixture
def storage(tmp_path):
    """A ``site:`` mount over ``tmp_path`` with key material installed."""
    built = site_storage(tmp_path)
    built.set_encryption_keys(Fernet.generate_key().decode())
    return built


@pytest.fixture(params=STORE_CLASSES)
def store(request, storage):
    """A fresh store of the parametrized class over the encrypted mount."""
    return request.param(storage)


def _user(store, identity="alice", **fields):
    """A complete user record; ``fields`` override the defaults."""
    record = {
        "identity": identity,
        "groups": [],
        "primary_group": None,
        "tags": [],
        "status": "confirmed",
        "system": False,
        "password_hash": store.hash_password("secret"),
        "email": f"{identity}@example.org",
        "external_ids": [],
    }
    record.update(fields)
    return record


def _key_id(key: str) -> str:
    """The ``key_id`` embedded in a ``gak_<key_id>_<secret>`` key."""
    return key.removeprefix("gak_").partition("_")[0]


@pytest.fixture
def two_groups(store):
    """Groups ``staff`` and ``ops`` with distinct tags, and a user ``alice`` in both."""
    store.save_group({"code": "staff", "tags": ["STAFF_READ"]})
    store.save_group({"code": "ops", "tags": ["OPS_DEPLOY"]})
    store.save_user(_user(store, groups=["staff", "ops"], primary_group="staff", tags=["OWN"]))
    return store


class TestRecords:
    def test_is_an_auth_store(self, store) -> None:
        assert isinstance(store, AuthStore)

    def test_empty_store_lists_nothing(self, store) -> None:
        assert store.user_list == []
        assert store.group_list == []
        assert store.tag_list == []
        assert store.token_list == []

    def test_user_roundtrip_keeps_every_field(self, store) -> None:
        record = _user(
            store,
            groups=["staff"],
            primary_group="staff",
            tags=["OWN"],
            external_ids=[{"iss": "https://idp.example.org", "sub": "42"}],
        )
        store.save_user(record)
        assert store.get_user("alice") == record
        assert store.user_list == [record]

    def test_identity_with_separators_roundtrips(self, store) -> None:
        record = _user(store, identity="ns/alice:x")
        store.save_user(record)
        assert store.get_user("ns/alice:x") == record

    def test_group_roundtrip(self, store) -> None:
        store.save_group({"code": "staff", "tags": ["STAFF_READ"]})
        assert store.get_group("staff") == {"code": "staff", "tags": ["STAFF_READ"]}
        assert store.group_list == [{"code": "staff", "tags": ["STAFF_READ"]}]

    def test_unknown_records_are_none(self, store) -> None:
        assert store.get_user("nobody") is None
        assert store.get_group("nobody") is None
        assert store.get_tag("nobody") is None
        assert store.get_token("nobody") is None

    def test_delete_removes_and_reports(self, store) -> None:
        store.save_user(_user(store))
        store.save_group({"code": "staff", "tags": []})
        store.save_tag({"code": "T", "description": "", "owner": "", "reserved": False})
        key = store.issue_token("alice", "*")
        assert store.delete_token(_key_id(key)) is True
        assert store.delete_user("alice") is True
        assert store.delete_group("staff") is True
        assert store.delete_tag("T") is True
        assert store.get_user("alice") is None
        assert store.get_group("staff") is None
        assert store.get_tag("T") is None
        assert store.get_token(_key_id(key)) is None

    def test_delete_absent_is_false(self, store) -> None:
        assert store.delete_user("ghost") is False
        assert store.delete_group("ghost") is False
        assert store.delete_tag("ghost") is False
        assert store.delete_token("ghost") is False

    def test_records_survive_a_new_store_on_the_same_mount(self, store, storage) -> None:
        store.save_user(_user(store))
        assert type(store)(storage).get_user("alice") is not None


@pytest.mark.parametrize("store_class", [FileAuthStore, MemoryAuthStore])
class TestLoginFailures:
    def test_roundtrip_update_and_delete(self, store_class, storage) -> None:
        store = store_class(storage)
        assert store.get_login_failures("ns/alice:x") is None
        assert store.delete_login_failures("ns/alice:x") is False
        record = {"identity": "ns/alice:x", "failed_attempts": 2, "last_failed_at": 123.5}
        store.save_login_failures(record)
        assert store.get_login_failures("ns/alice:x") == record
        record = {**record, "failed_attempts": 3, "last_failed_at": 124.5}
        store.save_login_failures(record)
        assert store.get_login_failures("ns/alice:x") == record
        assert store.delete_login_failures("ns/alice:x") is True
        assert store.get_login_failures("ns/alice:x") is None
        assert store.delete_login_failures("ns/alice:x") is False

    def test_user_writes_and_deletion_leave_failures_unchanged(self, store_class, storage) -> None:
        store = store_class(storage)
        record = {"identity": "alice", "failed_attempts": 2, "last_failed_at": 123.5}
        store.save_login_failures(record)
        store.save_user(_user(store))
        assert store.get_login_failures("alice") == record
        assert store.delete_user("alice") is True
        assert store.get_login_failures("alice") == record


@pytest.mark.parametrize("store_class", [FileAuthStore, MemoryAuthStore])
class TestUserDeleteTokens:
    def test_delete_user_removes_its_tokens_and_leaves_the_others(
        self, store_class, storage
    ) -> None:
        store = store_class(storage)
        store.save_user(_user(store, "alice"))
        store.save_user(_user(store, "bob"))
        alice_keys = [store.issue_token("alice", "*"), store.issue_token("alice", ["OWN"])]
        bob_key = store.issue_token("bob", "*")
        assert store.delete_user("alice") is True
        for key in alice_keys:
            assert store.get_token(_key_id(key)) is None
        assert [token["key_id"] for token in store.token_list] == [_key_id(bob_key)]
        assert store.verify_token(bob_key)["identity"] == "bob"


class TestStatus:
    @pytest.mark.parametrize("status", ["waiting", "confirmed", "banned"])
    def test_the_three_values_are_saved(self, store, status) -> None:
        store.save_user(_user(store, status=status))
        assert store.get_user("alice")["status"] == status

    @pytest.mark.parametrize("status", ["enabled", "conf", "", None])
    def test_another_value_is_refused_and_not_written(self, store, status) -> None:
        with pytest.raises(ValueError, match="status"):
            store.save_user(_user(store, status=status))
        assert store.get_user("alice") is None

    def test_missing_status_is_refused(self, store) -> None:
        record = _user(store)
        del record["status"]
        with pytest.raises(ValueError, match="status"):
            store.save_user(record)

    def test_confirmed_verifies_a_password(self, store) -> None:
        store.save_user(_user(store, status="confirmed"))
        assert store.verify_password("alice", "secret")["identity"] == "alice"

    @pytest.mark.parametrize("status", ["waiting", "banned"])
    def test_waiting_and_banned_do_not_verify_a_password(self, store, status) -> None:
        store.save_user(_user(store, status=status))
        assert store.verify_password("alice", "secret") is None

    def test_confirmed_verifies_a_token(self, store) -> None:
        store.save_user(_user(store, status="confirmed", tags=["OWN"]))
        assert store.verify_token(store.issue_token("alice", "*"))["tags"] == ["OWN"]

    @pytest.mark.parametrize("status", ["waiting", "banned"])
    def test_waiting_and_banned_do_not_verify_a_token(self, store, status) -> None:
        store.save_user(_user(store, status="confirmed"))
        key = store.issue_token("alice", "*")
        store.save_user(_user(store, status=status))
        assert store.verify_token(key) is None


class TestPrimaryGroup:
    def test_primary_group_among_groups_is_saved(self, store) -> None:
        store.save_user(_user(store, groups=["staff", "ops"], primary_group="ops"))
        assert store.get_user("alice")["primary_group"] == "ops"

    def test_groups_without_primary_group_are_saved(self, store) -> None:
        store.save_user(_user(store, groups=["staff"], primary_group=None))
        assert store.get_user("alice")["primary_group"] is None

    def test_primary_group_outside_groups_is_refused_and_not_written(self, store) -> None:
        with pytest.raises(ValueError, match="primary_group"):
            store.save_user(_user(store, groups=["staff"], primary_group="ops"))
        assert store.get_user("alice") is None


class TestGroupDelete:
    def test_group_with_a_member_is_refused_and_stays(self, two_groups) -> None:
        with pytest.raises(ValueError, match="alice"):
            two_groups.delete_group("ops")
        assert two_groups.get_group("ops") is not None

    def test_group_is_deleted_once_no_user_has_it(self, two_groups) -> None:
        two_groups.save_user(_user(two_groups, groups=["staff"], primary_group="staff"))
        assert two_groups.delete_group("ops") is True
        assert two_groups.get_group("ops") is None


class TestPassword:
    def test_wrong_password_is_refused(self, store) -> None:
        store.save_user(_user(store))
        assert store.verify_password("alice", "wrong") is None

    def test_unknown_user_is_refused(self, store) -> None:
        assert store.verify_password("nobody", "secret") is None

    def test_user_without_password_is_refused(self, store) -> None:
        store.save_user(_user(store, password_hash=None))
        assert store.verify_password("alice", "") is None

    def test_system_user_does_not_verify_a_password(self, store) -> None:
        store.save_user(_user(store, system=True))
        assert store.verify_password("alice", "secret") is None

    def test_system_user_verifies_a_token(self, store) -> None:
        store.save_user(_user(store, system=True, password_hash=None, tags=["JOB"]))
        assert store.verify_token(store.issue_token("alice", ["JOB"]))["tags"] == ["JOB"]

    def test_hash_is_salted_and_checks(self, store) -> None:
        first = store.hash_password("pw")
        assert first != store.hash_password("pw")
        assert store.check_password("pw", first) is True
        assert store.check_password("other", first) is False

    @pytest.mark.parametrize(
        "stored", ["", "garbage", "scrypt$n=x,r=8,p=1$AA==$AA==", "md5$n=1,r=1,p=1$AA==$AA=="]
    )
    def test_malformed_hash_is_false(self, store, stored) -> None:
        assert store.check_password("pw", stored) is False


class TestUserTags:
    def test_indicated_group_gives_its_tags_and_not_the_other(self, two_groups) -> None:
        assert two_groups.get_user_tags("alice", group="ops") == ["OWN", "OPS_DEPLOY"]

    def test_no_group_means_the_primary_group(self, two_groups) -> None:
        assert two_groups.get_user_tags("alice") == ["OWN", "STAFF_READ"]

    def test_groups_without_primary_give_only_own_tags(self, store) -> None:
        store.save_group({"code": "staff", "tags": ["STAFF_READ"]})
        store.save_user(_user(store, groups=["staff"], primary_group=None, tags=["OWN"]))
        assert store.get_user_tags("alice") == ["OWN"]

    def test_group_the_user_does_not_belong_to_raises(self, two_groups) -> None:
        two_groups.save_group({"code": "finance", "tags": ["PAY"]})
        with pytest.raises(ValueError, match="finance"):
            two_groups.get_user_tags("alice", group="finance")

    def test_unknown_user_raises(self, store) -> None:
        with pytest.raises(ValueError, match="nobody"):
            store.get_user_tags("nobody")

    def test_tag_shared_by_user_and_group_appears_once(self, store) -> None:
        store.save_group({"code": "staff", "tags": ["OWN", "STAFF_READ"]})
        store.save_user(_user(store, groups=["staff"], primary_group="staff", tags=["OWN"]))
        assert store.get_user_tags("alice") == ["OWN", "STAFF_READ"]


class TestTagRecord:
    def test_tag_with_parent_roundtrips(self, store) -> None:
        parent = {
            "code": "REPORTS",
            "description": "Reports",
            "owner": "",
            "reserved": False,
            "parent": None,
        }
        child = {
            "code": "REPORTS_EXPORT",
            "description": "Export reports",
            "owner": "billing",
            "reserved": True,
            "parent": "REPORTS",
        }
        store.save_tag(parent)
        store.save_tag(child)
        assert store.get_tag("REPORTS_EXPORT") == child
        assert sorted(store.tag_list, key=lambda tag: tag["code"]) == [parent, child]

    def test_parent_tag_does_not_give_the_child(self, store) -> None:
        store.save_tag({"code": "REPORTS", "description": "", "owner": "", "reserved": False})
        store.save_tag(
            {
                "code": "REPORTS_EXPORT",
                "description": "",
                "owner": "",
                "reserved": False,
                "parent": "REPORTS",
            }
        )
        store.save_group({"code": "staff", "tags": ["REPORTS"]})
        store.save_user(_user(store, groups=["staff"], primary_group="staff", tags=["REPORTS"]))
        assert "REPORTS_EXPORT" not in store.get_user_tags("alice")
        key = store.issue_token("alice", ["REPORTS", "REPORTS_EXPORT"])
        assert store.verify_token(key)["tags"] == ["REPORTS"]


class TestTokenIssue:
    def test_issue_returns_a_gak_key_and_writes_the_record(self, store) -> None:
        store.save_user(_user(store))
        key = store.issue_token("alice", ["OWN"], expires_at=None)
        assert key.startswith("gak_")
        record = store.get_token(_key_id(key))
        assert record["user"] == "alice"
        assert record["tags"] == ["OWN"]
        assert record["enabled"] is True
        assert record["expires_at"] is None
        assert "group" not in record
        secret = key.split("_", 2)[2]
        assert secret not in str(record)

    def test_star_is_kept_as_the_string(self, store) -> None:
        store.save_user(_user(store))
        assert store.get_token(_key_id(store.issue_token("alice", "*")))["tags"] == "*"

    def test_empty_list_fails_and_creates_no_record(self, store) -> None:
        store.save_user(_user(store))
        with pytest.raises(ValueError, match=r"list of tags or '\*'"):
            store.issue_token("alice", [])
        assert store.token_list == []

    def test_a_string_other_than_star_fails(self, store) -> None:
        store.save_user(_user(store))
        with pytest.raises(ValueError):
            store.issue_token("alice", "OWN")
        assert store.token_list == []


class TestTokenVerify:
    def test_star_opens_own_tags_and_primary_group_tags(self, two_groups) -> None:
        result = two_groups.verify_token(two_groups.issue_token("alice", "*"))
        assert result == {
            "identity": "alice",
            "group": "staff",
            "tags": ["OWN", "STAFF_READ"],
            "expires_at": None,
        }

    def test_list_opens_held_tags_including_a_non_primary_group(self, two_groups) -> None:
        key = two_groups.issue_token("alice", ["OPS_DEPLOY", "OWN", "NOT_HELD"])
        assert two_groups.verify_token(key)["tags"] == ["OPS_DEPLOY", "OWN"]

    def test_tag_removed_from_user_leaves_the_token(self, two_groups) -> None:
        key = two_groups.issue_token("alice", ["OWN", "STAFF_READ"])
        assert two_groups.verify_token(key)["tags"] == ["OWN", "STAFF_READ"]
        user = two_groups.get_user("alice")
        user["tags"] = []
        two_groups.save_user(user)
        assert two_groups.verify_token(key)["tags"] == ["STAFF_READ"]

    def test_expired_token_is_refused(self, store) -> None:
        store.save_user(_user(store))
        key = store.issue_token("alice", "*", expires_at=time.time() - 1)
        assert store.verify_token(key) is None

    def test_unexpired_token_is_accepted(self, store) -> None:
        store.save_user(_user(store))
        expires_at = time.time() + 60
        key = store.issue_token("alice", "*", expires_at=expires_at)
        assert store.verify_token(key)["expires_at"] == expires_at

    def test_revoked_token_is_refused(self, store) -> None:
        store.save_user(_user(store))
        key = store.issue_token("alice", "*")
        assert store.revoke_token(_key_id(key)) is True
        assert store.get_token(_key_id(key))["enabled"] is False
        assert store.verify_token(key) is None

    def test_revoke_absent_is_false(self, store) -> None:
        assert store.revoke_token("ghost") is False

    def test_token_of_a_deleted_user_is_refused(self, store) -> None:
        store.save_user(_user(store))
        key = store.issue_token("alice", "*")
        store.delete_user("alice")
        assert store.verify_token(key) is None

    def test_wrong_secret_is_refused(self, store) -> None:
        store.save_user(_user(store))
        key = store.issue_token("alice", "*")
        assert store.verify_token(f"gak_{_key_id(key)}_wrongsecret") is None

    @pytest.mark.parametrize("key", ["", "gak_", "gak_abc", "gak__x", "xyz_abc_def"])
    def test_malformed_key_is_refused(self, store, key) -> None:
        assert store.verify_token(key) is None


class TestExternalLookups:
    def test_a_user_is_found_by_iss_and_sub(self, store) -> None:
        linked = _user(store, external_ids=[{"iss": "https://idp.example.org", "sub": "42"}])
        store.save_user(linked)
        store.save_user(_user(store, identity="bob"))
        assert store.get_user_by_external_id("https://idp.example.org", "42") == linked

    def test_iss_and_sub_must_both_match(self, store) -> None:
        store.save_user(
            _user(store, external_ids=[{"iss": "https://idp.example.org", "sub": "42"}])
        )
        assert store.get_user_by_external_id("https://other.example.org", "42") is None
        assert store.get_user_by_external_id("https://idp.example.org", "43") is None

    def test_a_user_with_several_external_ids_is_found_by_each(self, store) -> None:
        store.save_user(
            _user(
                store,
                external_ids=[
                    {"iss": "https://idp.example.org", "sub": "42"},
                    {"iss": "https://other.example.org", "sub": "7"},
                ],
            )
        )
        assert store.get_user_by_external_id("https://other.example.org", "7")["identity"] == (
            "alice"
        )

    def test_users_are_listed_by_email_compared_lowercased(self, store) -> None:
        store.save_user(_user(store, email="Alice@Example.org"))
        store.save_user(_user(store, identity="alice2", email="alice@example.org"))
        store.save_user(_user(store, identity="bob"))
        found = store.get_user_list_by_email("ALICE@example.ORG")
        assert sorted(user["identity"] for user in found) == ["alice", "alice2"]

    def test_an_unknown_email_lists_nothing(self, store) -> None:
        store.save_user(_user(store, email=None))
        assert store.get_user_list_by_email("nobody@example.org") == []


class TestFileAuthStore:
    def test_write_without_key_material_raises(self, tmp_path) -> None:
        store = FileAuthStore(site_storage(tmp_path))
        with pytest.raises(StorageError):
            store.save_user(_user(store))

    def test_records_on_disk_are_ciphertext(self, tmp_path) -> None:
        key_material = Fernet.generate_key().decode()
        store = FileAuthStore(site_storage(tmp_path, key_material))
        store.save_user(_user(store, "dave"))
        key = store.issue_token("dave", ["OWN"], expires_at=None)
        failures = {"identity": "dave", "failed_attempts": 2, "last_failed_at": 123.5}
        store.save_login_failures(failures)
        reopened = FileAuthStore(site_storage(tmp_path, key_material))
        assert reopened.get_login_failures("dave") == failures
        for raw in (
            (tmp_path / "auth" / "users" / "dave.json").read_bytes(),
            (tmp_path / "auth" / "login_failures" / "dave.json").read_bytes(),
            (tmp_path / "auth" / "tokens" / f"{_key_id(key)}.json").read_bytes(),
        ):
            assert raw.startswith(b"#GNRE1:")
            assert b"dave" not in raw
            assert b"scrypt" not in raw
            with pytest.raises(json.JSONDecodeError):
                json.loads(raw)
            assert Fernet(key_material).decrypt(raw.split(b"\n", 1)[1])

    def test_files_of_the_previous_stores_are_not_read(self, storage) -> None:
        user = {"identity": "alice", "tags": ["ADMIN"], "enabled": True}
        storage.node("site:users/alice.json").write_text(json.dumps(user), encrypted=True)
        key = {"key_id": "k1", "label": "ci", "tags": ["deploy"], "enabled": True}
        storage.node("site:api_keys/k1.json").write_text(json.dumps(key), encrypted=True)
        store = FileAuthStore(storage)
        assert store.user_list == []
        assert store.token_list == []
        assert store.get_user("alice") is None
