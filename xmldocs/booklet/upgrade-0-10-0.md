<!-- generated from upgrade-0-10-0.xml by xmldocs: do not edit, edit the XML -->

## Upgrading to kajenn 0.10.0: login with the _auth application, and what each application must change
*Instruction booklet · booklet/upgrade-0-10-0 · 2026-11-17*

### Purpose

kajenn 0.10.0 moves authentication out of `_server` into one application with code `_auth`. This booklet is for the developers of the applications that run on kajenn. It states what changed, how to set up login, a generic checklist, and the changes each known application needs.

Every fact carries its source. A source without a repository prefix is a path in the kajenn repository at tag `v0.10.0`. A source with a prefix (`kajenn-webrtc/`, `sourcerer_v2/`, `genropy-kajenn/`, `hetzner-kube/`) is a path in that repository, read on 2026-11-17.

### Requirements

- kajenn 0.10.0 (tag `v0.10.0`, on PyPI).
- A `storage_key` on the server: the `_auth` records are written encrypted.
- For OIDC login: `external_url` on the server.

#### 1. What changed

- Authentication belongs to the application with code `_auth`, class `AuthApplicationBase`. A server without it authenticates nobody.
- `_server` has no login, no users and no tokens.
- One store, `AuthStore`, with the file backend `FileAuthStore`. It holds users, groups, tag records, tokens and login failure counters.
- A user record has `status` (`waiting`, `confirmed`, `banned`), `groups`, `primary_group` and `system`. Only `confirmed` authenticates.
- A token belongs to a user. It carries `"*"` or a list of the user's tags.
- A tag must have a tag record. An application declares its tags in `declared_tags` and its program users in `system_users`. Other tags are created with `/_auth/tags/create_tag`.
- The management of users, groups, tags and tokens asks `AUTH_ADMIN`. The `_server` sections ask `SERVER_ADMIN`. `SUPERADMIN` is removed.
- The session keeps the identity and the group of an avatar. The tags are asked to `/_auth/avatar` at every request.
- `avatar.data` lasts one request. Data for the whole session goes in `session.data`.
- `external_url` is optional. It switches on the capability `external_links`. The OIDC routes require it.
- Data under `site:users` and `site:api_keys` is not read. At the first start the server log carries the password of `_temporary_admin`.
- Session dumps and snapshots of earlier versions cannot be loaded. Delete them before the first start.

- `READ` `CHANGELOG.md:12` · 2026-11-17 — A server that does not mount `_auth` authenticates nobody: a presented `Authorization` header answers 401 and a session gives no identity.
- `READ` `src/kajenn/auth_app/auth_app.py:277` · 2026-11-17 — `AuthApplicationBase` has `code` and `mount` equal to `AUTHENTICATION_CODE`, which is `"_auth"` (`src/kajenn/auth/mixin.py:71`).
- `READ` `CHANGELOG.md:18` · 2026-11-17 — The `_server` routes `login`, `logout`, `login_methods`, `users/...`, `tokens/...` and `auth/...` are removed.
- `READ` `CHANGELOG.md:55` · 2026-11-17 — `AuthCore`, `UserStore`, `FileUserStore`, `ApiKeyStore` and `FileApiKeyStore` are removed. `AuthStore` and `FileAuthStore` import from `kajenn`.
- `READ` `docs/guides/authentication.md:121` · 2026-11-17 — The store holds five kinds of record: user, group, tag, token, login failure counter.
- `READ` `CHANGELOG.md:103` · 2026-11-17 — The user record has `status` in place of `enabled`, and the fields `groups`, `primary_group`, `system`, `email`, `external_ids`. Only `confirmed` authenticates.
- `READ` `CHANGELOG.md:106` · 2026-11-17 — A token belongs to a user and carries `"*"` or a non-empty list of tags, a subset of the user's tags. `label` is removed.
- `READ` `CHANGELOG.md:113` · 2026-11-17 — The management refuses a tag that has no tag record. The tags an application uses are declared in `declared_tags`, or created with `/_auth/tags/create_tag`.
- `READ` `CHANGELOG.md:109` · 2026-11-17 — `SUPERADMIN` is removed. Users, groups, tags and tokens ask `AUTH_ADMIN`; the `_server` sections, tasks included, ask `SERVER_ADMIN`.
- `READ` `CHANGELOG.md:116` · 2026-11-17 — The session stores the identity and the group of an avatar, never its tags. At every request the server asks `/_auth/avatar` for the tags. A refused identity loses its session.
- `READ` `CHANGELOG.md:127` · 2026-11-17 — `avatar.data` lives for one request. Data that must last for the session is written in `session.data`.
- `READ` `src/kajenn/server_capabilities.py:53` · 2026-11-17 — The capability `external_links` is true when the server has an `external_url`.
- `READ` `src/kajenn/auth_app/oidc_method.py:108` · 2026-11-17 — `OidcMethod` declares `env_requires = "external_links"` on its `start` and `callback` routes.
- `READ` `CHANGELOG.md:98` · 2026-11-17 — `FileAuthStore` reads only `site:auth`. Records under `site:users` and `site:api_keys` are ignored. The server log carries the password of `_temporary_admin`.
- `READ` `CHANGELOG.md:121` · 2026-11-17 — A session dump or snapshot of an earlier version cannot be loaded. Whoever upgrades deletes the session dump and the snapshot file before the first start.

