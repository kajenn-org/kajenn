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

"""The ``_server/tokens`` section: SUPERADMIN-gated issued credentials.

``TokensSection`` is the ONE section for credentials the server issues: API
keys (``gak_``) and short-lived JWTs. It is a ``RoutingClass`` the
``ServerApplication`` attaches under ``tokens`` (endpoints at
``/_server/tokens/...``), ALWAYS declared (fixed structure, D26). Every route is
``auth_rule="SUPERADMIN"`` and answers the ``{"error": ...}`` shape when the
server has no ``api_key_store`` wired.

The secret invariant mirrors the users section: an api key's ``secret_hash``
NEVER crosses the wire (``list`` strips it), and the full ``gak_`` key is
returned ONLY by ``issue``, once — it is never retrievable again (the record
keeps only its hash).

``create_jwt`` mints a JWT signed with the FIRST symmetric (``HS*``) verifier in
the ``auth`` config (``AuthCore.signing_jwt_config``): the token verifies against
the same config, so no new key material is introduced. With no symmetric verifier
configured it answers the error shape.

Parent (dual relationship): the ServerApplication, stored as
``self.application``; the stores are reached via ``self.application.server``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import jwt
from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from kajenn.auth.api_key_store import ApiKeyStore
    from kajenn.auth.core import AuthCore
    from ..server_app import ServerApplication

__all__ = ["TokensSection"]

NO_STORE_ERROR = {"error": "Token management is not available"}
NO_JWT_ERROR = {"error": "No symmetric JWT signing key is configured"}


class TokensSection(RoutingClass):
    """The ``_server/tokens`` mount: SUPERADMIN api-key registry + JWT minting.

    Note:
        Parent (dual relationship): the ServerApplication, stored as
        ``self.application``. The stores live on ``self.application.server``.
    """

    def __init__(self, application: ServerApplication) -> None:
        """Bind the section to its ServerApplication (dual relationship)."""
        self.application = application

    @property
    def api_key_store(self) -> ApiKeyStore | None:
        """The server's ApiKeyStore, or ``None`` when tokens are unconfigured."""
        return getattr(self.application.server, "api_key_store", None)

    @property
    def auth_core(self) -> AuthCore | None:
        """The server's AuthCore (carries the JWT signing seam), or ``None``."""
        return getattr(self.application.server, "auth_core", None)

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """A key record without its ``secret_hash`` — the wire-safe projection."""
        return {k: v for k, v in record.items() if k != "secret_hash"}

    @route(auth_rule="SUPERADMIN")
    def list(self) -> dict[str, Any]:
        """Every api-key record, ``secret_hash`` stripped."""
        store = self.api_key_store
        if store is None:
            return NO_STORE_ERROR
        return {"tokens": [self.public_record(r) for r in store.load_all()]}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def issue(self, body_data: dict | None = None) -> dict[str, Any]:
        """Mint an api key; return the full ``gak_`` key ONCE (never again).

        Body: ``label`` (required), ``tags`` (default ``[]``), ``expires_at``
        (POSIX timestamp or absent for a key that never expires).
        """
        store = self.api_key_store
        if store is None:
            return NO_STORE_ERROR
        body = body_data or {}
        label = body.get("label")
        if not label:
            return {"error": "Label is required"}
        key = store.issue(label, body.get("tags") or [], body.get("expires_at"))
        return {"key": key, "label": label}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def revoke(self, key_id: str = "") -> dict[str, Any]:
        """Disable a key (the record stays, listed, for audit)."""
        store = self.api_key_store
        if store is None:
            return NO_STORE_ERROR
        return {"key_id": key_id, "revoked": store.revoke(key_id)}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def delete(self, key_id: str = "") -> dict[str, Any]:
        """Remove a key record entirely."""
        store = self.api_key_store
        if store is None:
            return NO_STORE_ERROR
        return {"key_id": key_id, "deleted": store.delete(key_id)}

    @route(auth_rule="SUPERADMIN", openapi_method="post")
    def create_jwt(self, body_data: dict | None = None) -> dict[str, Any]:
        """Mint a JWT signed with the first symmetric verifier of the auth config.

        Body: ``sub`` (required — the token's subject), ``tags`` (default ``[]``),
        ``expires_in`` (seconds; absent for no ``exp`` claim). The token verifies
        against the same config it was signed with (no new key material). Answers
        the error shape when no symmetric verifier is configured.
        """
        core = self.auth_core
        config = core.signing_jwt_config if core is not None else None
        if config is None:
            return NO_JWT_ERROR
        body = body_data or {}
        sub = body.get("sub")
        if not sub:
            return {"error": "Subject 'sub' is required"}
        claims: dict[str, Any] = {"sub": sub, "tags": body.get("tags") or []}
        expires_in = body.get("expires_in")
        if expires_in is not None:
            claims["exp"] = int(time.time()) + int(expires_in)
        token = jwt.encode(claims, config["secret"], algorithm=config["algorithm"])
        return {"token": token, "sub": sub}
