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

"""OidcMethod: the login with an external OIDC provider, a method of ``_auth``.

``OidcMethod`` is the ``redirect`` login method of ``AuthApplicationBase``:
one instance per declared provider, with ``method_id`` ``oidc:<code>``. It owns
two routes, so the registry attaches it under ``/_auth/oidc:<code>/``:

- ``GET /_auth/oidc:<code>/start?next=<path>`` — begins the authorization-code
  flow with PKCE (S256). It stores a fresh ``state``, the PKCE verifier and the
  safe ``next`` path in the session's ``data`` under ``oidc.<method_id>.*`` and
  answers 302 to the provider's authorization endpoint.
- ``GET /_auth/oidc:<code>/callback?state=...&code=...`` — the provider sends
  the browser here. It checks the ``state``, exchanges the ``code`` on the
  provider's token endpoint (PKCE verifier, ``client_secret`` when declared),
  verifies the ``id_token`` (RS256 on the provider's JWKS, audience =
  ``client_id``, issuer = ``issuer``), finds the user in the ``AuthStore``,
  attaches the avatar to the session, clears the ``oidc.<method_id>.*`` keys
  and answers 302 to the stored ``next``.

The provider metadata comes from ``<issuer>/.well-known/openid-configuration``,
fetched at first use and kept on the instance: a server whose provider is
unreachable still boots. The JWKS client is built once and kept on the
instance; its blocking key fetch runs through ``server.run_sync``.

The ``redirect_uri`` is absolute: ``<server.external_url>/_auth/oidc:<code>/callback``.
The start request and the token exchange send the same string. Both routes
declare ``env_requires="external_links"``: on a server without
``external_url`` they are not available and the router refuses them with
``not_available`` (see ``ServerCapabilities``), and ``login_methods`` leaves
the provider out.

The user, from the verified ``id_token`` claims:

- the user whose ``external_ids`` holds the token's ``iss`` + ``sub``, whatever
  its email is now;
- else, only when the token carries ``email`` and ``email_verified`` true: the
  one user with that email (compared lowercased); ``{iss, sub}`` is appended to
  its ``external_ids`` and the record is saved. A system user is never written.
  Two or more users with that email: refused, nothing written;
- else the ``self_registration`` setting of the application: absent, refused.
  Present, a record is written with identity = the verified email, ``status``
  and ``tags`` from the setting, ``groups`` [], ``primary_group`` None,
  ``system`` False, ``password_hash`` None, ``email``, ``external_ids``
  [{iss, sub}]. An identity already taken: refused, nothing written.

A user found or written whose ``status`` is not ``confirmed``, or that is a
system user, is refused. The avatar is
``Avatar(identity, store.get_user_tags(identity), group=primary_group)``.
The callback does not touch the lockout counter.

Answers of the callback:

- 302 to the stored ``next`` on success;
- 400 (``HTTPBadRequest``) for a flow error: a ``state`` missing or different
  from the session's, a failed token exchange, an ``id_token`` that does not
  verify or carries no ``sub``;
- 403 (``HTTPForbidden``) for a refused login: no verified email, an ambiguous
  email, an unknown user without ``self_registration``, an identity already
  taken, a status that does not authenticate, a system user. Nothing is
  attached.

``descriptor()`` is the public ``login_methods`` entry: ``id``, ``kind``,
``label`` and the relative ``url`` of ``start``; never the client id, the
issuer or the secret.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from genro_routes import route

from kajenn.exceptions import HTTPBadRequest, HTTPForbidden, Redirect
from kajenn.session import Avatar

from .auth_method import AuthMethod
from .auth_store import AUTHENTICATING_STATUS, AuthStore

__all__ = ["OidcMethod"]

DEFAULT_SCOPES = "openid email profile"
FLOW_KEYS = ("state", "verifier", "next")


class OidcMethod(AuthMethod):
    """An OIDC provider as a ``redirect`` login method of ``_auth`` (see module docstring)."""

    kind = "redirect"
    env_requires = "external_links"

    def __init__(
        self, application: Any, method_id: str, code: str, provider: dict[str, Any]
    ) -> None:
        """Bind the method to its application and to the provider's settings.

        ``provider`` carries ``issuer``, ``client_id``, optional
        ``client_secret`` and optional ``scopes``.
        """
        super().__init__(application, method_id)
        self._code = code
        self._provider = provider
        self._discovery: dict[str, Any] | None = None
        self._jwk_client: jwt.PyJWKClient | None = None

    @property
    def code(self) -> str:
        """The provider's code."""
        return self._code

    @property
    def provider(self) -> dict[str, Any]:
        """The provider's settings (carries the secret, never exposed)."""
        return self._provider

    @property
    def start_url(self) -> str:
        """The relative url of the ``start`` route."""
        return f"/{self.application.mount}/{self.method_id}/start"

    @property
    def callback_url(self) -> str:
        """The absolute ``redirect_uri``, built from the server's ``external_url``."""
        return f"{self.application.server.external_url}/{self.application.mount}/{self.method_id}/callback"

    def descriptor(self) -> dict[str, Any]:
        """Public descriptor: a labelled redirect button to the ``start`` url."""
        return {**super().descriptor(), "label": f"Sign in with {self.code}", "url": self.start_url}

    def get_safe_next_path(self, value: str | None) -> str:
        """``value`` when it is a same-origin relative path, else ``/``.

        A safe path starts with one ``/``, is not ``//host`` and carries no
        backslash.
        """
        if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
            return "/"
        return value

    async def discovery(self) -> dict[str, Any]:
        """The provider's OIDC metadata, fetched at first use and kept on the instance."""
        discovery = self._discovery
        if discovery is None:
            issuer = self.provider["issuer"].rstrip("/")
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{issuer}/.well-known/openid-configuration")
                response.raise_for_status()
            discovery = self._discovery = response.json()
        return discovery

    async def jwk_client(self) -> jwt.PyJWKClient:
        """The provider's JWKS client, built once from ``jwks_uri`` and kept on the instance."""
        client = self._jwk_client
        if client is None:
            discovery = await self.discovery()
            client = self._jwk_client = jwt.PyJWKClient(discovery["jwks_uri"])
        return client

    # ── routes ─────────────────────────────────────────────────────────

    @route(media_type="text/plain", env_requires=env_requires)
    async def start(self, next: str = "", _request=None) -> str:
        """Begin the authorization-code flow with PKCE: redirect to the provider.

        Raises:
            Redirect: 302 to the provider's authorization endpoint.

        Note:
            Route: GET /_auth/oidc:<code>/start
        """
        discovery = await self.discovery()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        session = _request.session
        session.data[f"oidc.{self.method_id}.state"] = state
        session.data[f"oidc.{self.method_id}.verifier"] = verifier
        session.data[f"oidc.{self.method_id}.next"] = self.get_safe_next_path(next)
        session.mark_dirty()
        params = {
            "response_type": "code",
            "client_id": self.provider["client_id"],
            "redirect_uri": self.callback_url,
            "scope": self.provider.get("scopes") or DEFAULT_SCOPES,
            "state": state,
            "code_challenge": self._pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        raise Redirect(f"{discovery['authorization_endpoint']}?{urlencode(params)}")

    @route(media_type="text/plain", env_requires=env_requires)
    async def callback(self, state: str = "", code: str = "", _request=None) -> str:
        """Close the flow: check state, exchange, verify, find the user, attach, redirect.

        The callback is counted in ``server.event_metrics`` as scheme ``oidc``:
        ``accepted`` on redirect, ``refused`` on HTTPBadRequest or HTTPForbidden.

        Raises:
            HTTPBadRequest: A flow error (state, token exchange, id_token).
            HTTPForbidden: A refused login; nothing is attached.
            Redirect: 302 to the stored safe ``next`` on success.

        Note:
            Route: GET /_auth/oidc:<code>/callback
        """
        metrics = self.application.server.event_metrics
        try:
            next_target = await self._attach_identity(state, code, _request.session)
        except (HTTPBadRequest, HTTPForbidden):
            metrics.record_credential_check("oidc", "refused")
            raise
        metrics.record_credential_check("oidc", "accepted")
        raise Redirect(self.get_safe_next_path(next_target))

    async def _attach_identity(self, state: str, code: str, session: Any) -> str:
        """Check the flow and attach the avatar; return the stored next path."""
        expected = session.data[f"oidc.{self.method_id}.state"]
        if not state or state != expected:
            raise HTTPBadRequest("OIDC state mismatch")
        verifier = session.data[f"oidc.{self.method_id}.verifier"]
        next_target = session.data[f"oidc.{self.method_id}.next"] or "/"
        id_token = await self._exchange_code(code, verifier)
        claims = await self._verify_id_token(id_token)
        if not claims.get("sub"):
            raise HTTPBadRequest("OIDC id_token carries no sub")
        avatar = await self.application.server.run_sync(self.authenticate_claims, claims)
        session.attach_avatar(avatar)
        for suffix in FLOW_KEYS:
            session.data.pop(f"oidc.{self.method_id}.{suffix}", None)
        session.mark_dirty()
        return next_target

    # ── the user in the AuthStore ──────────────────────────────────────

    def authenticate_claims(self, claims: dict[str, Any]) -> Avatar:
        """The avatar of the user the verified ``claims`` lead to.

        Finds, links or registers the user (``link_user``), then refuses a
        status that does not authenticate and a system user.

        Raises:
            HTTPForbidden: A refused login.
        """
        user = self.link_user(claims)
        if user.get("status") != AUTHENTICATING_STATUS or user.get("system", False):
            raise HTTPForbidden("OIDC login refused: the user cannot sign in")
        identity = user["identity"]
        store: AuthStore = self.application.store
        return Avatar(identity, store.get_user_tags(identity), group=user.get("primary_group"))

    def link_user(self, claims: dict[str, Any]) -> dict[str, Any]:
        """The user record of ``claims``: by ``iss`` + ``sub``, else by verified email, else registered.

        Writes ``{iss, sub}`` on the one confirmed, non-system user with the
        verified email; writes a new record through ``register_user`` when nobody has it.

        Raises:
            HTTPForbidden: No verified email, an ambiguous email, a user found
                by email that is not confirmed or is a system user, or a refused
                registration.
        """
        store: AuthStore = self.application.store
        iss, sub = claims["iss"], claims["sub"]
        user = store.get_user_by_external_id(iss, sub)
        if user is not None:
            return user
        email = claims.get("email")
        if not email or claims.get("email_verified") is not True:
            raise HTTPForbidden("OIDC login refused: no verified email")
        matches = store.get_user_list_by_email(email)
        if len(matches) > 1:
            raise HTTPForbidden("OIDC login refused: more than one user has this email")
        if not matches:
            return self.register_user(email, iss, sub)
        user = matches[0]
        if user.get("status") != AUTHENTICATING_STATUS or user.get("system", False):
            raise HTTPForbidden("OIDC login refused: the user cannot sign in")
        user["external_ids"] = [*user.get("external_ids", []), {"iss": iss, "sub": sub}]
        store.save_user(user)
        return user

    def register_user(self, email: str, iss: str, sub: str) -> dict[str, Any]:
        """Write the record of an unknown user under the ``self_registration`` setting.

        Raises:
            HTTPForbidden: No ``self_registration`` setting, or the identity
                ``email`` is already taken.
        """
        setting = self.application.self_registration
        if not setting:
            raise HTTPForbidden("OIDC login refused: unknown user")
        store: AuthStore = self.application.store
        if store.get_user(email) is not None:
            raise HTTPForbidden("OIDC login refused: the identity is already taken")
        record: dict[str, Any] = {
            "identity": email,
            "groups": [],
            "primary_group": None,
            "tags": list(setting.get("tags") or []),
            "status": setting.get("status"),
            "system": False,
            "password_hash": None,
            "email": email,
            "external_ids": [{"iss": iss, "sub": sub}],
        }
        store.save_user(record)
        return record

    # ── provider calls ─────────────────────────────────────────────────

    async def _exchange_code(self, code: str, verifier: str) -> str:
        """Exchange the authorization ``code`` for tokens; return the ``id_token`` (400 on failure)."""
        discovery = await self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.callback_url,
            "client_id": self.provider["client_id"],
            "code_verifier": verifier,
        }
        client_secret = self.provider.get("client_secret")
        if client_secret:
            data["client_secret"] = client_secret
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(discovery["token_endpoint"], data=data)
                response.raise_for_status()
            id_token: str = response.json().get("id_token")
        except httpx.HTTPError as error:
            raise HTTPBadRequest("OIDC token exchange failed") from error
        if not id_token:
            raise HTTPBadRequest("OIDC token response carried no id_token")
        return id_token

    async def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Verify signature, audience and issuer of ``id_token``; return its claims (400 on failure)."""
        client = await self.jwk_client()
        try:
            signing_key = await self.application.server.run_sync(
                lambda: client.get_signing_key_from_jwt(id_token)
            )
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.provider["client_id"],
                issuer=self.provider["issuer"],
            )
        except (jwt.InvalidTokenError, jwt.PyJWKClientError) as error:
            raise HTTPBadRequest("OIDC id_token verification failed") from error
        return claims

    def _pkce_challenge(self, verifier: str) -> str:
        """The S256 PKCE challenge of ``verifier`` (base64url, no padding)."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
