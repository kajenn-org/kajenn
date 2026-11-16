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

"""The ``_auth/users`` section: user management over the application's ``AuthStore``.

Every route asks ``auth_rule="AUTH_ADMIN"``. Routes, under ``/_auth/users/``:

- ``list`` — ``{"users": [record, ...]}``;
- ``get(identity)`` — one record, or ``{"error": ...}`` when absent;
- ``create_user(identity, body)`` — a new record; the body carries any field
  of the user record and, optionally, ``password``/``password_confirm``
  (when given they must match). Without a password the record has no
  ``password_hash`` and the user does not enter with a password. Defaults: ``groups`` ``[]``, ``primary_group``
  ``None``, ``tags`` ``[]``, ``status`` ``"confirmed"``, ``system`` ``False``,
  ``email`` ``None``, ``external_ids`` ``[]``. An existing identity is an error;
- ``save(identity, body)`` — merges the body's fields over an existing record;
  the password hash is untouched. An absent identity is an error;
- ``set_password(identity, body)`` — sets or replaces the password of an
  existing record; ``password``/``password_confirm`` are required and must
  match;
- ``delete(identity)`` — ``{"identity", "deleted"}``.

``password_hash`` never crosses the wire: every answer strips it, and a body
never sets it. A password enters only as plaintext through ``create_user`` and
``set_password`` and is hashed by ``AuthStore.hash_password``.

Errors answer ``{"error": message}`` with status 200. ``create_user`` and
``save`` refuse a ``tags`` field naming a tag that has no record, and nothing is
written. A record the store
refuses (``ValueError`` from ``save_user``: an unknown ``status``, a
``primary_group`` outside ``groups``) is such an error, and nothing is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from ..auth_app import AuthApplicationBase
    from ..auth_store import AuthStore

__all__ = ["UsersSection"]

ADMIN_TAG = "AUTH_ADMIN"
CREDENTIAL_FIELDS = ("password", "password_confirm", "password_hash")
NEW_USER_DEFAULTS: dict[str, Any] = {
    "groups": [],
    "primary_group": None,
    "tags": [],
    "status": "confirmed",
    "system": False,
    "email": None,
    "external_ids": [],
}


class UsersSection(RoutingClass):
    """The ``_auth/users`` mount: AUTH_ADMIN management of user records.

    Note:
        Parent (dual relationship): the AuthApplicationBase, stored as
        ``self.application``. The store is ``self.application.store``.
    """

    def __init__(self, application: AuthApplicationBase) -> None:
        """Bind the section to its application (dual relationship)."""
        self.application = application

    @property
    def store(self) -> AuthStore:
        """The application's AuthStore."""
        return self.application.store

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """A record without its ``password_hash``."""
        return {k: v for k, v in record.items() if k != "password_hash"}

    def matched_password(self, body: dict[str, Any]) -> str | None:
        """The password when present and equal to its confirmation, else None."""
        password: str | None = body.get("password")
        if password is None or password != body.get("password_confirm"):
            return None
        return password

    def metadata_of(self, body: dict[str, Any]) -> dict[str, Any]:
        """The fields of a body other than the credential fields and ``identity``."""
        dropped = {*CREDENTIAL_FIELDS, "identity"}
        return {k: v for k, v in body.items() if k not in dropped}

    def save_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Save ``record``; the public record, or the error shape when the store refuses it."""
        try:
            self.store.save_user(record)
        except ValueError as refused:
            return {"error": str(refused)}
        return self.public_record(record)

    @route(name="list", auth_rule=ADMIN_TAG)
    def user_list(self) -> dict[str, Any]:
        """Every user record, ``password_hash`` stripped."""
        return {"users": [self.public_record(r) for r in self.store.user_list]}

    @route(auth_rule=ADMIN_TAG)
    def get(self, identity: str = "") -> dict[str, Any]:
        """One user record, ``password_hash`` stripped."""
        record = self.store.get_user(identity)
        if record is None:
            return {"error": f"No such user: {identity}"}
        return self.public_record(record)

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def create_user(self, identity: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Create a new user from the body (fields + optional password/confirm).

        Without a password the record carries no ``password_hash``: the user
        does not enter with a password until ``set_password`` gives it one.
        """
        if not identity:
            return {"error": "Identity is required"}
        if self.store.get_user(identity) is not None:
            return {"error": f"User already exists: {identity}"}
        body = body_data or {}
        refusal = self.application.get_tags_refusal(body)
        if refusal:
            return refusal
        record = {**NEW_USER_DEFAULTS, **self.metadata_of(body)}
        record["identity"] = identity
        record["password_hash"] = None
        if "password" in body or "password_confirm" in body:
            password = self.matched_password(body)
            if password is None:
                return {"error": "Password and confirmation must match"}
            record["password_hash"] = self.store.hash_password(password)
        return self.save_record(record)

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def save(self, identity: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Merge the body's fields over an existing record; ``password_hash`` untouched."""
        record = self.store.get_user(identity)
        if record is None:
            return {"error": f"No such user: {identity}"}
        refusal = self.application.get_tags_refusal(body_data or {})
        if refusal:
            return refusal
        record.update(self.metadata_of(body_data or {}))
        return self.save_record(record)

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def set_password(self, identity: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Replace an existing user's password (plaintext in, hashed by the store)."""
        record = self.store.get_user(identity)
        if record is None:
            return {"error": f"No such user: {identity}"}
        password = self.matched_password(body_data or {})
        if password is None:
            return {"error": "Password and confirmation are required and must match"}
        record["password_hash"] = self.store.hash_password(password)
        answer = self.save_record(record)
        if "error" in answer:
            return answer
        return {"identity": identity, "updated": True}

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def delete(self, identity: str = "") -> dict[str, Any]:
        """Remove a user; reports whether a record was removed."""
        return {"identity": identity, "deleted": self.store.delete_user(identity)}
