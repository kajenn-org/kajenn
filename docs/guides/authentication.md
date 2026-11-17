# Authentication

## What it does

Authentication belongs to one application: `AuthApplicationBase`, mounted with
the code `_auth`. It holds the users, groups, tags and tokens of the site,
verifies a password or a token, and answers the server when it asks who the
caller of a request is. The server filters routes by the tags carried on the
caller's `Avatar`.

A server that does not mount `_auth` does not authenticate. A presented
credential answers `401`, and a session gives no identity. The server
application `_server` has no login, no users and no tokens.

## When to use it

When some routes must be restricted to authenticated callers, or to callers
with a particular tag. Mount `AuthApplicationBase` and mark the protected
routes with `auth_rule`.

## Setup

`AuthApplicationBase` mounts as is, with the code and the mount `_auth`. The
server never adds it on its own: a site declares it.

```python
import os

from kajenn import AsgiServer, AuthApplicationBase, RoutedApplication
from genro_routes import route


class App(RoutedApplication):
    mount = ""

    @property
    def declared_tags(self) -> list:
        return [
            {"code": "shop_admin", "description": "Read the secret page"},
            {"code": "shop_clerk", "description": "Serve the customers"},
        ]

    @route()
    def public(self) -> dict:
        return {"open": True}

    @route(auth_rule="shop_admin")
    def secret(self) -> dict:
        return {"classified": True}


server = AsgiServer(
    applications=[AuthApplicationBase, App],
    storage_key=os.environ["DEMO_STORAGE_KEY"],
)
store = server.applications["_auth"].store
for identity, password, tags in (("alice", "wonderland", ["shop_admin"]),
                                 ("bob", "builder", ["shop_clerk"])):
    store.save_user({
        "identity": identity, "groups": [], "primary_group": None, "tags": tags,
        "status": "confirmed", "system": False,
        "password_hash": store.hash_password(password),
        "email": None, "external_ids": [],
    })
server.serve(host="127.0.0.1", port=8000)
```