#### 2. Set up login

#### Mount _auth in the recipe

The recipe writes one `application` element with code `_auth`. The words under it configure the store, the lockout, the OIDC providers and the self registration.

**Example — Recipe from docs/guides/authentication.md**

```python
from genro_bag.resolvers import EnvResolver
from kajenn import AuthApplicationBase, FileAuthStore


def applications_section(self, cfg):
    """The authentication application: who keeps the users, the lockout, the external provider."""
    apps = cfg.applications()
    auth_app = apps.application(code="_auth", app_class=AuthApplicationBase, cache_ttl=60)
    auth_app.store(store_class=FileAuthStore, mount="site", prefix="auth")
    auth_app.login(max_attempts=3, backoff=10)
    auth_app.oidc().provider(
        code="google",
        issuer="https://accounts.example.com",
        client_id="client-123",
        client_secret=EnvResolver("GOOGLE_CLIENT_SECRET"),
    )
    auth_app.self_registration(status="waiting", tags=["shop_guest"])
```

- `cache_ttl`: seconds an answer of `_auth` stays cached. Default 300.
- `store(store_class, ...)`: without the word the store is `FileAuthStore` on `site:auth`.
- `login(max_attempts, backoff)`: defaults 5 attempts, 30 seconds.
- `oidc().provider(code, issuer, client_id, client_secret, scopes)`: one per provider. `scopes` defaults to `openid email profile`.
- `self_registration(status, tags)`: what happens to a provider user the store does not have.

- `READ` `docs/guides/authentication.md:84` · 2026-11-17 — The recipe above is the documented form of the `_auth` element.
- `READ` `src/kajenn/auth_app/auth_app.py:242` · 2026-11-17 — The `_auth` grammar has the words `store`, `login`, `oidc`, `provider` (code, issuer, client\_id, client\_secret, scopes) and `self_registration` (status, tags).
- `READ` `docs/guides/authentication.md:101` · 2026-11-17 — `cache_ttl` defaults to 300; the store defaults to `FileAuthStore` on `site:auth`; the lockout defaults to 5 attempts and 30 seconds.

#### The first administrator

An empty store has no administrator. At startup `_auth` writes `_temporary_admin` and logs its password. Enter with it and create a real user with `AUTH_ADMIN` (and `SERVER_ADMIN` for the `_server` sections). `_temporary_admin` is then deleted.

```text
application _auth: temporary administrator _temporary_admin, password <password>
```

