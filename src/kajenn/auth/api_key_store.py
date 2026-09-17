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

"""ApiKeyStore — the server's revocable bearer-credential registry (contract + file backend).

An API key is its own identity (label + tags), registered at issue time and
revocable at any moment: unlike a JWT (stateless, valid until it expires), a
key is checked against this registry on every request, so disabling the
record kills the credential instantly.

Contract (clients depend on this, never on files or SQL — a future
``DbApiKeyStore`` swaps behind it)::

    ApiKeyStore:
        load_all() -> list[dict]                       # every record
        get(key_id) -> dict | None                     # one record or None
        save(record) -> None                            # create or update
        delete(key_id) -> bool                          # True if a record was removed
        issue(label, tags, expires_at=None) -> str      # mint a key, shown ONCE
        revoke(key_id) -> bool                          # True if a record was disabled
        verify(key) -> dict | None                      # record if enabled+unexpired+match

The key: ``gak_<key_id>_<secret>`` — recognizable prefix, embedded record id
(hex, so the first ``_`` after it splits unambiguously) for O(1) lookup, then
a high-entropy secret. The registry stores ONLY the sha256 hash of the secret:
a 256-bit random secret is not guessable, so a fast hash suffices (scrypt is
for low-entropy passwords, see ``UserStore``). The full key exists in clear
only once, as ``issue``'s return value.

``FileApiKeyStore`` is the filesystem backend: one JSON file per key at
``<mount>:<prefix>/<key_id>.json`` over genro-storage nodes, defaulting to
``site:api_keys``. Every record is written ``encrypted=True``: credentials are
ciphertext at rest, and without installed key material the write hard-fails
(D5 — no plain-text fallback). All I/O is synchronous (core 1b ratified: async
callers wrap in ``server.run_sync()``).

The record::

    {
      "key_id": "3f9c2a1b7d4e6a05",
      "label": "ci-deploy",
      "tags": ["deploy"],
      "enabled": true,
      "expires_at": null,
      "secret_hash": "<sha256 hex>"
    }

``expires_at`` is a POSIX timestamp (``time.time()`` convention, matching
``Session``) or ``None`` for a key that never expires. Revoke sets
``enabled: false`` (the record stays: the audit row remains listed); delete
removes the file. ``verify`` returns None on ANY failure — malformed key,
unknown id, disabled record, expired, or a secret hash mismatch (constant-time
comparison).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from genro_storage import StorageManager, StorageNode

__all__ = ["ApiKeyStore", "FileApiKeyStore"]

API_KEY_PREFIX = "gak_"
KEY_ID_BYTES = 8
SECRET_BYTES = 32


class ApiKeyStore:
    """Contract for the API key registry (see module docstring).

    Subclasses implement persistence; key generation, hashing and
    verification live here so every backend behaves identically.
    """

    __slots__ = ()

    def load_all(self) -> list[dict[str, Any]]:
        """Return every stored record."""
        raise NotImplementedError

    def get(self, key_id: str) -> dict[str, Any] | None:
        """Return the record for ``key_id`` or None if there is no such key."""
        raise NotImplementedError

    def save(self, record: dict[str, Any]) -> None:
        """Persist ``record`` (create or update)."""
        raise NotImplementedError

    def delete(self, key_id: str) -> bool:
        """Remove ``key_id``. True if a record was removed, False if absent."""
        raise NotImplementedError

    def issue(
        self,
        label: str,
        tags: list[str],
        expires_at: float | None = None,
    ) -> str:
        """Mint a key: generate, hash, persist, return the full ``gak_...`` key.

        The returned key is the ONLY time the secret exists in clear — the
        record carries just its hash. ``expires_at`` is a POSIX timestamp;
        None means the key never expires.
        """
        key_id = secrets.token_hex(KEY_ID_BYTES)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        record = {
            "key_id": key_id,
            "label": label,
            "tags": list(tags),
            "enabled": True,
            "expires_at": expires_at,
            "secret_hash": self._hash_secret(secret),
        }
        self.save(record)
        return f"{API_KEY_PREFIX}{key_id}_{secret}"

    def revoke(self, key_id: str) -> bool:
        """Disable ``key_id``. True if a record was found and disabled, False if absent."""
        record = self.get(key_id)
        if record is None:
            return False
        record["enabled"] = False
        self.save(record)
        return True

    def verify(self, key: str) -> dict[str, Any] | None:
        """Return the record when ``key`` matches an enabled, unexpired one.

        None on ANY failure: malformed key, unknown id, disabled record,
        expired, or a secret hash mismatch (constant-time comparison).
        """
        parsed = self._parse(key)
        if parsed is None:
            return None
        key_id, secret = parsed
        record = self.get(key_id)
        if record is None or not record.get("enabled", False):
            return None
        expires_at = record.get("expires_at")
        if expires_at is not None and expires_at <= time.time():
            return None
        if hmac.compare_digest(self._hash_secret(secret), record.get("secret_hash", "")):
            return record
        return None

    def _parse(self, key: str) -> tuple[str, str] | None:
        """Split a ``gak_<key_id>_<secret>`` key, or None if malformed."""
        if not key.startswith(API_KEY_PREFIX):
            return None
        body = key[len(API_KEY_PREFIX) :]
        key_id, sep, secret = body.partition("_")
        if not sep or not key_id or not secret:
            return None
        return key_id, secret

    def _hash_secret(self, secret: str) -> str:
        """sha256 hex of the secret (high-entropy: a fast hash is enough)."""
        return hashlib.sha256(secret.encode()).hexdigest()


class FileApiKeyStore(ApiKeyStore):
    """One JSON file per key over a storage mount, written encrypted at rest.

    Like ``FileUserStore`` it holds the shared ``StorageManager`` (dual
    relationship) and never raw paths. Records live at
    ``<mount>:<prefix>/<key_id>.json`` and are always written
    ``encrypted=True`` — the write site declares it, not the mount.
    """

    __slots__ = ("_storage", "_mount", "_prefix")

    def __init__(
        self,
        storage: StorageManager,
        mount: str = "site",
        prefix: str = "api_keys",
    ) -> None:
        """Bind the store to a storage ``mount``/``prefix`` (default ``site:api_keys``)."""
        self._storage = storage
        self._mount = mount
        self._prefix = prefix

    # ── persistence helpers ────────────────────────────────────────────

    def _dir_node(self) -> StorageNode:
        """The ``<mount>:<prefix>`` directory node."""
        return self._storage.node(f"{self._mount}:{self._prefix}")

    def _record_node(self, key_id: str) -> StorageNode:
        """The node for one key's JSON file on the configured mount."""
        return self._storage.node(f"{self._mount}:{self._prefix}/{key_id}.json")

    # ── ApiKeyStore contract ────────────────────────────────────────────

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

    def get(self, key_id: str) -> dict[str, Any] | None:
        """Read one key's record, or None if the file does not exist."""
        node = self._record_node(key_id)
        if not node.exists():
            return None
        result: dict[str, Any] = json.loads(node.read_text())
        return result

    def save(self, record: dict[str, Any]) -> None:
        """Persist ``record`` encrypted at rest through its storage node.

        The record's ``key_id`` is its file key: one file per key, written via
        the node's public ``write_text`` with ``encrypted=True`` — credentials
        never land in plain text.
        """
        node = self._record_node(record["key_id"])
        node.write_text(json.dumps(record, indent=2), encrypted=True)

    def delete(self, key_id: str) -> bool:
        """Remove one key's file. True if it existed, False otherwise."""
        node = self._record_node(key_id)
        if not node.exists():
            return False
        node.delete()
        return True
