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

"""UserStore — the server's local identity source (contract + file backend).

The ``UserStore`` is the server's in-house equivalent of an external identity
provider (LDAP, OIDC): it holds users with a password and a set of tags and
verifies credentials. Its tags are the verified claims of the local source;
they ride in the authentication result as trusted input (the receiving app
stays sovereign on what it keeps — the two-phase model).

Contract (clients depend on this, never on files or SQL — a future
``DbUserStore`` swaps behind it)::

    UserStore:
        load_all() -> list[dict]                    # every record
        get(identity) -> dict | None                # one record or None
        save(record) -> None                        # create or update
        delete(identity) -> bool                    # True if a record was removed
        verify(identity, password) -> dict | None   # full record on success, None otherwise

``FileUserStore`` is the filesystem backend: one JSON file per user at
``<mount>:<prefix>/<userkey>.json`` over genro-storage nodes, defaulting to
``site:users``. Every record is written ``encrypted=True``: credentials are
ciphertext at rest, and without installed key material the write hard-fails
(D5 — no plain-text fallback). All I/O is synchronous (core 1b ratified: async
callers wrap in ``server.run_sync()``).

The record::

    {
      "identity": "admin",
      "password_hash": "scrypt$n=16384,r=8,p=1$<salt-b64>$<hash-b64>",
      "tags": ["SUPERADMIN"],
      "enabled": true,
      "failed_attempts": 0,
      "last_failed_at": 0.0
    }

``failed_attempts``/``last_failed_at`` are OPTIONAL (absent until the first
failure): the store-backed login-lockout counter the ``_server`` login route
maintains — incremented on failure, reset on success (``applications/server_app.py``).

Passwords are hashed with ``hashlib.scrypt`` (stdlib, zero new deps): a random
per-user salt and the cost parameters are embedded in the hash string, so a
future parameter upgrade needs no migration. Comparison is constant-time
(``hmac.compare_digest``). Plain-text passwords never persist anywhere.

``verify`` returns the full record on success and None on ANY failure (unknown
user, disabled account, wrong password) — a disabled user never authenticates.
``identity`` is the immutable record key (a rename is delete + create).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.parse
from typing import Any

from genro_storage import StorageManager, StorageNode

__all__ = ["UserStore", "FileUserStore"]

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16


class UserStore:
    """Contract for a local identity store (see module docstring).

    Subclasses implement persistence; the password hashing and verification
    logic lives here so every backend hashes identically. Clients depend on this
    contract only, never on files or SQL.
    """

    __slots__ = ()

    def load_all(self) -> list[dict[str, Any]]:
        """Return every stored record."""
        raise NotImplementedError

    def get(self, identity: str) -> dict[str, Any] | None:
        """Return the record for ``identity`` or None if there is no such user."""
        raise NotImplementedError

    def save(self, record: dict[str, Any]) -> None:
        """Persist ``record`` (create or update)."""
        raise NotImplementedError

    def delete(self, identity: str) -> bool:
        """Remove ``identity``. True if a record was removed, False if absent."""
        raise NotImplementedError

    def verify(self, identity: str, password: str) -> dict[str, Any] | None:
        """Return the full record when the password matches an enabled user.

        Returns None on ANY failure (unknown user, disabled, wrong password),
        with a constant-time comparison against the stored hash.
        """
        record = self.get(identity)
        if record is None or not record.get("enabled", False):
            return None
        if self.check_password(password, record.get("password_hash", "")):
            return record
        return None

    def hash_password(self, password: str) -> str:
        """Hash ``password`` with scrypt: a fresh salt, params embedded in the string."""
        salt = os.urandom(SALT_BYTES)
        derived = hashlib.scrypt(
            password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
        )
        salt_b64 = base64.b64encode(salt).decode()
        hash_b64 = base64.b64encode(derived).decode()
        return f"scrypt$n={SCRYPT_N},r={SCRYPT_R},p={SCRYPT_P}${salt_b64}${hash_b64}"

    def check_password(self, password: str, stored: str) -> bool:
        """Constant-time check of ``password`` against a stored scrypt hash string.

        Parses the salt and cost parameters out of ``stored`` (so old hashes stay
        verifiable after a parameter upgrade). A malformed hash string yields False.
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


class FileUserStore(UserStore):
    """One JSON file per user over a storage mount, written encrypted at rest.

    It holds the shared ``StorageManager`` (dual relationship) and never raw
    paths. Records live at
    ``<mount>:<prefix>/<userkey>.json`` — the userkey is the identity
    percent-encoded by ``user_to_userkey``, so any identity names a file inside
    the prefix — and are always written ``encrypted=True`` — the write site
    declares it, not the mount.
    """

    __slots__ = ("_storage", "_mount", "_prefix")

    def __init__(
        self,
        storage: StorageManager,
        mount: str = "site",
        prefix: str = "users",
    ) -> None:
        """Bind the store to a storage ``mount``/``prefix`` (default ``site:users``)."""
        self._storage = storage
        self._mount = mount
        self._prefix = prefix

    # ── persistence helpers ────────────────────────────────────────────

    def _dir_node(self) -> StorageNode:
        """The ``<mount>:<prefix>`` directory node."""
        return self._storage.node(f"{self._mount}:{self._prefix}")

    def user_to_userkey(self, user: str) -> str:
        """The filename ``user``'s record goes by: its identity, percent-encoded.

        ONE WAY: readers compute the key forward from the identity, nothing
        derives an identity back from a filename (``load_all`` reads it from
        the record body). ``quote`` with nothing declared safe keeps every
        separator out of the name, so no identity escapes the prefix.
        """
        return urllib.parse.quote(user, safe="")

    def _record_node(self, identity: str) -> StorageNode:
        """The node for one user's JSON file on the configured mount."""
        key = self.user_to_userkey(identity)
        return self._storage.node(f"{self._mount}:{self._prefix}/{key}.json")

    # ── UserStore contract ─────────────────────────────────────────────

    def load_all(self) -> list[dict[str, Any]]:
        """Read every ``*.json`` record under ``<mount>:<prefix>/``."""
        directory = self._dir_node()
        if not directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for child in directory.children():
            if child.ext == "json":
                records.append(json.loads(child.read_text()))
        return records

    def get(self, identity: str) -> dict[str, Any] | None:
        """Read one user's record, or None if the file does not exist."""
        node = self._record_node(identity)
        if not node.exists():
            return None
        result: dict[str, Any] = json.loads(node.read_text())
        return result

    def save(self, record: dict[str, Any]) -> None:
        """Persist ``record`` encrypted at rest through its storage node.

        The record's ``identity`` is its file key: one file per user, written via
        the node's public ``write_text`` with ``encrypted=True`` — credentials
        never land in plain text.
        """
        node = self._record_node(record["identity"])
        node.write_text(json.dumps(record, indent=2), encrypted=True)

    def delete(self, identity: str) -> bool:
        """Remove one user's file. True if it existed, False otherwise."""
        node = self._record_node(identity)
        if not node.exists():
            return False
        node.delete()
        return True