- `READ` `src/kajenn/auth_app/auth_app.py:629` · 2026-11-17 — The log line is `application %s: temporary administrator %s, password %s`, at level warning.
- `READ` `docs/guides/authentication.md:418` · 2026-11-17 — When a user other than `_temporary_admin` holds `AUTH_ADMIN`, `_auth` deletes `_temporary_admin`. A restart while it still exists writes it again with a new password.

#### Password login

- `POST /_auth/login` with `{identity, password, group}`. `group` is optional.
- A refusal answers status 200 with `{"error": ...}`. Check the body.
- An `Authorization: Basic` header is checked on the same users.

- `READ` `docs/guides/authentication.md:224` · 2026-11-17 — `/_auth/login` answers `{session_id, identity, group, tags, groups}`, or 200 with `{"error": ...}`.

#### Replace a static bearer with a token of a system user

A fixed bearer of the recipe no longer exists. Declare a system user in the application. An administrator issues it a `gak_` token.

**Example — Declared tags and system user, from docs/guides/authentication.md**

```python
class Billing(RoutedApplication):
    @property
    def declared_tags(self) -> list:
        return [
            {"code": "bll_reports", "description": "Read the billing reports"},
            {"code": "bll_admin", "parent": "bll_reports"},
        ]

    @property
    def system_users(self) -> list:
        return [{"identity": "bll_robot", "status": "confirmed",
                 "tags": ["bll_reports"], "groups": []}]
```

```bash
curl -u "operator:$ADMIN_PASSWORD" -H 'Content-Type: application/json' \
  -d '{"user":"bll_robot","tags":["bll_reports"]}' \
  https://<host>/_auth/tokens/issue
```

The answer carries the `key` once. The client sends it as `Authorization: Bearer gak_...`.

- `READ` `CHANGELOG.md:50` · 2026-11-17 — A fixed bearer becomes a `gak_` token of a system user.
- `READ` `docs/guides/authentication.md:381` · 2026-11-17 — At startup `_auth` writes the declared tags and system users that do not exist. A system user gets `system` true and no token.
- `READ` `docs/guides/management.md:117` · 2026-11-17 — `/_auth/tokens/issue` takes `{user, tags, expires_at}`; the `key` is returned only once.

#### OIDC login

- The provider goes under the `_auth` element: `auth_app.oidc().provider(code, issuer, client_id, client_secret)`.
- Write `client_secret` as a resolver, for example `EnvResolver("...")`.
- The server needs `external_url`. The default template reads `KAJENN_EXTERNAL_URL`.
- Register `<external_url>/_auth/oidc:<code>/callback` as redirect URI with the provider.
- The login starts at `/_auth/oidc:<code>/start?next=...`.

How a provider user is found in the store:

1. The user whose `external_ids` holds the token's `iss` + `sub`.
2. Else, with `email` and `email_verified` true, the one user with that email. `{iss, sub}` is added to its `external_ids`.
3. Else `self_registration`: absent, the login is refused; present, a user is written with identity = the verified email.

The provider attributes `tags` and `identity_claim` are removed. The tags of a provider user come from its store record.

- `READ` `CHANGELOG.md:27` · 2026-11-17 — The OIDC routes move from `/_server/auth/oidc:<code>/...` to `/_auth/oidc:<code>/...`. The `redirect_uri` becomes `<external_url>/_auth/oidc:<code>/callback`.
- `READ` `CHANGELOG.md:38` · 2026-11-17 — `_server_app.oidc().provider(...)` becomes `auth_app.oidc().provider(...)`. The provider attributes `tags` and `identity_claim` are removed.
- `READ` `src/kajenn/auth_app/oidc_method.py:268` · 2026-11-17 — `link_user` looks up `iss` + `sub`, then the one user with the verified email, then calls `register_user`.
- `READ` `src/kajenn/config/templates.py:78` · 2026-11-17 — The default template writes `cfg.server(external_url=EnvResolver("KAJENN_EXTERNAL_URL", default=""))`.
- `READ` `src/kajenn/auth_app/auth_method.py:128` · 2026-11-17 — `login_methods` lists only the methods whose required capability is on: without `external_url` the OIDC providers are left out.

