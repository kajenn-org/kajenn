# Authentication — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.
Authentication passages updated against `dfc3941`.

## Credential precedence and application boundary

`AuthMixin.authenticate` checks a presented `Authorization` header first.
An invalid credential raises `HTTPUnauthorized`, without falling back to the
session. Without a header, the server asks `/_auth/avatar` for the session's
root identity and group. A 401/404 answer deletes the session and returns `None`.
Without a root avatar or an authentication application, it returns `None`.
A scope without `kajenn.channel` uses `api`.

The site must mount `AuthApplicationBase` with code `_auth`. The server does
not add it. `authenticate_credential` calls `/_auth/authenticate` over the
bus with the credential and channel. The base application does not use the
channel. Its answer contains `identity`, `group`, `tags`, `data` and
`valid_until`. The authenticate, avatar and forget-credential routes refuse
HTTP callers with 403. A missing authentication application or a 401/404
answer produces 401 with `WWW-Authenticate: Bearer`; an unreachable or failing
service produces 503.

Claim anchors: [`authenticate`](../../../src/kajenn/auth/mixin.py#AuthMixin.authenticate), [`authenticate_credential`](../../../src/kajenn/auth/mixin.py#AuthMixin.authenticate_credential), [`AuthApplicationBase`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase).

## Cached answers

The cache holds answers, not shared avatar objects. Credential keys are
`("credential", sha256(credential), channel)`; session keys are
`("session", identity, group or "")`. Each request receives a new avatar.
The TTL comes from the mounted application's `cache_ttl`, default 300 seconds;
0 disables caching. A dedicated application process reads the `_auth` recipe
entry instead. `valid_until` caps the lifetime. Failures are not cached.

`forget_credential` evicts that credential on every channel and all session
entries. `forget_all_credentials` clears the cache. Revoking or deleting a
token clears the whole cache through `forget_credentials_everywhere`.
`/_auth/forget_credential` calls that method with one credential. It evicts in
the server and every joined process; a process that cannot confirm is
relaunched.

Claim anchors: [`_ask_avatar`](../../../src/kajenn/auth/mixin.py#AuthMixin._ask_avatar), [`forget_credential`](../../../src/kajenn/auth/mixin.py#AuthMixin.forget_credential), [`forget_credentials_everywhere`](../../../src/kajenn/kbus_mixin.py#KBusMixin.forget_credentials_everywhere).

## Store, configuration and login

`AuthStore` holds users, groups, tags, tokens and login failures. `FileAuthStore` writes one
encrypted JSON file per record under `site:auth` by default. The application
builds the store on first use with `store_class(server.storage, **params)`.
Its grammar provides `store`, `login`, `oidc`/`provider` and
`self_registration`. `cache_ttl` is an application attribute.

`verify_credential` accepts Basic passwords and Bearer `gak_` tokens.
Password checks and `/_auth/login` share a separate `login_failures` record
keyed by identity, containing `failed_attempts` and `last_failed_at`. Login
never writes the user record. User management saves leave counters unchanged;
`/_auth/users/delete` also deletes the counter record.
After 5 failures by default, the backoff is
`30 * 2**(failed_attempts - 5)` seconds. The `login` word changes both defaults.
Success deletes the counter record; attempts during lockout leave it unchanged.
A password requires a confirmed, non-system user.

Login attaches the avatar to the existing session without changing its id.
The selected group defaults to the user's primary group. `/_auth/logout`
deletes the supplied session id. `/_auth/login_methods` returns public
method descriptors. At startup, `_auth` creates declared records and, when
no confirmed non-system administrator other than `_temporary_admin` exists,
writes `_temporary_admin` with `AUTH_ADMIN` and logs its new password. User and group management remove that
account and clear cached answers once a real administrator exists.

Claim anchors: [`AuthStore`](../../../src/kajenn/auth_app/auth_store.py#AuthStore), [`FileAuthStore`](../../../src/kajenn/auth_app/auth_store.py#FileAuthStore), [`build_store`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase.build_store), [`AuthApplicationGrammar`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationGrammar), [`verify_credential`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase.verify_credential), [`login`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase.login), [`refresh_temporary_admin`](../../../src/kajenn/auth_app/auth_app.py#AuthApplicationBase.refresh_temporary_admin).

## OIDC

Each configured provider registers `oidc:<code>` on `_auth`, with `start`
and `callback` routes under `/_auth/oidc:<code>/`. A provider requires the
server's `external_url`: both routes declare `env_requires="external_links"`,
so on a server without `external_url` the server boots and the routes are not
available (`404`). Discovery is lazy; authorization uses PKCE S256.
The callback finds a user by verified `iss` and `sub`, then by verified email,
or registers one under `self_registration`. It refuses a user that is not
confirmed or is a system user. Tags and the primary group come from the
store, not the provider configuration.

Claim anchors: [`authenticate_claims`](../../../src/kajenn/auth_app/oidc_method.py#OidcMethod.authenticate_claims), [`link_user`](../../../src/kajenn/auth_app/oidc_method.py#OidcMethod.link_user).

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
does not negotiate a login page (errors.py:128-131). The monitor,
databases and tasks sections declare `SERVER_ADMIN`; users, groups, tags and
tokens on `_auth` declare `AUTH_ADMIN`. Login endpoints are public.

The separately recorded return-path conversion — a hosted site's own identity
becoming a core session avatar with server-configured tags — is not
implemented. It remains a distinct target.

Claim anchors: [`RoutedApplication`](../../../src/kajenn/routed_application.py#RoutedApplication).

Behavior evidence: [`MonitorSection`](../../../src/kajenn/server_app/server_sections/monitor_section.py#MonitorSection), [`DatabasesSection`](../../../src/kajenn/server_app/server_sections/databases_section.py#DatabasesSection), [`TasksSection`](../../../src/kajenn/server_app/server_sections/tasks_section.py#TasksSection).

## Source and test evidence

- [src/kajenn/auth/mixin.py](../../../src/kajenn/auth/mixin.py)
- [src/kajenn/server_app/server_app.py](../../../src/kajenn/server_app/server_app.py)
- [src/kajenn/auth_app/oidc_method.py](../../../src/kajenn/auth_app/oidc_method.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/kbus_mixin.py](../../../src/kajenn/kbus_mixin.py)
- [tests/core/test_auth.py](../../../tests/core/test_auth.py)
- [tests/core/test_authentication_on_demand.py](../../../tests/core/test_authentication_on_demand.py)
- [tests/core/test_authentication_precedence.py](../../../tests/core/test_authentication_precedence.py)
- [tests/core/test_credential_validity.py](../../../tests/core/test_credential_validity.py)
- [tests/core/test_revocation_across_processes.py](../../../tests/core/test_revocation_across_processes.py)
- [tests/auth_app/test_oidc.py](../../../tests/auth_app/test_oidc.py)
