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

"""AuthStore — one store for users, groups, tags, tokens and login failures (contract + file backend).

``AuthStore`` is the contract. The rules live in the base class, so every
backend computes tags, hashes passwords and verifies tokens the same way:

- the tags of a user in a group;
- the scrypt hash of a password and its check;
- the issue and the verification of a ``gak_`` token.

A backend implements only persistence. ``FileAuthStore`` is the file backend.

Contract::

    AuthStore:
        user_list -> list[dict]                     # property: every user record
        group_list -> list[dict]                    # property: every group record
        tag_list -> list[dict]                      # property: every tag record
        token_list -> list[dict]                    # property: every token record
        get_login_failures(identity) -> dict | None
        save_login_failures(record) -> None
        delete_login_failures(identity) -> bool
        get_user(identity) -> dict | None
        get_group(code) -> dict | None
        get_tag(code) -> dict | None
        get_token(key_id) -> dict | None
        save_user(record) -> None                   # ValueError on a refused record
        save_group(record) -> None
        save_tag(record) -> None
        save_token(record) -> None
        delete_user(identity) -> bool               # True if a record was removed; deletes its tokens and login failures
        delete_group(code) -> bool                  # ValueError while it has members
        delete_tag(code) -> bool
        delete_token(key_id) -> bool
        get_user_by_external_id(iss, sub) -> dict | None
        get_user_list_by_email(email) -> list[dict]
        get_user_tags(identity, group=None) -> list[str]
        verify_password(identity, password) -> dict | None
        hash_password(password) -> str
        check_password(password, stored) -> bool
        issue_token(user, tags, expires_at=None) -> str
        revoke_token(key_id) -> bool                # True if a record was disabled
        verify_token(key) -> dict | None

``save_user`` checks the record and hands it to ``write_user``, the one
persistence method a backend implements for users. The other ``save_*``
methods are persistence methods themselves.

The user record::

    {
      "identity": "alice",
      "groups": ["staff", "ops"],
      "primary_group": "staff",            # one of groups, or None
      "tags": ["reports"],
      "status": "confirmed",               # waiting | confirmed | banned
      "system": false,
      "password_hash": "scrypt$n=16384,r=8,p=1$<salt-b64>$<hash-b64>",  # or None
      "email": "alice@example.org",
      "firstname": "Alice",                # person fields: email, firstname,
      "lastname": "Liddell",               # lastname, photo (None when unknown)
      "photo": None,
      "external_ids": [{"iss": "https://idp.example.org", "sub": "248289761001"}]
    }

``status`` has three fixed values. Only ``confirmed`` authenticates.
``save_user`` raises ``ValueError`` for any other value, and for a
``primary_group`` that is not one of ``groups``. A ``system`` user is
a program: it never verifies a password and enters only with a token.
The separate ``login_failures`` record is keyed by identity::

    {"identity": "alice", "failed_attempts": 2, "last_failed_at": 123.5}

The login surface enforces lockout using this record and deletes it on success.
User writes do not change login failures; ``delete_user`` deletes them.

``get_user_by_external_id`` returns the user whose ``external_ids`` holds
``{iss, sub}``, or None. ``get_user_list_by_email`` returns every user whose
``email`` equals ``email``; both sides are compared lowercased. The base class
implements both over ``user_list``; a database backend may rewrite them with
a query.

The group record: ``{"code": "staff", "tags": ["reports"]}``.
``delete_group`` raises ``ValueError`` while a user has the group among its
``groups``. Each backend enforces this constraint with its own means.

The tag record::

    {"code": "reports", "description": "Read reports", "owner": "",
     "reserved": false, "parent": None}

``owner`` is the code of the application that declared the tag, empty when the
installer created it. ``parent`` is optional: the tree groups tags and never
grants anything. Holding a parent tag does not give its children.

The token record::

    {
      "key_id": "3f9c2a1b7d4e6a05",
      "user": "alice",                    # identity of the owning user
      "tags": ["reports"],                # a non-empty list, or the string "*"
      "expires_at": None,                 # POSIX timestamp, or None: never expires
      "enabled": true,
      "secret_hash": "<sha256 hex>"
    }

The key is ``gak_<key_id>_<secret>``. Only the sha256 hash of the secret is
stored; the full key exists in clear once, as ``issue_token``'s return value.

``delete_user`` deletes every token whose ``user`` is the deleted identity and
the identity's ``login_failures`` record: a user created later with the same
identity inherits neither. Each backend enforces this constraint with its own
means.

Tag computation (``get_user_tags``): the user's own tags plus the tags of one
group. ``group=None`` means the user's ``primary_group``; a user without a
primary group gets only its own tags. A ``group`` the user does not belong to
raises ``ValueError``; so does an unknown identity. A group listed on the
user with no group record adds no tags. The store does not check that an
assigned tag exists in the tag list.

Token tags are a subset of the user's tags, recomputed at every verification:

- ``"*"``: the user's own tags plus those of its primary group;
- a list: only the listed tags the user holds, as its own or through any of
  its groups; the others are dropped.

``issue_token`` raises ``ValueError`` for an empty list and writes nothing.

``verify_password`` returns the user record, or None when the user is unknown,
its status does not authenticate, it is a system user, it has no password, or
the password does not match. ``verify_token`` returns
``{"identity", "group", "tags", "expires_at"}``, where ``group`` is the user's
primary group, or None when the key is malformed, unknown, disabled, expired,
does not match, or its user is absent or does not authenticate. Secrets are
compared in constant time.

``FileAuthStore`` keeps one JSON file per record at
``<mount>:<prefix>/<kind>/<key>.json``, with ``kind`` one of ``users``,
``groups``, ``tags``, ``tokens``, ``login_failures``; the default is ``site:auth``. The key is the
record's identity, code or ``key_id``, percent-encoded. Every record is
written ``encrypted=True``: without installed key material the write raises
``StorageError``. All I/O is synchronous: an async caller wraps it in
``server.run_sync()``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from typing import Any

from genro_storage import StorageManager, StorageNode

__all__ = ["AuthStore", "FileAuthStore"]

USER_STATUSES = ("waiting", "confirmed", "banned")
PERSON_FIELDS = ("email", "firstname", "lastname", "photo")
AUTHENTICATING_STATUS = "confirmed"
ALL_TAGS = "*"

TOKEN_PREFIX = "gak_"
KEY_ID_BYTES = 8
SECRET_BYTES = 32

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16


class AuthStore:
    """Contract for the store of users, groups, tags, tokens and login failures (see module docstring).

    Subclasses implement persistence. Tag computation, password hashing and
    token issue and verification live here.
    """

    __slots__ = ()

    # ── persistence: implemented by a backend ─────────────────────────

    @property
    def user_list(self) -> list[dict[str, Any]]:
        """Every user record."""
        raise NotImplementedError

    @property
    def group_list(self) -> list[dict[str, Any]]:
        """Every group record."""
        raise NotImplementedError

    @property
    def tag_list(self) -> list[dict[str, Any]]:
        """Every tag record."""
        raise NotImplementedError

    @property
    def token_list(self) -> list[dict[str, Any]]:
        """Every token record."""
        raise NotImplementedError

    def get_login_failures(self, identity: str) -> dict[str, Any] | None:
        """The login failure record for ``identity``, or None."""
        raise NotImplementedError

    def save_login_failures(self, record: dict[str, Any]) -> None:
        """Persist login failures, keyed by ``identity``."""
        raise NotImplementedError

    def delete_login_failures(self, identity: str) -> bool:
        """Remove login failures. True if removed, False if absent."""
        raise NotImplementedError

    def get_user(self, identity: str) -> dict[str, Any] | None:
        """The user record for ``identity``, or None."""
        raise NotImplementedError

    def get_group(self, code: str) -> dict[str, Any] | None:
        """The group record for ``code``, or None."""
        raise NotImplementedError

    def get_tag(self, code: str) -> dict[str, Any] | None:
        """The tag record for ``code``, or None."""
        raise NotImplementedError

    def get_token(self, key_id: str) -> dict[str, Any] | None:
        """The token record for ``key_id``, or None."""
        raise NotImplementedError

    def write_user(self, record: dict[str, Any]) -> None:
        """Persist a checked user record (create or update)."""
        raise NotImplementedError

    def save_group(self, record: dict[str, Any]) -> None:
        """Persist a group record (create or update)."""
        raise NotImplementedError

    def save_tag(self, record: dict[str, Any]) -> None:
        """Persist a tag record (create or update)."""
        raise NotImplementedError

    def save_token(self, record: dict[str, Any]) -> None:
        """Persist a token record (create or update)."""
        raise NotImplementedError

    def delete_user(self, identity: str) -> bool:
        """Remove a user, every token whose ``user`` is ``identity`` and its login failures.

        True if a user record was removed, False if absent.
        """
        raise NotImplementedError

    def delete_group(self, code: str) -> bool:
        """Remove a group. True if a record was removed, False if absent.

        Raises ``ValueError`` while a user has the group among its ``groups``.
        """
        raise NotImplementedError

    def delete_tag(self, code: str) -> bool:
        """Remove a tag. True if a record was removed, False if absent."""
        raise NotImplementedError

    def delete_token(self, key_id: str) -> bool:
        """Remove a token. True if a record was removed, False if absent."""
        raise NotImplementedError

    # ── users ──────────────────────────────────────────────────────────

    def save_user(self, record: dict[str, Any]) -> None:
        """Check the user record and persist it through ``write_user``.

        Raises ``ValueError`` when ``status`` is not one of the three fixed
        values, or when ``primary_group`` is set and is not one of ``groups``.
        """
        if record.get("status") not in USER_STATUSES:
            raise ValueError(
                f"user {record.get('identity')!r}: status {record.get('status')!r} "
                f"is not one of {', '.join(USER_STATUSES)}"
            )
        primary_group = record.get("primary_group")
        if primary_group is not None and primary_group not in record.get("groups", []):
            raise ValueError(
                f"user {record.get('identity')!r}: primary_group {primary_group!r} "
                "is not one of its groups"
            )
        self.write_user(record)

    def get_user_by_external_id(self, iss: str, sub: str) -> dict[str, Any] | None:
        """The user whose ``external_ids`` holds ``{iss, sub}``, or None."""
        for user in self.user_list:
            for external_id in user.get("external_ids", []):
                if external_id.get("iss") == iss and external_id.get("sub") == sub:
                    return user
        return None

    def get_user_list_by_email(self, email: str) -> list[dict[str, Any]]:
        """Every user whose ``email`` equals ``email``, compared lowercased."""
        wanted = email.lower()
        return [user for user in self.user_list if (user.get("email") or "").lower() == wanted]

    def get_user_tags(self, identity: str, group: str | None = None) -> list[str]:
        """The user's own tags plus the tags of ``group`` (default: its primary group).

        Raises ``ValueError`` for an unknown identity or a group the user
        does not belong to.
        """
        user = self.get_user(identity)
        if user is None:
            raise ValueError(f"unknown user {identity!r}")
        active_group = user.get("primary_group") if group is None else group
        if active_group is None:
            return list(user.get("tags", []))
        if active_group not in user.get("groups", []):
            raise ValueError(f"user {identity!r} does not belong to group {active_group!r}")
        return self._get_merged_tags(user.get("tags", []), [active_group])

    def _get_merged_tags(self, own_tags: list[str], groups: list[str]) -> list[str]:
        """``own_tags`` followed by the tags of each group in ``groups``, without repeats."""
        merged = list(own_tags)
        for code in groups:
            group_record = self.get_group(code)
            if group_record is None:
                continue
            for tag in group_record.get("tags", []):
                if tag not in merged:
                    merged.append(tag)
        return merged

    def verify_password(self, identity: str, password: str) -> dict[str, Any] | None:
        """The user record when ``password`` matches a confirmed, non-system user.

        None on any failure: unknown user, status that does not authenticate,
        system user, no password, wrong password.
        """
        user = self.get_user(identity)
        if user is None or user.get("status") != AUTHENTICATING_STATUS:
            return None
        password_hash = user.get("password_hash")
        if user.get("system", False) or password_hash is None:
            return None
        if self.check_password(password, password_hash):
            return user
        return None

    def hash_password(self, password: str) -> str:
        """Hash ``password`` with scrypt: a fresh salt, params embedded in the string."""
        salt = os.urandom(SALT_BYTES)
        derived = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
        salt_b64 = base64.b64encode(salt).decode()
        hash_b64 = base64.b64encode(derived).decode()
        return f"scrypt$n={SCRYPT_N},r={SCRYPT_R},p={SCRYPT_P}${salt_b64}${hash_b64}"

    def check_password(self, password: str, stored: str) -> bool:
        """Constant-time check of ``password`` against a stored scrypt hash string.

        A malformed hash string yields False.
        """
        try:
            scheme, params, salt_b64, hash_b64 = stored.split("$")
            cost = dict(pair.split("=") for pair in params.split(","))
            n, r, p = int(cost["n"]), int(cost["r"]), int(cost["p"])
        except (KeyError, ValueError):
            return False
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=n,
            r=r,
            p=p,
        )
        return hmac.compare_digest(derived, base64.b64decode(hash_b64))

    # ── tokens ─────────────────────────────────────────────────────────

    def issue_token(
        self,
        user: str,
        tags: list[str] | str,
        expires_at: float | None = None,
    ) -> str:
        """Mint a token of ``user`` and return the full ``gak_...`` key, shown once.

        ``tags`` is a non-empty list or ``"*"``. Raises ``ValueError`` for
        anything else, before any record is written.
        """
        if tags != ALL_TAGS and (isinstance(tags, str) or not tags):
            raise ValueError(f"a token needs a non-empty list of tags or {ALL_TAGS!r}")
        key_id = secrets.token_hex(KEY_ID_BYTES)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        self.save_token(
            {
                "key_id": key_id,
                "user": user,
                "tags": ALL_TAGS if tags == ALL_TAGS else list(tags),
                "expires_at": expires_at,
                "enabled": True,
                "secret_hash": self._hash_secret(secret),
            }
        )
        return f"{TOKEN_PREFIX}{key_id}_{secret}"

    def revoke_token(self, key_id: str) -> bool:
        """Disable ``key_id``. True if a record was found and disabled, False if absent."""
        record = self.get_token(key_id)
        if record is None:
            return False
        record["enabled"] = False
        self.save_token(record)
        return True

    def verify_token(self, key: str) -> dict[str, Any] | None:
        """``{"identity", "group", "tags", "expires_at"}`` for a valid ``key``, else None.

        None on any failure: malformed key, unknown id, disabled, expired,
        secret mismatch (constant-time), user absent or not confirmed.
        """
        if not key.startswith(TOKEN_PREFIX):
            return None
        key_id, sep, secret = key[len(TOKEN_PREFIX) :].partition("_")
        if not sep or not key_id or not secret:
            return None
        record = self.get_token(key_id)
        if record is None or not record.get("enabled", False):
            return None
        expires_at = record.get("expires_at")
        if expires_at is not None and expires_at <= time.time():
            return None
        if not hmac.compare_digest(self._hash_secret(secret), record.get("secret_hash", "")):
            return None
        user = self.get_user(record["user"])
        if user is None or user.get("status") != AUTHENTICATING_STATUS:
            return None
        if record["tags"] == ALL_TAGS:
            tags = self.get_user_tags(user["identity"])
        else:
            held = self._get_merged_tags(user.get("tags", []), user.get("groups", []))
            tags = [tag for tag in record["tags"] if tag in held]
        return {
            "identity": user["identity"],
            "group": user.get("primary_group"),
            "tags": tags,
            "expires_at": expires_at,
        }

    def _hash_secret(self, secret: str) -> str:
        """sha256 hex of a token secret (high entropy: a fast hash is enough)."""
        return hashlib.sha256(secret.encode()).hexdigest()


class FileAuthStore(AuthStore):
    """One JSON file per record over a storage mount, written encrypted at rest.

    It holds the shared ``StorageManager`` and never raw paths. Records live
    at ``<mount>:<prefix>/<kind>/<key>.json`` for users, groups, tags, tokens
    and login failures (see module docstring).
    """

    __slots__ = ("_storage", "_mount", "_prefix")

    def __init__(
        self,
        storage: StorageManager,
        mount: str = "site",
        prefix: str = "auth",
    ) -> None:
        """Bind the store to a storage ``mount``/``prefix`` (default ``site:auth``)."""
        self._storage = storage
        self._mount = mount
        self._prefix = prefix

    @property
    def storage(self) -> StorageManager:
        """The storage manager the records are written through."""
        return self._storage

    @property
    def mount(self) -> str:
        """The storage mount the records live on."""
        return self._mount

    @property
    def prefix(self) -> str:
        """The directory under the mount that holds the five record kinds."""
        return self._prefix

    # ── persistence helpers ────────────────────────────────────────────

    def _dir_node(self, kind: str) -> StorageNode:
        """The ``<mount>:<prefix>/<kind>`` directory node."""
        return self.storage.node(f"{self.mount}:{self.prefix}/{kind}")

    def _record_node(self, kind: str, key: str) -> StorageNode:
        """The node of one record: its key percent-encoded, so it stays inside the kind."""
        filename = urllib.parse.quote(key, safe="")
        return self.storage.node(f"{self.mount}:{self.prefix}/{kind}/{filename}.json")

    def _read_all(self, kind: str) -> list[dict[str, Any]]:
        """Every ``*.json`` record of ``kind``."""
        directory = self._dir_node(kind)
        if not directory.is_dir():
            return []
        return [
            json.loads(child.read_text()) for child in directory.children() if child.ext == "json"
        ]

    def _read(self, kind: str, key: str) -> dict[str, Any] | None:
        """One record of ``kind``, or None if its file does not exist."""
        node = self._record_node(kind, key)
        if not node.exists():
            return None
        result: dict[str, Any] = json.loads(node.read_text())
        return result

    def _write(self, kind: str, key: str, record: dict[str, Any]) -> None:
        """Write one record encrypted at rest."""
        self._record_node(kind, key).write_text(json.dumps(record, indent=2), encrypted=True)

    def _delete(self, kind: str, key: str) -> bool:
        """Remove one record's file. True if it existed."""
        node = self._record_node(kind, key)
        if not node.exists():
            return False
        node.delete()
        return True

    # ── AuthStore persistence ──────────────────────────────────────────

    @property
    def user_list(self) -> list[dict[str, Any]]:
        """Every user record."""
        return self._read_all("users")

    @property
    def group_list(self) -> list[dict[str, Any]]:
        """Every group record."""
        return self._read_all("groups")

    @property
    def tag_list(self) -> list[dict[str, Any]]:
        """Every tag record."""
        return self._read_all("tags")

    @property
    def token_list(self) -> list[dict[str, Any]]:
        """Every token record."""
        return self._read_all("tokens")

    def get_login_failures(self, identity: str) -> dict[str, Any] | None:
        """The login failure record for ``identity``, or None."""
        return self._read("login_failures", identity)

    def save_login_failures(self, record: dict[str, Any]) -> None:
        """Persist login failures, keyed by ``identity``."""
        self._write("login_failures", record["identity"], record)

    def delete_login_failures(self, identity: str) -> bool:
        """Remove login failures. True if removed, False if absent."""
        return self._delete("login_failures", identity)

    def get_user(self, identity: str) -> dict[str, Any] | None:
        """The user record for ``identity``, or None."""
        return self._read("users", identity)

    def get_group(self, code: str) -> dict[str, Any] | None:
        """The group record for ``code``, or None."""
        return self._read("groups", code)

    def get_tag(self, code: str) -> dict[str, Any] | None:
        """The tag record for ``code``, or None."""
        return self._read("tags", code)

    def get_token(self, key_id: str) -> dict[str, Any] | None:
        """The token record for ``key_id``, or None."""
        return self._read("tokens", key_id)

    def write_user(self, record: dict[str, Any]) -> None:
        """Persist a checked user record, keyed by ``identity``."""
        self._write("users", record["identity"], record)

    def save_group(self, record: dict[str, Any]) -> None:
        """Persist a group record, keyed by ``code``."""
        self._write("groups", record["code"], record)

    def save_tag(self, record: dict[str, Any]) -> None:
        """Persist a tag record, keyed by ``code``."""
        self._write("tags", record["code"], record)

    def save_token(self, record: dict[str, Any]) -> None:
        """Persist a token record, keyed by ``key_id``."""
        self._write("tokens", record["key_id"], record)

    def delete_user(self, identity: str) -> bool:
        """Remove a user, every token whose ``user`` is ``identity`` and its login failures.

        True if a user record was removed, False if absent.
        """
        for token in self.token_list:
            if token.get("user") == identity:
                self._delete("tokens", token["key_id"])
        self._delete("login_failures", identity)
        return self._delete("users", identity)

    def delete_group(self, code: str) -> bool:
        """Remove a group. True if a record was removed, False if absent.

        Raises ``ValueError`` while a user has the group among its ``groups``.
        """
        members = [user["identity"] for user in self.user_list if code in user.get("groups", [])]
        if members:
            raise ValueError(f"group {code!r} has members: {', '.join(members)}")
        return self._delete("groups", code)

    def delete_tag(self, code: str) -> bool:
        """Remove a tag. True if a record was removed, False if absent."""
        return self._delete("tags", code)

    def delete_token(self, key_id: str) -> bool:
        """Remove a token. True if a record was removed, False if absent."""
        return self._delete("tokens", key_id)