#### Replace a custom credential check

Subclass `AuthApplicationBase`. Override `verify_credential(credential)`. Mount the subclass with code `_auth`. The answer is `{identity, group, tags, data, valid_until}`, or `HTTPUnauthorized`.

```python
apps.application(code="_auth", app_class=SiteAuth)
```

A login method of a subclass is registered with `register_auth_method` on the `AuthApplicationBase` subclass.

- `READ` `src/kajenn/auth_app/auth_app.py:431` · 2026-11-17 — `verify_credential(credential)` receives the `Authorization` value and answers `{identity, group, tags, data, valid_until}`. It accepts `Basic` and `Bearer gak_...`.
- `READ` `src/kajenn/auth_app/auth_app.py:790` · 2026-11-17 — The route `/_auth/authenticate` receives `channel` and calls `self.verify_credential(credential)`: the channel does not reach `verify_credential`.
- `READ` `src/kajenn/auth_app/auth_app.py:415` · 2026-11-17 — `register_auth_method(method)` is a member of `AuthApplicationBase`.
- `READ` `docs/guides/authentication.md:518` · 2026-11-17 — The guide has a full `SiteAuth` example that overrides `verify_credential`.

#### 3. Upgrade checklist

*Old to new*

| Before 0.10.0 | 0.10.0 |
|---|---|
| No `_auth` in the recipe | `application(code="_auth", app_class=AuthApplicationBase)` |
| `cfg.authentication()` with `users`, `tokens`, `credentials`, `basic_user`, `bearer_token`, `jwt`, `cache_ttl` | `auth_app.store(store_class=...)`; `cache_ttl` as attribute of the `_auth` element |
| `cfg.channels().channel(name, authentication_route)` | Removed. Custom check: subclass with `verify_credential` |
| `bearer_token(identity, token, tags)` | `gak_` token of a system user (`/_auth/tokens/issue`) |
| JWT with a shared secret, `/_server/tokens/create_jwt` | Removed. A JWT issued elsewhere: `verify_credential` in a subclass |
| `_server_app.login(...)`, `_server_app.oidc().provider(...)` | `auth_app.login(...)`, `auth_app.oidc().provider(...)` |
| Provider attributes `tags`, `identity_claim` | Removed. Tags from the store record |
| `/_server/login`, `/_server/logout`, `/_server/login_methods` | `/_auth/login`, `/_auth/logout`, `/_auth/login_methods` |
| `/_server/users/...`, `/_server/tokens/...` | `/_auth/users/...`, `/_auth/tokens/...`; new `/_auth/groups/...`, `/_auth/tags/...` |
| `/_server/auth/oidc:<code>/start`, `/callback` | `/_auth/oidc:<code>/start`, `/callback` |
| `/_server/auth/authenticate`, `/_server/auth/forget_credential` | `/_auth/authenticate`, `/_auth/forget_credential` |
| `from kajenn.server_app import AuthMethod, PasswordMethod, OidcMethod, safe_next_path` | `from kajenn.auth_app import ...`; `OidcMethod.get_safe_next_path` |
| `AuthCore`, `UserStore`, `ApiKeyStore` and their file classes | `AuthStore`, `FileAuthStore` from `kajenn` |
| `server.user_store`, `server.api_key_store`, `server.auth_core`, `server.channels` | `server.applications["_auth"].store` |
| `AsgiServer(credential_cache_ttl=...)` | `cache_ttl` of the `_auth` application |
| `ServerApplication.register_auth_method`, `login_policy`, `oidc_providers` | Same names on a subclass of `AuthApplicationBase` |
| `SUPERADMIN` | `AUTH_ADMIN`; `SERVER_ADMIN` for tasks |
| Any tag written in a rule | A tag record: `declared_tags` or `/_auth/tags/create_tag` |
| User field `enabled` | `status`: `waiting`, `confirmed`, `banned` |
| Token field `label` | Removed. Token has `user` |
| Authentication answer `{identity, tags, ...}` | `{identity, group, tags, ...}` |
| `avatar.data` for the session | `session.data` |
| Users in `site:users`, keys in `site:api_keys` | Not read. Create them again with `_temporary_admin` |
| Session dump and snapshot files | Delete before the first start |

