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

"""The ``_auth/tags`` section: tag management over the application's ``AuthStore``.

Every route asks ``auth_rule="AUTH_ADMIN"``. Routes, under ``/_auth/tags/``:

- ``list`` — ``{"tags": [record, ...]}``;
- ``get(code)`` — one record, or ``{"error": ...}`` when absent;
- ``create_tag(code, body)`` — a new record from the body's fields. Defaults:
  ``description`` ``""``, ``owner`` ``""``, ``reserved`` ``False``, ``parent``
  ``None``. An existing code is an error;
- ``save(code, body)`` — merges the body's fields over an existing record.
  An absent code is an error;
- ``delete(code)`` — ``{"code", "deleted"}``.

Errors answer ``{"error": message}`` with status 200.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from ..auth_app import AuthApplicationBase
    from ..auth_store import AuthStore

__all__ = ["TagsSection"]

ADMIN_TAG = "AUTH_ADMIN"
NEW_TAG_DEFAULTS: dict[str, Any] = {
    "description": "",
    "owner": "",
    "reserved": False,
    "parent": None,
}


class TagsSection(RoutingClass):
    """The ``_auth/tags`` mount: AUTH_ADMIN management of tag records.

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
    def tag_list(self) -> dict[str, Any]:
        """Every tag record."""
        return {"tags": self.store.tag_list}

    @route(auth_rule=ADMIN_TAG)
    def get(self, code: str = "") -> dict[str, Any]:
        """One tag record."""
        record = self.store.get_tag(code)
        if record is None:
            return {"error": f"No such tag: {code}"}
        return record

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def create_tag(self, code: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Create a new tag from the body's fields."""
        if not code:
            return {"error": "Code is required"}
        if self.store.get_tag(code) is not None:
            return {"error": f"Tag already exists: {code}"}
        record = {**NEW_TAG_DEFAULTS, **self.fields_of(body_data or {}), "code": code}
        self.store.save_tag(record)
        return record

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def save(self, code: str = "", body_data: dict | None = None) -> dict[str, Any]:
        """Merge the body's fields over an existing tag record."""
        record = self.store.get_tag(code)
        if record is None:
            return {"error": f"No such tag: {code}"}
        record.update(self.fields_of(body_data or {}))
        self.store.save_tag(record)
        return record

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def delete(self, code: str = "") -> dict[str, Any]:
        """Remove a tag; reports whether a record was removed."""
        return {"code": code, "deleted": self.store.delete_tag(code)}
