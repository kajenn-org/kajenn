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

"""The ``_auth/groups`` section: group management over the application's ``AuthStore``.

Every route asks ``auth_rule="AUTH_ADMIN"``. Routes, under ``/_auth/groups/``:

- ``list`` — ``{"groups": [record, ...]}``;
- ``get(code)`` — one record, or ``{"error": ...}`` when absent;
- ``create_group(code, body)`` — a new record from the body's fields;
  ``tags`` defaults to ``[]``. An existing code is an error;
- ``save(code, body)`` — merges the body's fields over an existing record.
  An absent code is an error;
- ``delete(code)`` — ``{"code", "deleted"}``.

Errors answer ``{"error": message}`` with status 200. ``create_group`` and
``save`` refuse a ``tags`` field naming a tag that has no record, and nothing is
written. A group that still has
members is refused by ``delete_group`` with ``ValueError``: that refusal is
such an error, and the record stays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from ..auth_app import AuthApplicationBase
    from ..auth_store import AuthStore

__all__ = ["GroupsSection"]

ADMIN_TAG = "AUTH_ADMIN"


class GroupsSection(RoutingClass):
    """The ``_auth/groups`` mount: AUTH_ADMIN management of group records.

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

    def fields_of(self, body: dict[str, Any]) -> dict[str, Any]:
        """The fields of a body other than the key ``code``."""
        return {k: v for k, v in body.items() if k != "code"}

    @route(name="list", auth_rule=ADMIN_TAG)
    def group_list(self) -> dict[str, Any]:
        """Every group record."""
        return {"groups": self.store.group_list}

    @route(auth_rule=ADMIN_TAG)
    def get(self, code: str = "") -> dict[str, Any]:
        """One group record."""
        record = self.store.get_group(code)
        if record is None:
            return {"error": f"No such group: {code}"}
        return record

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def create_group(self, code: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Create a new group from the body's fields."""
        if not code:
            return {"error": "Code is required"}
        if self.store.get_group(code) is not None:
            return {"error": f"Group already exists: {code}"}
        refusal = self.application.get_tags_refusal(body_data or {})
        if refusal:
            return refusal
        record = {"tags": [], **self.fields_of(body_data or {}), "code": code}
        self.store.save_group(record)
        return record

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def save(self, code: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Merge the body's fields over an existing group record."""
        record = self.store.get_group(code)
        if record is None:
            return {"error": f"No such group: {code}"}
        refusal = self.application.get_tags_refusal(body_data or {})
        if refusal:
            return refusal
        record.update(self.fields_of(body_data or {}))
        self.store.save_group(record)
        return record

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def delete(self, code: str = "") -> dict[str, Any]:
        """Remove a group; the error shape while a user has it among its groups."""
        try:
            deleted = self.store.delete_group(code)
        except ValueError as refused:
            return {"error": str(refused)}
        return {"code": code, "deleted": deleted}
