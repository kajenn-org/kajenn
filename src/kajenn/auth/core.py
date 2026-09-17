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

"""Auth core: credential configuration and header verification (salvage Q5).

``AuthCore`` holds the whole credential configuration and the verification
logic for the ``basic``, ``bearer`` and ``jwt`` schemes. It is router-free —
configured per instance from ``{'basic': ..., 'bearer': ..., 'jwt': [...]}``
and consumed by the server-side ``AuthMixin`` (never by walking wrappers).

Config sections (``_configure_<type>`` dynamic dispatch, unknown sections
ignored)::

    basic:  {username: {password: "...", tags: "..."}} — O(1) lookup keyed
            by the base64 username:password the Basic header carries.
    bearer: {name: {token: "...", tags: "..."}} — O(1) lookup keyed by the
            token value.
    jwt:    [{secret|public_key, algorithm, tags, name}, ...] — a list of
            verifier configs, each tried in turn and decoded with pyjwt.

``authenticate(scope)`` reads the ``Authorization`` header via the shared
``headers_dict`` cache, dispatches ``_auth_<scheme>`` and returns an ``Avatar``
(the ONE identity type, unified with sessions) or ``None`` when NO credentials
are presented. Credentials PRESENT but invalid raise ``HTTPUnauthorized`` —
never a silent fallback; a malformed header (no scheme/value separator) is
present-but-invalid, so it raises too. Tag normalization (e.g. a JWT null
``tags`` claim) happens at the ``Avatar`` boundary, never ``list(None)``.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import jwt

from ..exceptions import HTTPUnauthorized
from ..middleware.base import headers_dict
from ..session.avatar import Avatar
from .api_key_store import API_KEY_PREFIX

if TYPE_CHECKING:
    from ..types import Scope
    from .api_key_store import ApiKeyStore

__all__ = ["AuthCore"]

# HMAC algorithms share one secret for sign and verify, so a verifier using them
# can also MINT tokens (the tokens surface's create_jwt); asymmetric verifiers
# (RS*/ES*, a public_key) can only verify.
SYMMETRIC_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


def _split_and_strip(value: str | list[str] | None) -> list[str]:
    """Split a comma-separated string into a stripped list; pass a list through."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",")]
    return list(value)


def _basic_auth_key(username: str, password: str) -> str:
    """Base64-encode ``username:password`` as the HTTP Basic header carries it."""
    return base64.b64encode(f"{username}:{password}".encode()).decode()