The example writes its two users directly through the store, before the
server starts. A running site creates users through the
[management routes](management.md). The records are written encrypted, so the
server needs a `storage_key` (see [storage encryption](storage.md#optional-encryption)).
This site has no user with `AUTH_ADMIN`: at startup the server log carries the
password of the [temporary administrator](#the-first-access-to-an-empty-site).

In a recipe the application is one `application` element with its own words,
written in the recipe's `applications_section` (see
[configuration](configuration.md)):

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

The recipe writes `code="_auth"`. The words:

- `cache_ttl` — an attribute of the `application` element: the seconds the
  server keeps an answer of `_auth` cached. Default `300`; `0` disables the
  cache.
- `store(store_class, ...)` — the store of the records. Without `store_class`
  the store is `FileAuthStore`; the other attributes are the kwargs of the
  store class. Without the word the store is `FileAuthStore` on `site:auth`.
- `login(max_attempts, backoff)` — the lockout policy. Defaults: 5 attempts,
  30 seconds.
- `oidc()` with one `provider(code, issuer, client_id, client_secret, scopes)`
  per external provider. `client_secret` is optional and is written as a
  resolver. `scopes` defaults to `openid email profile`. See [OIDC](#oidc).
- `self_registration(status, tags)` — what happens to a user an external
  provider recognises and the store does not have. See [OIDC](#oidc).

A hand-built server passes the same values as kwargs of the application:
`(AuthApplicationBase, {"cache_ttl": 60, "store": {...}, "login": {...},
"oidc": {code: {...}}, "self_registration": {...}})`.

## The records

The store holds five kinds of record.

A **user**:

| Field | Meaning |
| --- | --- |
| `identity` | the name the user enters with |
| `groups` | the codes of the groups of the user |
| `primary_group` | one of `groups`, or `None` |
| `tags` | the user's own tags |
| `status` | `waiting`, `confirmed` or `banned`; only `confirmed` authenticates |
| `system` | `true` for a program: it never verifies a password and enters only with a token |
| `password_hash` | the scrypt hash of the password, or `None`: the user does not enter with a password |
| `email` | the email address, or `None` |
| `external_ids` | a list of `{iss, sub}`, one per external provider the user is linked to |

`waiting` is a user that waits for approval. `banned` is a user an
administrator switched off. Neither enters. The store refuses a user record
with another `status`, and one whose `primary_group` is not one of its
`groups`.

A **group**: `{code, tags}`. A group that a user has among its `groups`
cannot be deleted.

A **tag**: `{code, description, owner, reserved, parent}`. `owner` is the code
of the application that declared the tag, empty when the tag was created
through the management. `parent` is optional: the tree groups tags and grants
nothing. Holding a parent tag does not give its children.

A **token**: `{key_id, user, tags, expires_at, enabled, secret_hash}`. A token
always belongs to a user. `tags` is `"*"` or a non-empty list. `expires_at` is
a POSIX timestamp, or `None` for a token that never expires. The key is
`gak_<key_id>_<secret>`; the store keeps only the hash of the secret.

A **login failure counter**: `{identity, failed_attempts, last_failed_at}`.
Password login writes this separate record and deletes it on success.

### The store

`AuthStore` is the contract of the store, and `FileAuthStore` is its file
backend. `FileAuthStore` writes one encrypted JSON file per record, at
`<mount>:<prefix>/<kind>/<key>.json`, with `kind` one of `users`, `groups`,
`tags`, `tokens`, `login_failures`. The default is `site:auth`.

The rules live in `AuthStore`: the tags of a user in a group, the password
hash and its check, the issue and the verification of a token. A backend
implements only persistence:

- `user_list`, `group_list`, `tag_list`, `token_list` — properties, every
  record of a kind;
- `get_user(identity)`, `get_group(code)`, `get_tag(code)`, `get_token(key_id)`
  — one record, or `None`;
- `write_user(record)` — persist a user record. `AuthStore.save_user` checks
  the record first, then calls it;
- `save_group(record)`, `save_tag(record)`, `save_token(record)`;
- `delete_user`, `delete_group`, `delete_tag`, `delete_token` — `True` when a
  record was removed. `delete_user` also deletes every token whose `user` is
  that identity and its `login_failures` record. `delete_group` raises `ValueError` while a user has the
  group among its `groups`. Each backend enforces both constraints with its
  own means;
- `get_login_failures(identity)`, `save_login_failures(record)`,
  `delete_login_failures(identity)` — read, write and remove a login counter.

`get_user_by_external_id(iss, sub)` and `get_user_list_by_email(email)` have a
body in `AuthStore`, over `user_list`. A database backend may rewrite them with
a query. A recipe names the backend with `store(store_class=...)`; the
application builds `store_class(server.storage, **params)` on first use.

### Data of earlier versions

`FileAuthStore` reads only its own files. The user and key stores written by
earlier versions of kajenn, under `site:users` and `site:api_keys`, are not
read and not converted. A site starts with an empty store and enters through
the [temporary administrator](#the-first-access-to-an-empty-site).

## The tags of a request

The tags of a user are its own tags plus the tags of one group:

- the group chosen at login;
- else the user's `primary_group`;
- else none: only the user's own tags.

A group listed on the user without a group record adds no tags.

A token carries a subset of the user's tags, computed again at every
verification:

- `"*"`: the user's own tags plus those of its primary group;
- a list: only the listed tags the user holds, as its own or through any of
  its groups. The others are dropped without an error.

A token is refused when it is expired, revoked, deleted, or when its user is
absent or does not have the status `confirmed`.

The caller of a request is an `Avatar` with four fields: `identity`, `group`
(the active group, or `None`), `tags`, and `data`. For a token and a `Basic`
header `group` is the primary group of the user.

## The routes of `_auth`

Called by a client:

- `POST /_auth/login` with `{"identity", "password", "group"}`. `group` is
  optional: empty means the primary group. Success attaches the avatar to the
  request's **existing** session, so the session id does not change, and
  answers `{session_id, identity, group, tags, groups}`; `groups` lists every
  group of the user. A refusal answers `200` with `{"error": ...}` and
  attaches nothing: a missing identity or password, wrong credentials, an
  active lockout, a group the user does not belong to.
- `POST /_auth/logout?session_id=...` — deletes that session; answers
  `{"status": "ok"}`. Without `session_id` no session is deleted.
- `GET /_auth/login_methods` — public: `{"methods": [descriptor, ...]}`. The
  password method comes first (`{"id": "password", "kind": "form", "label",
  "action": "/_auth/login"}`), then one `redirect` method per OIDC provider.

All of them answer JSON; `_auth` serves no HTML login page.

Management, every route asking `AUTH_ADMIN`: `/_auth/users/...`,
`/_auth/groups/...`, `/_auth/tags/...`, `/_auth/tokens/...`. The
[management guide](management.md) walks through them.

Called by the server, reachable only through the KajennBus (an HTTP caller
gets `403`):

- `POST /_auth/authenticate` with `{credential, channel}` — verifies an
  `Authorization` value and answers `{identity, group, tags, data,
  valid_until}`, or `401`.
- `POST /_auth/avatar` with `{identity, group}` — the same answer for the
  identity and the group a session remembers. An empty `group` means the
  primary group. An unknown identity, a status other than `confirmed` and a
  group the user does not belong to answer `401`.
- `POST /_auth/forget_credential` with `{credential}` — empties the cached
  answers of that credential in the server and in every joined process.

`valid_until` is the `expires_at` of a token, or `None`. A cached answer never
outlives it.

### Credentials

`_auth` accepts two credentials:

- a password, through `/_auth/login` or an `Authorization: Basic` header;
- a `gak_` token, as `Authorization: Bearer gak_...`.

Any other `Authorization` value answers `401`. A `system` user never enters
with a password.

### The lockout

A password check through `/_auth/login` and through a `Basic` header share one
counter in a separate `login_failures` record keyed by identity
(`identity`, `failed_attempts`, `last_failed_at`). Login never writes the user
record. After `max_attempts` consecutive failures the identity is refused for
`backoff * 2**(failed_attempts - max_attempts)` seconds from the last failure.
A refused attempt during the lockout does not touch the counter; a success
deletes it. An identity without a user record has no counter. Deleting a user
(`delete_user`, also through `/_auth/users/delete`) deletes its counter.

A failed login answers `{"error": "Invalid credentials", "remaining_attempts":
n}` for a known identity; a login during the lockout answers
`{"error": "Too many failed attempts"}`. A `Basic` header answers `401` in
both cases.

## Authentication on demand

`RoutedApplication.execute` is the one execution point of REST, MCP, WSX and
the KajennBus. For each request it authenticates first, then resolves the route
with the identity it found:

- A credential in the `Authorization` header, when presented, is verified by
  `/_auth/authenticate`. It is always verified, session or not. The channel of
  the request (`scope["kajenn.channel"]`) travels with it; `AuthApplicationBase`
  does not read it.
- Else, when the session has a root avatar, the server asks `/_auth/avatar`
  for its identity and group.
- No credential is not an error: the route's `auth_rule` decides, and a ruled
  route answers a bare `401` with `WWW-Authenticate: Bearer`.
- An invalid credential is always `401`.

MCP `arguments` are the handler's keyword arguments, never a body: a handler
declaring `body_data` receives an argument named `body_data`, not the whole
arguments dict. REST keeps the JSON body whole as `body_data`.

The answers of `/_auth/authenticate` and `/_auth/avatar` share one cache in
each process, kept `cache_ttl` seconds. The server reads `cache_ttl` from the
mounted `_auth` application. The dedicated process of an application with a
`spawner` does not mount `_auth`: it reads the `cache_ttl` attribute of the
`_auth` element of its own recipe, and uses 300 seconds when the recipe has
none. Its questions go to the server process over the bus.

A change of the status, the tags or the groups of a user reaches a live session
and a token within `cache_ttl`. It reaches them at once when the cache is
emptied. A successful write of the `users` or `groups` management section,
revoking or deleting a token, and
`await server.forget_credentials_everywhere()` without an argument, empty the
whole cache. `/_auth/forget_credential` empties the entries of the credential
it names and the entries of every session; the other tokens of the same user
keep their cached answer until `cache_ttl`. Each of these empties the cache of
the server and of every joined process before it returns. From then on no new
request, served by the server or by an application in its own process, accepts
the forgotten credential; a request already past authentication is not interrupted. A
process that joins later starts with an empty cache.

When `/_auth/avatar` refuses the identity of a session (a user deleted, banned,
or taken out of the group it entered with) the session is deleted, with its
`session.data`, and the request is anonymous.

Every request gets its own `Avatar` object. What a handler writes on
`avatar.data` lives for that request only. Data that must last for the session
lives in `session.data`.

`server.authenticate(scope)` is `async`. A caller awaits it; a pool thread
uses `server.run_on_loop(server.authenticate(scope))`.

## Minimal snippet

Protecting a single route:

```python
@route(auth_rule="shop_admin")
def secret(self) -> dict:
    return {"classified": True}
```

`auth_rule="shop_admin"` means the caller's avatar must carry the `shop_admin`
tag. Protection is **default-deny**: an anonymous caller receives `401`, also on
a server that does not mount `_auth`; a known caller without the required tag
receives `403`, so a protected endpoint is never accidentally open.

## Tags of the applications

An application declares the tags its routes ask for, and the system users it
needs, with two properties of `BaseApplication`. Both are empty on the base:

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

    @route(auth_rule="bll_reports")
    def reports(self) -> dict:
        return {"reports": []}
```

The author chooses a namespace for the tags of the application and writes the
full tag name everywhere: in `declared_tags`, in `auth_rule` and in the tags of
a system user. kajenn adds no prefix. A tag entry has `code` and, optionally,
`description`, `reserved` and `parent`. A system user is a complete user
record: `identity`, `status`, `tags`, `groups`.

At startup `_auth` reads the two properties of every application of the
server, itself included, and writes the records that do not exist. It does the
same for an application added to the running server, and for an application
with a `spawner` when its process joins. A tag record gets `owner` = the code of
the application. A system user gets `system` true and no token: it enters only
after an administrator issues it a token. A record that already exists is left
as it is. A record whose declaration disappears stays.

`_auth` declares `AUTH_ADMIN`. `_server` declares `SERVER_ADMIN` and
`SERVER_METRICS`.

The management refuses a tag that has no record: a user, a group or a token
that names one is not written. A recipe can change the tag a route asks for,
per installation, with `plugin_configuration` (see
[configuration](configuration.md)); the rule names the tag by its full name.

Warnings in the server log, starting `application _auth:`:

- `tag not written of '<app>': '<code>' already exists with owner '<other>'` —
  two applications declare the same tag name: the same namespace chosen by two
  authors, the same application mounted twice, or a tag created by hand with
  that name. The record stays as it is and the two applications share the tag.
- `tag not written of '<app>': a rule cannot hold the code '<code>'` — the code
  is `and`, `or`, `not`, or holds a character other than letters, digits and
  `_`. No route could ask for it.
- `tag not written` or `system user not written` with `bad ...` — an entry with
  a wrong type. The other entries are written.
- `declared records not written of '<app>': ...` — the store cannot be built or
  written (for example no `storage_key`). `_auth` stays active; the records
  are written at the first startup with a working store.

An application with a `spawner` whose `joined` call carries declarations that
are not a list of dicts is refused: it does not join and its routes answer
`503`.

## The first access to an empty site

A user needs `AUTH_ADMIN` to create a user. On a site where no user has it,
`_auth` writes a temporary administrator at startup:

- the identity `_temporary_admin`, with tags `["AUTH_ADMIN"]`, status
  `confirmed`, no group;
- a password generated at every startup, written once in the server log:

```text
application _auth: temporary administrator _temporary_admin, password <password>
```

Whoever installs the site enters with that password and creates a real
administrator. An administrator is a user other than `_temporary_admin`, with
status `confirmed` and `system` false, that holds `AUTH_ADMIN` among its own
tags or among the tags of any of its groups. After every write of the users and
groups management, `_auth` looks for one. When it exists, `_auth` deletes
`_temporary_admin` and empties the credential cache: its password, its sessions
and its tokens stop working at once.

A restart while `_temporary_admin` still exists writes it again with a new
password: the password of the previous log stops working, and the tokens
issued to `_temporary_admin` are deleted. A restart on a site with an
administrator deletes `_temporary_admin` and logs no password. Until then the
password in the log opens `AUTH_ADMIN` to whoever reads the log.

## OIDC

```{admonition} In revisione
:class: warning

The OIDC login is checked locally against a fake provider. A complete provider
login, including callback registration and token exchange, still requires
verification against your chosen external provider.
```

An OIDC provider is declared on `_auth`, and the server must know its own
**public base address** — `external_url`:

```python
from kajenn import AuthApplicationBase

PROVIDER = {
    "issuer": "https://accounts.example.com",
    "client_id": "client-123",
    "scopes": "openid email profile",
}
server = AsgiServer(
    applications=[(AuthApplicationBase, {"oidc": {"google": PROVIDER}}), App],
    external_url="https://shop.example.com",
)
```

Each provider gives two routes and one entry of `/_auth/login_methods`:

- `GET /_auth/oidc:google/start?next=...` → `302` to the provider
  (authorization code with PKCE S256).
- `GET /_auth/oidc:google/callback?...` → token exchange, `id_token`
  verification, avatar attached to the session, `302` to `next`.

Without a provider no OIDC route exists. The public descriptor never exposes
the `client_secret`, the `client_id` or the `issuer`.

`external_url` is what the server calls *itself* when it hands its own URL to
the provider: the `redirect_uri` the browser is sent back to must be absolute,
and must match the one you registered for the client in the provider's console —
here `https://shop.example.com/_auth/oidc:google/callback`. It is distinct from
the `host`/`port` the server binds to. A provider declared on a server
**without** `external_url` is a boot error: building the server raises
`ValueError`. In a recipe `external_url` is an attribute of the `server`
section, and each provider is addressed by its `code`:
`applications._auth.oidc.google`.

The user of a provider login is found in the store from the verified
`id_token`:

1. the user whose `external_ids` holds the token's `iss` + `sub`, whatever its
   email is now;
2. else, only when the token carries `email` and `email_verified` true, the one
   user with that email (compared lowercased). `{iss, sub}` is added to its
   `external_ids`: the next login finds it by `iss` + `sub`;
3. else the `self_registration` setting. Without it the login is refused. With
   it a user is written: identity = the verified email, `status` and `tags`
   from the setting, no group, no password, `email`, `external_ids`.

`self_registration(status="waiting")` writes a user that waits for approval
and does not enter; `status="confirmed"` writes a user that enters at once. A
setting whose `status` is not `waiting`, `confirmed` or `banned` is not
applied: self registration stays off and a warning is logged.

The avatar of a provider login carries the user's primary group and its tags
from the store, as a password login. The callback answers:

- `302` to the stored `next` on success;
- `400` for a flow error: a `state` missing or different from the session's,
  a failed token exchange, an `id_token` that does not verify or has no `sub`;
- `403` for a refused login: no verified email, two users with that email, an
  unknown user without `self_registration`, an identity already taken, a status
  other than `confirmed`, a system user. Nothing is attached.

## Customising

A subclass of `AuthApplicationBase` replaces the methods it wants. The
verification of a presented credential is `verify_credential(credential)`: it
receives the `Authorization` value and answers `{identity, group, tags, data,
valid_until}`, or raises `HTTPUnauthorized`:

```python
from kajenn import AuthApplicationBase, HTTPUnauthorized


class SiteAuth(AuthApplicationBase):
    def find_identity(self, value: str) -> str | None:
        """The identity a site credential belongs to: the site's own check."""
        raise NotImplementedError

    def verify_credential(self, credential: str) -> dict:
        scheme, _, value = credential.partition(" ")
        if scheme.lower() != "token":
            return super().verify_credential(credential)
        identity = self.find_identity(value)
        user = self.store.get_user(identity) if identity else None
        if user is None or user["status"] != "confirmed":
            raise HTTPUnauthorized("Invalid credentials")
        return {
            "identity": identity,
            "group": user.get("primary_group"),
            "tags": self.store.get_user_tags(identity),
            "data": {},
            "valid_until": None,
        }
```

`find_identity` stands for the site's own check; it is not a method of
`AuthApplicationBase`. The subclass is mounted in place of the base, with the
same code:
`application(code="_auth", app_class=SiteAuth)`. A store on another backend is
a subclass of `AuthStore`, named with `store(store_class=...)`.

## The Avatar

An authenticated caller is represented by an `Avatar`:

```python
from kajenn import Avatar

avatar = Avatar("alice", tags=["shop_admin"], group="staff")
```

The avatar's tags are what `auth_rule` matches against. The server builds the
avatar from the answers of `_auth`, whether the identity came from a header
credential or from a session (see the [sessions guide](sessions.md)).

## Header vs session precedence

- The `Authorization` header wins — kajenn is API-first. If a request carries
  a valid credential in the header, that identity is used.
- An **invalid** header credential produces `401` with no fallback to the session
  — a broken token is an error, not an invitation to try the cookie.
- Without a header, the session's identity and group are asked to
  `/_auth/avatar`.

## How to verify it

With the setup example running:

```console
$ curl -i http://127.0.0.1:8000/secret
HTTP/1.1 401 Unauthorized

$ curl -u alice:wonderland http://127.0.0.1:8000/secret
{"classified": true}

$ curl -i -u bob:builder http://127.0.0.1:8000/secret
HTTP/1.1 403 Forbidden

$ curl -i -H "Authorization: Bearer gak_unknown" http://127.0.0.1:8000/public
HTTP/1.1 401 Unauthorized
```

## Gotchas

- `auth_rule` is default-deny: anonymous callers receive `401`, known callers
  with insufficient tags receive `403`. The error middleware never points a
  caller at a login page — it owns none. It only negotiates the error *body*:
  `Accept: application/json` (or `*/*`) gets the `{"error": ...}` document, and
  anything else — including a browser's `text/html` and a missing `Accept` —
  gets `text/plain`. An application that wants to send a browser to its own
  login surface does that in its own routes.
- An authenticated caller whose avatar carries no tag at all receives `401`
  on a ruled route, as an anonymous one: the rule finds no tag to match.
- An invalid `Authorization` header is `401` on any route, a public one
  included, and does **not** fall back to the session cookie.
- `/_auth/login` answers a refusal with status `200` and `{"error": ...}`:
  check the body, not only the status.
- A tag in the list of a token that the user does not hold is dropped without
  an error: a typing error in the list is not visible when the token is issued.
- The store is built on first use. A server without storage starts; the first
  request that uses the store fails. A server without a `storage_key` for the
  encrypted records starts and reads the store; the first write fails. A store
  that has never been written holds no user, so no credential verifies: a
  request that only reads answers `401`.
- An OIDC provider needs `external_url`, and the resulting callback URL must be
  registered verbatim with the provider — a mismatch is refused by the provider,
  not by us. A missing `external_url` stops the server at boot.
- `AuthApplicationBase`, `AuthStore`, `FileAuthStore`, `AuthMixin` and `Avatar`
  import from `kajenn`. The login methods `AuthMethod`, `PasswordMethod` and
  `OidcMethod` import from `kajenn.auth_app`.
