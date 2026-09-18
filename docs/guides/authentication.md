# Authentication

## What it does

Configures how callers prove who they are and controls which routes they may
reach. kajenn accepts basic, bearer and JWT credentials plus API keys, and
filters routes by the tags carried on the caller's `Avatar`.

## When to use it

When some routes must be restricted to authenticated callers, or to callers with
a particular role. Pass an `auth` config to `AsgiServer` and mark the protected
routes with `auth_rule`.

## Setup

Auth is a mixin capability of `AsgiServer`; its middleware is active by default.
The `auth` keyword configures header credential backends. Without them, session
identity can still be resolved.

```python
from kajenn import AsgiServer, RoutedApplication
from genro_routes import route

AUTH = {
    "basic":  {"alice": {"password": "wonderland", "tags": "admin,ops"}},
    "bearer": {"svc":   {"token": "sk_live_xyz", "tags": "api"}},
    "jwt":    [{"secret": "topsecret", "algorithm": "HS256"}],
}


class App(RoutedApplication):
    mount = ""

    @route()
    def public(self) -> dict:
        return {"open": True}

    @route(auth_rule="admin")
    def secret(self) -> dict:
        return {"classified": True}


server = AsgiServer(applications=[App], auth=AUTH)
server.serve(host="127.0.0.1", port=8000)
```

Notes on the config shape:

- `basic` and `bearer` are dictionaries keyed by identity. A basic entry carries
  a `password`; a bearer entry carries a `token`. Both carry `tags` (a
  comma-separated string or a list) that become the avatar's roles.
- `jwt` is a **list** of verifier configurations, each with a `secret` and an
  `algorithm` — you can accept tokens from more than one issuer.
- API keys with the `gak_...` prefix are handled by an `ApiKeyStore`. The store
  is DECLARED, never handed over: `tokens={"store_class": MyApiKeyStore, ...}`
  alongside `auth` (`FileApiKeyStore` when the class is omitted), and the server
  builds it with its storage. Users are the same shape —
  `users={"store_class": ..., "mount": ..., "prefix": ...}`. In a recipe the
  same words are `authentication.users(...)` and `authentication.tokens(...)`.
- **The server creates no user.** There is no bootstrap password: a deployment
  that needs a first identity declares the store class that carries it, and the
  login surface belongs to the application.

## Minimal snippet

Protecting a single route:

```python
@route(auth_rule="admin")
def secret(self) -> dict:
    return {"classified": True}
```

`auth_rule="admin"` means the caller's avatar must carry the `admin` tag.
Protection is **default-deny**: an anonymous caller receives `401` even if no auth is
configured at all; a known caller without the required tag receives `403`, so a protected endpoint is never accidentally open.

## The Avatar

An authenticated caller is represented by an `Avatar`:

```python
from kajenn import Avatar

avatar = Avatar("alice", tags="admin,ops")   # tags: comma string or list
```

The avatar's tags are what `auth_rule` matches against. The same `Avatar` type is
used whether the identity came from a header credential or from a session (see
the [sessions guide](sessions.md)).

## Header vs session precedence

- The `Authorization` header wins — kajenn is API-first. If a request carries
  a valid credential in the header, that identity is used.
- An **invalid** header credential produces `401` with no fallback to the session
  — a broken token is an error, not an invitation to try the cookie.
- With `auth=None`, no header backend is configured, but the server still
  resolves the identity carried by the session.

## Server-side login flow

`ServerApplication` (from `kajenn_server_app`, declared like any other
application with the code `_server`) exposes a login flow for session-based
clients. Nothing mounts it for you:

- `POST /_server/login` with body `{"identity", "password"}` → `200` with
  `{identity, tags, session_id}` on success.
- `GET /_server/login_methods` — a public JSON descriptor of available methods.
- `POST /_server/logout`.

All three answer JSON; there is no HTML login page here. Login attaches the
avatar to the **existing** session, so the session id does not change and no
login-time cookie is set.

Login lockout with backoff is the app's own `login=` kwarg:
`ServerApplication(login={"max_attempts": 5, "backoff": 30})`, defaulting to 5
attempts and a 30-second base with exponential backoff.

