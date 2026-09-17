# Authentication — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Credential precedence

`AuthCore` verifies configured Basic, static Bearer and JWT credentials.
`gak_` API keys use the API-key store and never fall through to JWT on a miss.
A present invalid Authorization header raises `HTTPUnauthorized`; it does not
fall back to a logged-in session. `AuthMixin.authenticate` uses the session's
root avatar only when no Authorization header is supplied.

The server's local user and token stores are optional. Configured store
descriptors use storage nodes; a ready store can be supplied directly.
Bootstrap admin configuration upserts that account at boot. Password login and
OIDC methods live on `ServerApplication` and attach an avatar in place. OIDC
configuration requires a declared `external_url` at server construction.

Claim anchors: [`AuthCore`](../../../src/kajenn/auth/core.py#L79), [`AuthMixin`](../../../src/kajenn/auth/mixin.py#L67), [`authenticate`](../../../src/kajenn/auth/mixin.py#L151), [`authenticate`](../../../src/kajenn/auth/core.py#L160), [`ServerApplication`](../../../src/kajenn_server_app/server_app.py#L111).

## Authorization and hosted-site identity

`RoutedApplication` passes avatar tags to the auth plugin: unknown identity
receives 401, insufficient tags receive 403. The error middleware negotiates
the login challenge. The monitor declares `SERVER_ADMIN`; users, tokens and tasks declare
`SUPERADMIN`. Login endpoints are public.

The separately recorded return-path conversion — a hosted site's own identity
becoming a core session avatar with server-configured tags — is not
implemented. It remains a distinct target.

Claim anchors: [`RoutedApplication`](../../../src/kajenn/routed_application.py#L111).

Behavior evidence: [`MonitorSection`](../../../src/kajenn_server_app/server_sections/monitor_section.py#L74), [`UsersSection`](../../../src/kajenn_server_app/server_sections/users_section.py#L61), [`TokensSection`](../../../src/kajenn_server_app/server_sections/tokens_section.py#L57), [`TasksSection`](../../../src/kajenn_server_app/server_sections/tasks_section.py#L60).

## Source and test evidence

- [src/kajenn/auth/core.py](../../../src/kajenn/auth/core.py)
- [src/kajenn/auth/mixin.py](../../../src/kajenn/auth/mixin.py)
- [src/kajenn_server_app/server_app.py](../../../src/kajenn_server_app/server_app.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [tests/core/test_auth.py](../../../tests/core/test_auth.py)
- [tests/server_app/test_login_flow.py](../../../tests/server_app/test_login_flow.py)
- [tests/server_app/test_oidc.py](../../../tests/server_app/test_oidc.py)
- [tests/core/test_api_key_store.py](../../../tests/core/test_api_key_store.py)