class AuthCore:
    """Per-instance credential store and header verifier for basic/bearer/jwt."""

    def __init__(self, api_key_store: ApiKeyStore | None = None, **entries: Any) -> None:
        """Build the credential store from the configured sections.

        ``api_key_store`` is the wired registry the ``AuthMixin`` passes: when
        present, an inbound ``gak_`` bearer token is verified against it before
        the static-bearer/JWT chain.
        """
        self._api_key_store = api_key_store
        self._basic: dict[str, dict[str, Any]] = {}
        self._bearer: dict[str, dict[str, Any]] = {}
        self._jwt: list[dict[str, Any]] = []
        for auth_type, credentials in entries.items():
            configure = getattr(self, f"_configure_{auth_type}", self._configure_default)
            configure(credentials)

    def _configure_basic(self, credentials: dict[str, Any]) -> None:
        """Index Basic credentials by their base64 ``username:password`` key."""
        for username, config in credentials.items():
            password = config.get("password")
            if not password:
                raise ValueError(f"Basic auth user '{username}' missing 'password'")
            self._basic[_basic_auth_key(username, password)] = {
                "identity": username,
                "tags": _split_and_strip(config.get("tags")),
                "backend": "basic",
            }

    def _configure_bearer(self, credentials: dict[str, Any]) -> None:
        """Index Bearer credentials by their token value."""
        for name, config in credentials.items():
            token = config.get("token")
            if not token:
                raise ValueError(f"Bearer token '{name}' missing 'token' value")
            self._bearer[token] = {
                "identity": name,
                "tags": _split_and_strip(config.get("tags")),
                "backend": "bearer",
            }

    def _configure_jwt(self, credentials: list[dict[str, Any]]) -> None:
        """Store the JWT verifier configs as an ordered list of secrets/algorithms.

        ``signing`` records the key's provenance: only a key configured as
        ``secret`` (shared HMAC material) can SIGN — a ``public_key`` folded
        into the same slot verifies but must never mint tokens, whatever the
        (defaulted) algorithm says.
        """
        for config in credentials:
            self._jwt.append(
                {
                    "name": config.get("name"),
                    "secret": config.get("secret") or config.get("public_key"),
                    "algorithm": config.get("algorithm", "HS256"),
                    "tags": _split_and_strip(config.get("tags")),
                    "signing": bool(config.get("secret")),
                }
            )

    def _configure_default(self, credentials: Any) -> None:
        """Unknown config section — ignored (salvage semantics)."""

    @property
    def signing_jwt_config(self) -> dict[str, Any] | None:
        """The first symmetric (``HS*``) JWT verifier config, usable for signing.

        A read-only seam for the tokens surface: HMAC verifiers share one secret
        for sign and verify, so a token minted here verifies against the same
        config with zero new key material. Asymmetric verifiers (``RS*``/``ES*``,
        a ``public_key``) can only verify and are skipped — the ``signing``
        provenance flag guards them even when the algorithm was defaulted, so
        public key material can never mint tokens. ``None`` when no symmetric
        verifier is configured.
        """
        for config in self._jwt:
            if config["algorithm"] in SYMMETRIC_JWT_ALGORITHMS and config["signing"]:
                return config
        return None

    def authenticate(self, scope: Scope) -> Avatar | None:
        """Verify the request's credentials; return an ``Avatar`` or ``None``.

        ``None`` when no ``Authorization`` header is presented; a present but
        invalid credential — including a malformed header with no scheme/value
        separator — raises ``HTTPUnauthorized`` (no silent fallback) carrying a
        ``WWW-Authenticate: Bearer`` challenge header.
        """
        header = headers_dict(scope).get("authorization")
        if not header:
            return None
        challenge = [(b"www-authenticate", b"Bearer")]
        if " " not in header:
            raise HTTPUnauthorized("Malformed Authorization header", headers=challenge)
        scheme, credentials = header.split(" ", 1)
        verify = getattr(self, f"_auth_{scheme.lower()}", self._auth_default)
        result = verify(credentials)
        if result is None:
            raise HTTPUnauthorized("Invalid or expired credentials", headers=challenge)
        return Avatar(result["identity"], result["tags"])

    def _auth_basic(self, credentials: str) -> dict[str, Any] | None:
        """Look up Basic credentials by their raw base64 header value."""
        return self._basic.get(credentials)

    def _auth_bearer(self, credentials: str) -> dict[str, Any] | None:
        """Resolve a Bearer token: a ``gak_`` api key first, then static/JWT.

        A ``gak_``-prefixed token is unambiguously an api key: it is verified
        against the registry and NEVER falls through to the JWT chain — a miss
        (revoked, expired, unknown, or no store wired) is a hard ``None``.
        Any other token keeps the static-bearer → JWT chain unchanged.
        """
        if credentials.startswith(API_KEY_PREFIX):
            return self._auth_api_key(credentials)
        entry = self._bearer.get(credentials)
        if entry is not None:
            return entry
        return self._auth_jwt(credentials)

    def _auth_api_key(self, credentials: str) -> dict[str, Any] | None:
        """Verify a ``gak_`` key against the wired registry (identity = label)."""
        if self._api_key_store is None:
            return None
        record = self._api_key_store.verify(credentials)
        if record is None:
            return None
        return {
            "identity": record["label"],
            "tags": record["tags"],
            "backend": "api_key",
        }

    def _auth_jwt(self, credentials: str) -> dict[str, Any] | None:
        """Try each configured JWT verifier in turn."""
        for config in self._jwt:
            result = self._verify_jwt(credentials, config)
            if result is not None:
                return result
        return None

    def _auth_default(self, credentials: str) -> dict[str, Any] | None:
        """Unknown scheme — always rejected."""
        return None

    def _verify_jwt(self, credentials: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Decode a JWT with one verifier config; return the identity/tags or ``None``."""
        secret = config.get("secret")
        if not secret:
            return None
        name = config.get("name")
        try:
            payload = jwt.decode(credentials, secret, algorithms=[config["algorithm"]])
        except jwt.InvalidTokenError:
            return None
        return {
            "identity": payload.get("sub"),
            "tags": payload.get("tags", config["tags"]),
            "backend": f"jwt:{name}" if name else "jwt",
        }