## OIDC

OIDC providers are the app's own `oidc=` kwarg, and the server must know its
own **public base address** — `external_url`:

```python
PROVIDER = {
    "issuer": "https://accounts.example.com",
    "client_id": "client-123",
    "scopes": "openid email profile",
    "identity_claim": "email",
    "tags": [],
}
server = AsgiServer(
    applications=[ServerApplication(oidc={"google": PROVIDER}), App()],
    external_url="https://shop.example.com",
)
```

`external_url` is what the server calls *itself* when it hands its own URL to
the provider: the `redirect_uri` the browser is sent back to must be absolute,
and must match the one you registered for the client in the provider's console —
here `https://shop.example.com/_server/auth/oidc:google/callback`. It is
distinct from the `host`/`port` the server binds to, which differ behind a proxy
and mean nothing to an outside caller. Configuring a provider **without**
`external_url` is a boot error: the server refuses to start rather than fail at
the first login attempt with a provider-side error.

In a config recipe, `external_url` lives on the `server` section and the
providers are a keyed collection under the application that owns them — the
words are the package's own grammar, mounted on its `application` element:

```python
from kajenn.config import AsgiConfigBuilder
from kajenn_server_app import ServerApplication
from genro_bag.resolvers import EnvResolver

class ServerConfiguration(AsgiConfigBuilder):
    def main(self, root):
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8000, external_url="https://shop.example.com")
        server_app = cfg.applications().application(
            code="_server", app_class=ServerApplication
        )
        server_app.login(max_attempts=3, backoff=10)
        server_app.oidc().provider(
            code="google",
            issuer="https://accounts.example.com",
            client_id="client-123",
            client_secret=EnvResolver("GOOGLE_CLIENT_SECRET"),
            identity_claim="email",
        )
```

Each provider is addressed by its `code` — `applications._server.oidc.google` —
and the `client_secret` is an `EnvResolver` (from `genro_bag.resolvers`) read at
read time, so the secret never sits in the recipe. The `authentication` section
carries the `users`/`tokens` store descriptors and the `credentials` block that
replaces the `auth=` dict when the server is configured rather than hand-built.

- `GET /_server/auth/oidc:google/start?next=...` → `302` (PKCE S256).
- `GET /_server/auth/oidc:google/callback?...` → token exchange, avatar attach,
  redirect.
- The public descriptor never exposes the `client_secret` or the `issuer`.

## How to verify it

```console
$ curl -i http://127.0.0.1:8000/secret
HTTP/1.1 401 Unauthorized

$ curl -u alice:wonderland http://127.0.0.1:8000/secret
{"classified": true}

$ curl -H "Authorization: Bearer sk_live_xyz" http://127.0.0.1:8000/public
{"open": true}
```

## Gotchas

- `auth_rule` is default-deny: anonymous callers receive `401`, known callers
  with insufficient tags receive `403`. The error middleware never points a
  caller at a login page — it owns none. It only negotiates the error *body*:
  `Accept: application/json` (or `*/*`) gets the `{"error": ...}` document, and
  anything else — including a browser's `text/html` and a missing `Accept` —
  gets `text/plain`. An application that wants to send a browser to its own
  login surface does that in its own routes.
- `jwt` is a **list**, not a dict — a single verifier still goes inside a
  one-element list.
- An invalid `Authorization` header is `401` and does **not** fall back to the
  session cookie.
- An OIDC provider needs `external_url`, and the resulting callback URL must be
  registered verbatim with the provider — a mismatch is refused by the provider,
  not by us. A missing `external_url` stops the server at boot.
- The core's auth symbols — `AuthMixin`, `AuthCore`, `Avatar`, `ApiKeyStore`,
  `FileApiKeyStore`, `UserStore`, `FileUserStore` — import from `kajenn`. The
  login methods are **not** core: `ServerApplication`, `AuthMethod`,
  `PasswordMethod`, `OidcMethod` and the sections import from
  `kajenn_server_app`.