- `READ` `CHANGELOG.md:31` · 2026-11-17 — The recipe elements `authentication` and `channels` are removed. A recipe that still writes them fails with the error of the grammar.
- `READ` `CHANGELOG.md:64` · 2026-11-17 — `AsgiServer(..., credential_cache_ttl=5)` raises `TypeError`.
- `READ` `CHANGELOG.md:70` · 2026-11-17 — The server members `auth_core`, `user_store`, `api_key_store`, `channels` and `authentication_route(channel)` are removed.
- `READ` `CHANGELOG.md:82` · 2026-11-17 — `ServerApplication` loses `login_policy`, `oidc_providers`, `auth_section`, `register_auth_method` and `read_declared_login_surface`. That code moves to a subclass of `AuthApplicationBase`.
- `READ` `CHANGELOG.md:94` · 2026-11-17 — `AuthMethod`, `PasswordMethod`, `OidcMethod` and `safe_next_path` are removed from `kajenn.server_app`. `OidcMethod.get_safe_next_path` replaces `safe_next_path`.
- `READ` `CHANGELOG.md:129` · 2026-11-17 — A custom authentication answer adds `group`.

#### 4. Application by application

#### meetlot (kajenn-webrtc)

- `pyproject.toml` pins `kajenn>=0.9.2,<0.10`. Raise it.
- `site.py`: the static `bearer_token` of identity `webrtc`, tag `rooms`, becomes a `gak_` token of a system user.
- `site.py`: the provider `keycloak` moves from the `_server` element to the `_auth` element. `identity_claim='sub'` is removed. `external_url` stays.
- `auth.py`: the imports from `kajenn.server_app` move to `kajenn.auth_app`. `safe_next_path` becomes `OidcMethod.get_safe_next_path`.
- `auth.py`: `MeetlotServer(ServerApplication).register_auth_method` moves to a subclass of `AuthApplicationBase`.
- `auth.py`: `KeycloakMethod.callback` attaches `Avatar(subject, tags)`. In 0.10.0 the session identity must be a user of the `_auth` store.
- `auth.py`: `avatar_from_claims` writes `expires_at`, `name`, `email` in `avatar.data`. `require()` and `Account.me` read them on later requests. In 0.10.0 they are gone after the login request.
- `auth.py`: the tags `meetlot_host` and `meetlot_admin` come from the Keycloak groups. How groups become tags is an open decision (see the decisions).
- The path `/_server/auth/oidc:keycloak/...` becomes `/_auth/oidc:keycloak/...` in `auth.py`, `welcome.html` and `tests/test_auth.py`. `application.py` and `pages/call.py` use it through `LOGIN`.
- Tags to declare: `meetlot_host`, `meetlot_admin`, `rooms`.
- Keycloak (realm `softwell`, client `meetlot`): the redirect URI becomes `https://meetlot.genro.com/_auth/oidc:keycloak/callback`, in `deploy/keycloak-meetlot.json` and in Keycloak.
- Point to check: `deploy/meetlot.yaml` has `readOnlyRootFilesystem: true` and mounts only `/tmp`. `FileAuthStore` writes records and needs writable persistent storage.

