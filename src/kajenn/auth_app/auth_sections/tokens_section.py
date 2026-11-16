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

"""The ``_auth/tokens`` section: ``gak_`` tokens over the application's ``AuthStore``.

Every route asks ``auth_rule="AUTH_ADMIN"``. Routes, under ``/_auth/tokens/``:

- ``list`` — ``{"tokens": [record, ...]}``;
- ``issue(body)`` — mints a token and answers ``{"key", "user"}``. The body
  carries ``user`` (required, an existing identity), ``tags`` (a non-empty
  list or ``"*"``) and ``expires_at`` (POSIX timestamp, absent for a token
  that never expires). The full ``gak_`` key is in this answer only;
- ``revoke(key_id)`` — disables the token, the record stays listed:
  ``{"key_id", "revoked"}``;
- ``delete(key_id)`` — removes the record: ``{"key_id", "deleted"}``.

``revoke`` and ``delete`` empty the credential cache of the server and of
every joined process, so the token is refused at once.

``secret_hash`` never crosses the wire. Errors answer ``{"error": message}``
with status 200; a list naming a tag that has no record is such an error
(``"*"`` names none), and so is a tag list the store refuses (``ValueError`` from
``issue_token``: empty, or neither a list nor ``"*"``). No record is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from ..auth_app import AuthApplicationBase
    from ..auth_store import AuthStore

__all__ = ["TokensSection"]

ADMIN_TAG = "AUTH_ADMIN"


class TokensSection(RoutingClass):
    """The ``_auth/tokens`` mount: AUTH_ADMIN issue, revocation and removal of tokens.

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
        """A token record without its ``secret_hash``."""
        return {k: v for k, v in record.items() if k != "secret_hash"}

    @route(name="list", auth_rule=ADMIN_TAG)
    def token_list(self) -> dict[str, Any]:
        """Every token record, ``secret_hash`` stripped."""
        return {"tokens": [self.public_record(r) for r in self.store.token_list]}

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    def issue(self, body_data: dict | None = None) -> dict[str, Any]:
        """Mint a token of an existing user; the full ``gak_`` key is answered once."""
        body = body_data or {}
        user = body.get("user")
        if not user:
            return {"error": "User is required"}
        if self.store.get_user(user) is None:
            return {"error": f"No such user: {user}"}
        if body.get("tags") != "*":
            refusal = self.application.get_tags_refusal(body)
            if refusal:
                return refusal
        try:
            key = self.store.issue_token(user, body.get("tags", []), body.get("expires_at"))
        except ValueError as refused:
            return {"error": str(refused)}
        return {"key": key, "user": user}

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    async def revoke(self, key_id: str = "") -> dict[str, Any]:
        """Disable a token (the record stays listed); the credential cache is emptied."""
        revoked = self.store.revoke_token(key_id)
        await self.application.server.forget_credentials_everywhere()
        return {"key_id": key_id, "revoked": revoked}

    @route(auth_rule=ADMIN_TAG, openapi_method="post")
    async def delete(self, key_id: str = "") -> dict[str, Any]:
        """Remove a token record; the credential cache is emptied."""
        deleted = self.store.delete_token(key_id)
        await self.application.server.forget_credentials_everywhere()
        return {"key_id": key_id, "deleted": deleted}
