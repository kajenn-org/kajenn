# Authentication — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Credential precedence

`AuthMixin.authenticate` verifies a presented `Authorization` header through
`authenticate_credential`. Without a header, the session's root avatar answers.
Without a session avatar, the answer is `None`. A present invalid header raises
`HTTPUnauthorized` on any route; it does not fall back to a logged-in session.
A scope without `kajenn.channel` is read as the `rest` channel.

`RoutedApplication.authenticate_scope` calls `server.authenticate(scope)` when
`scope["auth"]` is missing or `None`. `execute` and `discover` call it before
resolution. No middleware layer authenticates.

Claim anchors: [`AuthMixin`](../../../src/kajenn/auth/mixin.py#AuthMixin), [`authenticate`](../../../src/kajenn/auth/mixin.py#AuthMixin.authenticate), [`authenticate_scope`](../../../src/kajenn/routed_application.py#RoutedApplication.authenticate_scope).

Behavior evidence: tests `test_an_invalid_bearer_is_401_even_on_a_public_route`, `test_the_session_avatar_wins_without_header`, `test_no_auth_middleware_anywhere` (tests/core/test_authentication_on_demand.py).

## Credential verification through the channel's authentication route

`authenticate_credential` sends the credential with a `kbus_call` to the
channel's `authentication_route`. The default route is
`/_server/auth/authenticate` (`DEFAULT_AUTHENTICATION_ROUTE`, auth/mixin.py:78).
A route answer of 401 or 404 becomes `HTTPUnauthorized` with
`WWW-Authenticate: Bearer`. An unreachable route, or a status of 500 and above,
becomes a 503 (auth/mixin.py:112-159).

`AuthSection.authenticate` serves the default route. It runs
`AuthCore.verify_with_validity` and answers `{identity, tags, data,
valid_until}`. It serves the bus only: an HTTP caller gets 403
(auth_section.py:107-140; test `test_the_server_auth_routes_serve_the_bus_only`).

`AuthCore` verifies configured Basic, static Bearer and JWT credentials.
`gak_` API keys use the API-key store and never fall through to JWT on a miss.

Claim anchors: [`authenticate_credential`](../../../src/kajenn/auth/mixin.py#AuthMixin.authenticate_credential), [`AuthCore`](../../../src/kajenn/auth/core.py#AuthCore), [`verify_with_validity`](../../../src/kajenn/auth/core.py#AuthCore.verify_with_validity), [`AuthSection.authenticate`](../../../src/kajenn/server_app/server_sections/auth_section.py#AuthSection.authenticate).

## Verified-credential cache

A verified avatar is cached under the key `(sha256(credential), channel)`. The
lifetime is `credential_cache_ttl`, default 300 seconds. A `valid_until` in the
route answer caps it at that instant. `0` disables the cache. Failures are not
cached (auth/mixin.py:127-159; tests in tests/core/test_credential_validity.py).

`forget_credential` and `forget_all_credentials` evict entries in the current
process. `forget_credentials_everywhere` evicts here and calls
`<code>.forget_credentials` on every joined process. A process that does not
confirm within `FORGET_CREDENTIALS_TIMEOUT` is relaunched
(kbus_mixin.py:161-183). Revoking or deleting an api key calls it
(tokens_section.py:115, 125). The route `POST /_server/auth/forget_credential`
calls it too, bus only (auth_section.py:141-150).

Behavior evidence: test `test_a_process_that_does_not_confirm_the_revocation_is_relaunched` (tests/core/test_revocation_across_processes.py).

## Configuration of channels and cache

The recipe element `authentication(cache_ttl)` sets `credential_cache_ttl`
(config/elements.py:275; config/handler.py:181-183). The collection
`channels` holds one `channel(name, authentication_route)` per channel
(config/elements.py:393-404). A channel without `authentication_route` is
refused when the server is built (config/handler.py:200-202).

## Identity stores and login surface

The server's local user and token stores are optional. Configured store
descriptors name a `store_class` and its kwargs, built on the server storage.
No built store can be supplied: `_build_user_store` and `_build_api_key_store`
take a descriptor only (auth/mixin.py:189-210). The server creates no user at
boot: there is no bootstrap admin (auth/mixin.py:33-36).

Password login and OIDC methods live on `ServerApplication` and attach an
avatar in place. OIDC configuration requires a declared `external_url` at
server construction (asgi_server.py:148, 265-284).

`login` enforces a lockout per identity. After `max_attempts` consecutive
failures the identity is refused for `backoff * 2**(failed - max_attempts)`
seconds. The defaults are 5 attempts and 30 seconds (`LOCKOUT_MAX_ATTEMPTS`,
`LOCKOUT_BACKOFF_SECONDS`, server_app.py:106-107). The application element
`login(max_attempts, backoff)` tunes it (server_app.py:155). Tests:
`test_max_attempts_failures_lock_even_the_correct_password`,
`test_window_expiry_re_allows_and_success_resets` (tests/server_app/test_login_flow.py).

Each OIDC provider is registered as the method `oidc:<code>`, with the routes
`/_server/auth/oidc:<code>/start` and `/_server/auth/oidc:<code>/callback`
(server_app.py:243-245, 390; oidc_method.py:165). `start` uses PKCE S256
(oidc_method.py:203). The provider metadata is fetched on first use, never at
construction (`discovery`, oidc_method.py:114-130). The application element
`oidc()` holds one `provider(code, issuer, client_id, ...)` per provider
(server_app.py:159-172).

`GET /_server/login_methods` answers the descriptors of the registered methods.
It declares no `auth_rule` (server_app.py:554-570).

Claim anchors: [`ServerApplication`](../../../src/kajenn/server_app/server_app.py#ServerApplication), [`login_methods`](../../../src/kajenn/server_app/server_app.py#ServerApplication.login_methods), [`OidcMethod.discovery`](../../../src/kajenn/server_app/oidc_method.py#OidcMethod.discovery).

## Identity carried by a bus message

A message served from the bus carries `meta["auth"]`. `avatar_from_auth`
rebuilds it into `scope["auth"]` with no verification
(kbus_mixin.py:472-485, 520-521). `authenticate_scope` leaves a scope that
already carries an identity as it is. A child process can therefore declare any
identity and any tags. Test: `test_a_frame_with_auth_is_trusted_as_it_is`
(tests/core/test_authentication_on_demand.py).

## Authorization and hosted-site identity

`RoutedApplication` passes avatar tags to the auth plugin: unknown identity
receives 401, insufficient tags receive 403. The 401 of a ruled entry carries
`WWW-Authenticate: Bearer` (`_ChallengedUnauthorized`, routed_application.py:120-122).
`ErrorMiddleware` forwards the exception's header pairs onto the response; it
does not negotiate a login page (errors.py:128-131). The monitor and the
databases section declare `SERVER_ADMIN`; users, tokens and tasks declare
`SUPERADMIN`. Login endpoints are public.

The separately recorded return-path conversion — a hosted site's own identity
becoming a core session avatar with server-configured tags — is not
implemented. It remains a distinct target.

Claim anchors: [`RoutedApplication`](../../../src/kajenn/routed_application.py#RoutedApplication).

Behavior evidence: [`MonitorSection`](../../../src/kajenn/server_app/server_sections/monitor_section.py#MonitorSection), [`DatabasesSection`](../../../src/kajenn/server_app/server_sections/databases_section.py#DatabasesSection), [`UsersSection`](../../../src/kajenn/server_app/server_sections/users_section.py#UsersSection), [`TokensSection`](../../../src/kajenn/server_app/server_sections/tokens_section.py#TokensSection), [`TasksSection`](../../../src/kajenn/server_app/server_sections/tasks_section.py#TasksSection).

## Source and test evidence

- [src/kajenn/auth/core.py](../../../src/kajenn/auth/core.py)
- [src/kajenn/auth/mixin.py](../../../src/kajenn/auth/mixin.py)
- [src/kajenn/server_app/server_app.py](../../../src/kajenn/server_app/server_app.py)
- [src/kajenn/server_app/server_sections/auth_section.py](../../../src/kajenn/server_app/server_sections/auth_section.py)
- [src/kajenn/server_app/oidc_method.py](../../../src/kajenn/server_app/oidc_method.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/kbus_mixin.py](../../../src/kajenn/kbus_mixin.py)
- [tests/core/test_auth.py](../../../tests/core/test_auth.py)
- [tests/core/test_authentication_on_demand.py](../../../tests/core/test_authentication_on_demand.py)
- [tests/core/test_authentication_precedence.py](../../../tests/core/test_authentication_precedence.py)
- [tests/core/test_credential_validity.py](../../../tests/core/test_credential_validity.py)
- [tests/core/test_revocation_across_processes.py](../../../tests/core/test_revocation_across_processes.py)
- [tests/server_app/test_login_flow.py](../../../tests/server_app/test_login_flow.py)
- [tests/server_app/test_oidc.py](../../../tests/server_app/test_oidc.py)
- [tests/core/test_api_key_store.py](../../../tests/core/test_api_key_store.py)