- `READ` `kajenn-webrtc/pyproject.toml:19` · 2026-11-17 — The dependency is `"kajenn>=0.9.2,<0.10"`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/site.py:49` · 2026-11-17 — `cfg.authentication().credentials().bearer_token(identity="webrtc", token=EnvResolver(ACCESS_CODE_VARIABLE), tags=ROOMS_TAG)`; `ROOMS_TAG` is `"rooms"` (`kajenn-webrtc/src/kajenn_webrtc/access.py:14`).
- `READ` `kajenn-webrtc/src/kajenn_webrtc/site.py:56` · 2026-11-17 — `apps.application(code='_server', app_class=MeetlotServer)` with `server.oidc().provider(code='keycloak', ..., scopes='openid email profile groups', identity_claim='sub')`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/site.py:43` · 2026-11-17 — `cfg.server(external_url=self.public_url)`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:11` · 2026-11-17 — Imports `ServerApplication` from `kajenn.server_app`, `safe_next_path` from `kajenn.server_app.auth_method`, `OidcMethod` from `kajenn.server_app.oidc_method`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:18` · 2026-11-17 — `LOGIN = '/_server/auth/oidc:keycloak/start?next=/call'`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:26` · 2026-11-17 — `require()` refuses when `identity.data.get('expires_at', 0) <= time.time()`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:40` · 2026-11-17 — `avatar_from_claims` gives `meetlot_host` to every user and `meetlot_admin` to members of `KEYCLOAK_ADMIN_GROUP`; it builds `Avatar(subject, tags)` and writes `expires_at`, `name`, `email` in `avatar.data`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:97` · 2026-11-17 — `KeycloakMethod.callback` calls `session.attach_avatar(avatar_from_claims(claims))` and redirects to `safe_next_path(target)`.
- `READ` `CHANGELOG.md:119` · 2026-11-17 — A handler that attaches an `Avatar` to a session attaches an identity of the `_auth` store.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:103` · 2026-11-17 — `MeetlotServer(ServerApplication)` overrides `register_auth_method` to replace the `keycloak` method with `KeycloakMethod`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/auth.py:116` · 2026-11-17 — `Account.me` reads `identity.data.get('name', ...)`.
- `READ` `kajenn-webrtc/src/kajenn_webrtc/welcome.html:3` · 2026-11-17 — Two links point to `/_server/auth/oidc:keycloak/start?next=/call` (lines 3 and 4).
- `READ` `kajenn-webrtc/tests/test_auth.py:160` · 2026-11-17 — The tests call `/_server/auth/oidc:keycloak/start` and `/callback` (lines 160, 165, 173).
- `READ` `kajenn-webrtc/src/kajenn_webrtc/application.py:16` · 2026-11-17 — `application.py` imports `LOGIN` and redirects to it (lines 62, 68). `pages/call.py:18` imports it too.
- `READ` `kajenn-webrtc/deploy/keycloak-meetlot.json:13` · 2026-11-17 — `"redirectUris": ["https://meetlot.genro.com/_server/auth/oidc:keycloak/callback"]`.
- `READ` `kajenn-webrtc/deploy/meetlot.yaml:55` · 2026-11-17 — `KEYCLOAK_ISSUER` is `https://auth.gpkube.genro.dev/realms/softwell`; `KEYCLOAK_CLIENT_ID` is `meetlot` (line 56).
- `READ` `kajenn-webrtc/deploy/meetlot.yaml:67` · 2026-11-17 — The container has `readOnlyRootFilesystem: true`; the only volume mount is `/tmp`, an `emptyDir` (lines 68, 73).

#### sourcerer v2

- It pins `kajenn==0.5.0`.
- Its Keycloak login goes through genropy `gnrauth:oidc`, not kajenn. No Keycloak change.
- `config.py` writes `cfg.channels()` with `authentication_route="/api/auth/authenticate"`. The element is removed. The logic of `AuthTools.authenticate` moves to a subclass of `AuthApplicationBase` mounted as `_auth`, and the answer adds `group`.
- `AuthTools.authenticate` branches on `channel` (telegram). `verify_credential` receives no channel.
- Other breaks between 0.5 and 0.10 are not about authentication: `McpOpenApiApplication` is removed in 0.9.0; the channel `rest` is renamed `api` in 0.9.0. See the 0.9.x sections of `CHANGELOG.md`.

