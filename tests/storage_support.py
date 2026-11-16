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

"""The storage a store test runs on, and the authentication a server test needs.

``site_mounts`` is exactly the mount list ``StorageMixin`` builds when ``storage=``
is omitted, only rooted at a ``tmp_path`` instead of the deployment directory, so a
store test exercises the real production shape. ``site_storage`` builds the manager
itself, for a store exercised without a server: a server never receives one, it
builds its own from the mounts its configuration declares.

``MemoryAuthStore`` is an ``AuthStore`` over dicts, for a server whose users and
tokens a test writes itself. ``auth_application`` is the ``(class, params)``
entry that mounts ``AuthApplicationBase`` at ``/_auth`` over it, and
``add_user`` / ``issue_token`` write a confirmed user and a token of it through
the mounted application's public ``store``.
"""

from __future__ import annotations

from typing import Any

from genro_storage import StorageManager

from kajenn import AuthApplicationBase, AuthStore


def site_mounts(base_dir: object) -> list[dict[str, Any]]:
    """The single ``site:`` mount rooted at ``base_dir``, in genro-storage's own shape."""
    return [{"name": "site", "protocol": "local", "base_path": str(base_dir)}]


def site_storage(base_dir: object, storage_key: str | None = None) -> StorageManager:
    """A built ``StorageManager`` over ``site_mounts`` — for a store tested WITHOUT a server.

    A server never receives one: it builds its own from the mounts its
    configuration declares.
    """
    storage = StorageManager()
    storage.configure(site_mounts(base_dir), storage_key=storage_key)
    return storage


class MemoryAuthStore(AuthStore):
    """In-memory ``AuthStore`` backend: the shared rules over four dicts."""

    __slots__ = ("_records",)

    def __init__(self, storage: object = None) -> None:
        self._records: dict[str, dict[str, dict[str, Any]]] = {
            "users": {},
            "groups": {},
            "tags": {},
            "tokens": {},
        }

    @property
    def user_list(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records["users"].values()]

    @property
    def group_list(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records["groups"].values()]

    @property
    def tag_list(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records["tags"].values()]

    @property
    def token_list(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records["tokens"].values()]

    def get_user(self, identity: str) -> dict[str, Any] | None:
        record = self._records["users"].get(identity)
        return dict(record) if record is not None else None

    def get_group(self, code: str) -> dict[str, Any] | None:
        record = self._records["groups"].get(code)
        return dict(record) if record is not None else None

    def get_tag(self, code: str) -> dict[str, Any] | None:
        record = self._records["tags"].get(code)
        return dict(record) if record is not None else None

    def get_token(self, key_id: str) -> dict[str, Any] | None:
        record = self._records["tokens"].get(key_id)
        return dict(record) if record is not None else None

    def write_user(self, record: dict[str, Any]) -> None:
        self._records["users"][record["identity"]] = dict(record)

    def save_group(self, record: dict[str, Any]) -> None:
        self._records["groups"][record["code"]] = dict(record)

    def save_tag(self, record: dict[str, Any]) -> None:
        self._records["tags"][record["code"]] = dict(record)

    def save_token(self, record: dict[str, Any]) -> None:
        self._records["tokens"][record["key_id"]] = dict(record)

    def delete_user(self, identity: str) -> bool:
        return self._records["users"].pop(identity, None) is not None

    def delete_group(self, code: str) -> bool:
        members = [
            user["identity"]
            for user in self._records["users"].values()
            if code in user.get("groups", [])
        ]
        if members:
            raise ValueError(f"group {code!r} has members: {', '.join(members)}")
        return self._records["groups"].pop(code, None) is not None

    def delete_tag(self, code: str) -> bool:
        return self._records["tags"].pop(code, None) is not None

    def delete_token(self, key_id: str) -> bool:
        return self._records["tokens"].pop(key_id, None) is not None


def auth_application(**params: Any) -> tuple[type, dict[str, Any]]:
    """The entry mounting ``AuthApplicationBase`` at ``/_auth`` over a ``MemoryAuthStore``."""
    return (AuthApplicationBase, {"store": {"store_class": MemoryAuthStore}, **params})


def add_user(
    server: Any,
    identity: str,
    tags: list[str] | None = None,
    password: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Write a confirmed user with ``tags`` (and ``password``) in the store of ``_auth``."""
    store: AuthStore = server.applications["_auth"].store
    record: dict[str, Any] = {
        "identity": identity,
        "groups": [],
        "primary_group": None,
        "tags": list(tags or []),
        "status": "confirmed",
        "system": False,
        "password_hash": store.hash_password(password) if password is not None else None,
        "email": None,
        "external_ids": [],
    }
    record.update(fields)
    store.save_user(record)
    return record


def issue_token(server: Any, identity: str, tags: list[str] | str = "*", **kwargs: Any) -> str:
    """Issue a token of ``identity`` in the store of ``_auth`` and return the ``gak_`` key."""
    store: AuthStore = server.applications["_auth"].store
    return store.issue_token(identity, tags, **kwargs)
