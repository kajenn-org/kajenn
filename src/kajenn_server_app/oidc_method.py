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

"""OIDC login method — authorization-code + PKCE redirect to an external provider.

``OidcMethod`` is the ``redirect`` auth method: one instance per configured
provider, registered on the ``ServerApplication`` from the ``oidc()`` config.
Unlike the password method it OWNS routes, so ``AuthSection`` attaches it under
``/_server/auth/<method_id>/`` (``method_id`` = ``oidc:<code>``): the ``start``
route and the ``callback`` route the provider redirects back to.

Provider metadata (the authorization/token endpoints, the JWKS uri) is NOT
fetched at construction: a server booting while its provider is unreachable
must not die. ``discovery()`` fetches ``<issuer>/.well-known/openid-configuration``
LAZILY at first use and caches it per instance.

The ``redirect_uri`` handed to the provider is ABSOLUTE (RFC 6749 §3.1.2) and
built from the server's ``external_url`` — its declared public base address.
Two properties make that the only workable source: the provider compares the
URI byte for byte with the one registered for the client, and the token
exchange must repeat the SAME value in a context where no request exists.
A server carrying a provider without ``external_url`` refuses to boot.

The ``start`` route begins the authorization-code flow with PKCE (S256, always
on — the ``client_secret`` is optional, a public client works without one and
the secret is never logged nor put on the wire here). It mints a fresh ``state``
and PKCE ``verifier``, stores them and the safe ``next`` target in the session's
``data`` (``mark_dirty`` so they survive to the callback), and redirects the
browser to the provider's authorization endpoint. The ``callback`` route closes
the round trip: it checks the ``state``, exchanges the ``code`` for tokens on the
provider's token endpoint (PKCE ``verifier``, ``client_secret`` when configured),
verifies the ``id_token`` (RS256 against the provider JWKS, audience/issuer
checked), and converges on the same success outcome every method shares — the
``Avatar`` attached to the session — then redirects to the stored safe ``next``.

``descriptor()`` marks the method a ``redirect`` for the login page: a labelled
button navigating to the ``start`` url (``login.html`` renders ``kind=redirect``
buttons). The descriptor is the PUBLIC ``login_methods`` payload — it carries the
id, kind, label and start url ONLY, never the client id, issuer, or secret.
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

from kajenn.exceptions import HTTPBadRequest, Redirect
from kajenn.session.avatar import Avatar
from .auth_method import AuthMethod, safe_next_path

__all__ = ["OidcMethod"]

DEFAULT_SCOPES = "openid email profile"


class OidcMethod(AuthMethod):
    """An OIDC provider as a ``redirect`` login method (authorization-code + PKCE).

    Owns the ``start`` route (and the ``callback`` route, next phase), so it is
    attached under ``/_server/auth/<method_id>/`` (Invariant 10: a method with
    routes enters the routing tree). Discovery is lazy and cached; PKCE is always
    S256; the ``client_secret`` (when configured) stays in ``provider`` and is
    never logged nor exposed by the descriptor.
    """

    kind = "redirect"

    def __init__(self, application: Any, method_id: str, code: str, provider: dict[str, Any]) -> None:
        """Bind the method to its login surface and its provider config.

        Args:
            application: The ServerApplication the login surface belongs to
                (dual relationship). The AsgiServer is ``application.server``.
            method_id: The mount name under ``_server/auth`` (``oidc:<code>``).
            code: The provider's config code (also the label source).
            provider: The resolved provider config (``issuer``, ``client_id``,
                optional ``client_secret``, ``scopes``, ``identity_claim``,
                ``tags``) — defaults already applied at the config fold.
        """
        super().__init__(application, method_id)
        self._code = code
        self._provider = provider
        self._discovery: dict[str, Any] | None = None
        self._jwk_client: jwt.PyJWKClient | None = None

    @property
    def code(self) -> str:
        """The provider's config code."""
        return self._code

    @property
    def provider(self) -> dict[str, Any]:
        """The resolved provider config (carries the secret — never exposed)."""
        return self._provider

    async def discovery(self) -> dict[str, Any]:
        """The provider's OIDC metadata, fetched once and cached per instance.

        Fetches ``<issuer>/.well-known/openid-configuration`` on first use — never
        at construction, so a server whose provider is unreachable still boots.
        The cached document carries the authorization/token endpoints and the
        JWKS uri (the last two consumed by the callback in the next phase).
        """
        discovery = self._discovery
        if discovery is None:
            issuer = self.provider["issuer"].rstrip("/")
            url = f"{issuer}/.well-known/openid-configuration"
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
            discovery = self._discovery = response.json()
        return discovery

    def start_url(self) -> str:
        """The ``start`` route url — the descriptor entry the login page navigates to.

        RELATIVE by design: the login page navigates to it within our own site,
        so it needs no host (and works on every address the server answers on).
        """
        return f"/_server/auth/{self.method_id}/start"

    def callback_url(self) -> str:
        """The ABSOLUTE ``callback`` url — the ``redirect_uri`` handed to the provider.

        Absolute because the provider redirects a browser to it from another
        origin and matches it against the URI registered for the client (RFC
        6749 §3.1.2); relative here would be rejected before the user ever sees
        a login screen. Built from the server's declared ``external_url``, so the
        authorization request and the token exchange — which has no request to
        derive a host from — send the identical string. The server refuses to
        boot with a provider configured and no ``external_url``, so this never
        renders a ``None`` prefix.
        """
        return f"{self.server.external_url}/_server/auth/{self.method_id}/callback"

    def descriptor(self) -> dict[str, Any]:
        """Public descriptor for the login page: a labelled redirect button.

        Extends the base ``{id, kind}`` with the ``label`` (derived from the
        provider code) and the ``url`` the button navigates to (the ``start``
        route). Deliberately carries no client id, issuer, or secret — it is the
        unauthenticated ``login_methods`` payload.
        """
        return {**super().descriptor(), "label": f"Sign in with {self.code}", "url": self.start_url()}

    @route(media_type="text/plain")
    async def start(self, next: str = "", _request=None) -> str:
        """Begin the authorization-code + PKCE flow: redirect to the provider.

        Mints a fresh ``state`` and PKCE ``verifier``, stores them and the safe
        ``next`` target under ``oidc.<method_id>.*`` in the session's ``data``
        (``mark_dirty`` so the callback can read them back), then redirects the
        browser to the provider's authorization endpoint with the S256 challenge.
        The ``client_secret`` is NOT used here (it belongs to the token exchange
        in the callback) — the authorization request is a public redirect.

        Args:
            next: Post-login redirect target, validated by ``safe_next_path``
                (same-origin relative only) before it is stored.
            _request: The live ``Request``, injected by the app's ``bind_kwargs``.
                Left unannotated so it stays out of the pydantic model (and the
                public OpenAPI schema); reached for ``_request.session``.

        Raises:
            Redirect: 302 to the provider's authorization endpoint.

        Note:
            Route: GET /_server/auth/<method_id>/start
        """
        discovery = await self.discovery()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        session = _request.session
        session.data[f"oidc.{self.method_id}.state"] = state
        session.data[f"oidc.{self.method_id}.verifier"] = verifier
        session.data[f"oidc.{self.method_id}.next"] = safe_next_path(next)
        session.mark_dirty()
        params = {
            "response_type": "code",
            "client_id": self.provider["client_id"],
            "redirect_uri": self.callback_url(),
            "scope": self.provider.get("scopes", DEFAULT_SCOPES),
            "state": state,
            "code_challenge": self._pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        raise Redirect(f"{discovery['authorization_endpoint']}?{urlencode(params)}")

    @route(media_type="text/plain")
    async def callback(self, state: str = "", code: str = "", _request=None) -> str:
        """Close the authorization-code flow: exchange, validate, attach, redirect.

        The provider redirects the browser here (``redirect_uri`` =
        ``callback_url``) with the ``state`` and the authorization ``code``. The
        ``state`` MUST match the value ``start`` stored in the session (a
        mismatch or a missing state is refused, never retried): it binds the
        callback to the session that began the flow and defeats CSRF. The
        ``code`` is exchanged for tokens on the provider's token endpoint with
        the stored PKCE ``verifier`` (and the ``client_secret`` when configured);
        the returned ``id_token`` is verified (RS256 against the provider JWKS,
        audience = ``client_id``, issuer = the configured issuer). The identity
        is read from the ``identity_claim`` claim (config default ``email``), the
        tags from the provider config, and the ``Avatar`` is attached to the
        request's session in place — the session id never changes, so no cookie
        is involved. The one-shot ``oidc.<method_id>.*`` keys are cleared, and the
        browser is redirected to the safe ``next`` ``start`` stored.

        Args:
            state: The opaque value echoed back by the provider; must match the
                session's stored state.
            code: The authorization code to exchange for tokens.
            _request: The live ``Request``, injected by ``bind_kwargs``. Left
                unannotated so it stays out of the pydantic model (and the public
                OpenAPI schema); reached for ``_request.session``.

        Raises:
            HTTPBadRequest: On a missing/mismatched state, a failed token
                exchange, or an invalid id_token.
            Redirect: 302 to the stored safe ``next`` on success.

        Note:
            Route: GET /_server/auth/<method_id>/callback
        """
        session = _request.session
        expected = session.data[f"oidc.{self.method_id}.state"]
        if not state or state != expected:
            raise HTTPBadRequest("OIDC state mismatch")
        verifier = session.data[f"oidc.{self.method_id}.verifier"]
        next_target = session.data[f"oidc.{self.method_id}.next"] or "/"
        id_token = await self._exchange_code(code, verifier)
        payload = await self._verify_id_token(id_token)
        identity = payload.get(self.provider.get("identity_claim", "email"))
        if not identity:
            raise HTTPBadRequest("OIDC identity claim missing")
        avatar = Avatar(identity, self.provider.get("tags", []))
        session.attach_avatar(avatar)
        for suffix in ("state", "verifier", "next"):
            session.data.pop(f"oidc.{self.method_id}.{suffix}", None)
        session.mark_dirty()
        raise Redirect(safe_next_path(next_target))

    async def _exchange_code(self, code: str, verifier: str) -> str:
        """Exchange the authorization ``code`` for tokens; return the ``id_token``.

        POSTs the authorization-code grant to the provider's token endpoint with
        the PKCE ``code_verifier`` (and ``client_secret`` when the provider is a
        confidential client). A transport or provider error, or a response
        without an ``id_token``, is a bad request — the callback answers 400.
        """
        discovery = await self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.callback_url(),
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

    async def jwk_client(self) -> jwt.PyJWKClient:
        """The provider's JWKS client, built once and kept for the instance.

        ``PyJWKClient`` caches the fetched key set (its own ``lifespan``), which
        only pays off if the client SURVIVES the request — a fresh one per
        callback would re-fetch every time. Built from the lazily discovered
        ``jwks_uri``, so it inherits the offline-boot guarantee.
        """
        client = self._jwk_client
        if client is None:
            discovery = await self.discovery()
            client = self._jwk_client = jwt.PyJWKClient(discovery["jwks_uri"])
        return client

    async def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Verify the ``id_token`` signature/audience/issuer; return its claims.

        The signing key is resolved from the provider JWKS
        (``discovery()["jwks_uri"]``) via ``jwt.PyJWKClient`` — RS256, audience =
        the client id, issuer = the configured issuer. Any verification failure
        collapses to a 400 (mirrors ``AuthCore._verify_jwt``'s
        ``InvalidTokenError`` shape).

        ``PyJWKClient`` fetches the key set with a BLOCKING stdlib call, so the
        resolution goes through ``server.run_sync`` (the D2 pool protocol every
        blocking call in the core follows) — on the loop it would freeze the whole
        server for the duration of a request to the provider. The signature check
        itself is pure CPU and stays here.
        """
        client = await self.jwk_client()
        try:
            signing_key = await self.server.run_sync(
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
        """The S256 PKCE challenge for ``verifier`` (base64url, no padding, RFC 7636)."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