The statement on genropy `gnrauth:oidc` comes from the survey of 2026-11-17; this booklet carries no source line for it.

- `READ` `sourcerer_v2/docker_v2/requirements_v2.txt:166` · 2026-11-17 — `kajenn==0.5.0`.
- `READ` `hetzner-kube/apps/sourcerer/sourcerer-v2.yaml:35` · 2026-11-17 — Started as `gnrkajenn sourcerer --config src/sourcerer_core/tools/config.py`.
- `READ` `sourcerer_v2/src/sourcerer_core/tools/config.py:54` · 2026-11-17 — `cfg.channels()` with `channel(name=name, authentication_route=AUTHENTICATION_ROUTE)` for `rest`, `mcp`, `telegram`; `AUTHENTICATION_ROUTE = "/api/auth/authenticate"` (line 41).
- `READ` `sourcerer_v2/src/sourcerer_core/tools/auth.py:72` · 2026-11-17 — `AuthTools.authenticate(credential, channel)` answers `{identity, tags, data}`, without `group`; for the telegram channel it calls `telegram_avatar` (line 84).
- `READ` `sourcerer_v2/src/sourcerer_core/tools/application.py:25` · 2026-11-17 — `from kajenn.applications.mcp import McpOpenApiApplication`.
- `READ` `CHANGELOG.md:222` · 2026-11-17 — In 0.9.0 `McpOpenApiApplication` is removed and the channel `rest` is renamed `api`.

#### sourcerer v1

It does not use kajenn. No change.

#### kajenn-orchestra

PR #44 (Prometheus families of the SPA pool) is merged in `develop` on 2026-11-17. It raises the dependency to `kajenn>=0.10.0`. Its tests mount `AuthApplicationBase` and read `GET /_server/metrics` with a `gak_` token of a user of `_auth`. The latest release is still 0.7.0.

- `RUN` `gh pr view 44 -R kajenn-org/kajenn-orchestra` · 2026-11-17 — State `MERGED` at 2026-11-17T15:28:49Z, base `develop`.
- `RUN` `gh pr diff 44 -R kajenn-org/kajenn-orchestra` · 2026-11-17 — `pyproject.toml` changes `kajenn>=0.9.0` to `kajenn>=0.10.0`; `tests/orchestration/test_orchestration_prometheus_metrics.py` reads `/_server/metrics` with a token of `_auth`.
- `RUN` `gh release list -R kajenn-org/kajenn-orchestra` · 2026-11-17 — The latest release is `v0.7.0` (2026-11-13).

#### genropy-kajenn

`GnrAppWrapper.authenticate` is used as `authentication_route`. Its answer has no `group`. How genropy-kajenn authenticates after 0.10.0 is an open decision of the owner (see the decisions).

- `READ` `genropy-kajenn/src/genropy_kajenn/gnrapp_wrapper/gnrapp_wrapper.py:89` · 2026-11-17 — `authenticate(credential, channel)` verifies a `Bearer` on `adm.api_token` and answers `{identity, tags, data, valid_until}` (line 123).
- `READ` `genropy-kajenn/pyproject.toml:29` · 2026-11-17 — The dependency is `"kajenn>=0.9.0"`.

#### 5. Open decisions and known issues

The open decisions are listed at the end of this booklet. They have no proposal yet. The known issue is listed under limits.

### Current limits

- A WSX connection keeps the avatar of its handshake. · `kajenn-org/kajenn#155`

### Decisions

- **d-oidc-groups** `open` — How do the groups of an OIDC provider become tags of a user: in kajenn through a recipe rule, or in the application through a subclass? meetlot depends on it.
- **d-genropy-kajenn-auth** `open` — How does genropy-kajenn authenticate its `adm.api_token` credentials after 0.10.0?
