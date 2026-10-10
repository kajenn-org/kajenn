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

"""The ``_server/users`` section: SUPERADMIN-gated user management.

``UsersSection`` is a ``RoutingClass`` the ``ServerApplication`` attaches under
``users`` (endpoints at ``/_server/users/...``). Every route is gated
``auth_rule="SUPERADMIN"`` — the section is ALWAYS declared (fixed structure,
D26), and each handler answers the ``{"error": ...}`` shape (coherent with
``login``) when the server has no ``user_store`` wired.

The credential invariant: ``password_hash`` NEVER crosses the wire. ``list``
and ``get`` strip it from the record; ``save`` never accepts it (it merges the
non-credential fields of the body over the stored record, preserving the hash);
a password enters the system only as plaintext through ``create_user`` and
``set_password``, hashed server-side via ``UserStore.hash_password``.

Route responsibilities are separated:

- ``create_user`` — births a NEW record (error if the identity exists): the
  body carries ``password``/``password_confirm`` (the server checks they match)
  plus the metadata and ``tags``;
- ``save`` — updates an EXISTING record only (error if absent): merges the
  body's metadata/tags over the stored record, ``password_hash`` untouched. The
  body is taken whole (``body_data``) so tomorrow's metadata fields need no
  signature change;
- ``set_password`` — changes the credential of an existing record only, with the
  same ``password``/``password_confirm`` server-side check;
- ``delete`` — removes a record.

Parent (dual relationship): the ServerApplication, stored as
``self.application``; the store is reached via ``self.application.server.user_store``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from kajenn.auth.user_store import UserStore
    from ..server_app import ServerApplication

__all__ = ["UsersSection"]

NO_STORE_ERROR = {"error": "User management is not available"}


class UsersSection(RoutingClass):
    """The ``_server/users`` mount: SUPERADMIN CRUD over the server's UserStore.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``. The store is ``self.application.server.user_store``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application

    @property
    def user_store(self) -> UserStore | None:
        """The server's UserStore, or ``None`` when identity is unconfigured."""
        return getattr(self.application.server, "user_store", None)

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """A record without its ``password_hash`` — the wire-safe projection."""
        return {k: v for k, v in record.items() if k != "password_hash"}

    def matched_password(self, body: dict[str, Any]) -> str | None:
        """The password when it is present and matches its confirmation, else None.

        The caller distinguishes a mismatch (this returns None) from an absent
        password by checking the body itself: only ``create_user`` requires one.
        """
        password = body.get("password")
        if password is None or password != body.get("password_confirm"):
            return None
        return password

    @route(auth_rule="SUPERADMIN")
    def list(self) -> dict[str, Any]:
        """Every user record, ``password_hash`` stripped."""
        store = self.user_store
        if store is None:
            return NO_STORE_ERROR
        return {"users": [self.public_record(r) for r in store.load_all()]}

    @route(auth_rule="SUPERADMIN")
    def get(self, identity: str = "") -> dict[str, Any]:
        """One user record, ``password_hash`` stripped."""
        store = self.user_store
        if store is None:
            return NO_STORE_ERROR
        record = store.get(identity)
        if record is None:
            return {"error": f"No such user: {identity}"}
        return self.public_record(record)

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def create_user(self, identity: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Create a NEW user from the body (metadata + tags + password/confirm).

        Errors if the identity already exists. The password is validated against
        its confirmation and hashed server-side; ``password_hash`` never arrives
        pre-formed. Metadata and ``tags`` from the body land on the new record.
        A new record defaults to ``enabled: True`` and ``tags: []`` (creating a
        user with a password means letting them log in — the body can still say
        ``enabled: false`` to create it disabled); ``verify`` requires
        ``enabled`` and the login ``Avatar`` requires ``tags``, so a minimal
        body must still produce a working user.
        """
        store = self.user_store
        if store is None:
            return NO_STORE_ERROR
        if not identity:
            return {"error": "Identity is required"}
        if store.get(identity) is not None:
            return {"error": f"User already exists: {identity}"}
        body = body_data or {}
        password = self.matched_password(body)
        if password is None:
            return {"error": "Password and confirmation are required and must match"}
        record = self.metadata_of(body)
        record.setdefault("enabled", True)
        record.setdefault("tags", [])
        record["identity"] = identity
        record["password_hash"] = store.hash_password(password)
        store.save(record)
        return self.public_record(record)

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def save(self, identity: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Update an EXISTING record's metadata/tags; ``password_hash`` untouched.

        Errors if the user does not exist (creation is ``create_user``'s job).
        The body is merged whole over the stored record — new metadata fields
        persist with no signature change — but the credential never moves here.
        """
        store = self.user_store
        if store is None:
            return NO_STORE_ERROR
        record = store.get(identity)
        if record is None:
            return {"error": f"No such user: {identity}"}
        record.update(self.metadata_of(body_data or {}))
        store.save(record)
        return self.public_record(record)

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def set_password(self, identity: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Change an existing user's password (plaintext in, hashed server-side).

        The body carries ``password``/``password_confirm``; the server checks
        they match. Errors if the user does not exist.
        """
        store = self.user_store
        if store is None:
            return NO_STORE_ERROR
        record = store.get(identity)
        if record is None:
            return {"error": f"No such user: {identity}"}
        password = self.matched_password(body_data or {})
        if password is None:
            return {"error": "Password and confirmation are required and must match"}
        record["password_hash"] = store.hash_password(password)
        store.save(record)
        return {"identity": identity, "updated": True}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def delete(self, identity: str = "") -> dict[str, Any]:
        """Remove a user; reports whether a record was actually removed."""
        store = self.user_store
        if store is None:
            return NO_STORE_ERROR
        return {"identity": identity, "deleted": store.delete(identity)}

    def metadata_of(self, body: dict[str, Any]) -> dict[str, Any]:
        """The non-credential, non-key fields of a body (never password/hash/identity).

        Credential and key fields are owned by their dedicated routes, so they
        are dropped here: whatever else the body carries is user metadata.
        """
        dropped = {"password", "password_confirm", "password_hash", "identity"}
        return {k: v for k, v in body.items() if k not in dropped}
