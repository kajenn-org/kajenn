# Sessions

## What it does

Gives a caller continuity across requests: a session cookie identifies a stored
`Session` that can hold an `Avatar` and arbitrary data, reconnected on every
request through the cookie.

## When to use it

When callers need to stay logged in, or when you want to carry per-user state
between requests without re-authenticating each time. The session subsystem is active on `AsgiServer` by default. It keeps a store,
sets the cookie, and rehydrates the session for you.

## Setup

Sessions are a mixin capability of `AsgiServer` (`SessionMixin`). Its presence wires
the session middleware automatically (unless explicitly disabled). The relevant constructor kwargs are:

- `session_store` — the backing store; `None` defaults to `MemorySessionStore`.
- `session_ttl` — the session lifetime.

```python
from kajenn import AsgiServer, RoutedApplication, MemorySessionStore
from genro_routes import route


class App(RoutedApplication):
    mount = ""

    @route()
    def index(self) -> dict:
        return {"ok": True}


server = AsgiServer(applications=[App], session_store=MemorySessionStore)
server.serve(host="127.0.0.1", port=8000)
```

## The store

One store ships with kajenn: **`MemorySessionStore`** — sessions live in
the process. Simple, fast, lost on restart (but see the shutdown snapshot
below). A custom backend is named as a CLASS — `session_store=MyStore`, or
`(MyStore, {...})` with its own parameters — and the server builds it; in a
recipe the same thing is `server.session(store_class=MyStore)`. It must
implement the `SessionStore` protocol.

## The shutdown snapshot

`server.session(save_path=...)` names a pickle file — `save_session=` is the
same word in the shortcut form; when set, the server saves
**every live session — data included** — to that file at shutdown, and loads
it back at the next startup (a session past its TTL is dropped on load; an
absent file starts empty).

The CLI arms this automatically when you name an instance:

```console
$ kajenn serve ./config.py --name demo
```

persists sessions to `~/.kajenn/sessions/demo.pickle` across restarts —
`--reload` restarts included. A nameless serve stays volatile.

This is a development convenience: your login and your work survive a code
restart. It is not a production persistence story — the snapshot is one file
written by one process at shutdown.

## Session expiry

A session dies by inactivity alone: every request refreshes its
`last_access`, and a session whose inactivity exceeds the TTL is dropped —
lazily when its cookie comes back, and in a periodic mass reap at
session-creation time. There is no active signal from the client: a closed
tab leaves the session alive until the TTL runs out; explicit death is the
logout's `delete()`.

## The session cookie

Cookie behaviour is controlled through the `session` middleware options:

```python
server = AsgiServer(
    applications=[App()],
    session_store=MemorySessionStore,
    middleware={"session": {
        "cookie_name": "session_id",
        "secure": True,
        "samesite": "lax",
    }},
)
```

The session cookie is **always** `HttpOnly`. `secure` and `samesite` are yours to
set; `cookie_name` renames the cookie.

The cookie's `Max-Age` is the session TTL **times 24**, on purpose: the
server-side expiry slides with activity while `Max-Age` is fixed from issue
time, so a same-length cookie would log an active user out on schedule. The
wide cookie makes the server the only arbiter of expiry, and only the first
response of a session carries a `Set-Cookie`.

## The Session object

A `Session` carries:

- `.id` — the session identifier.
- `.data` — a `Bag` of arbitrary session data.
- `.meta` — session metadata.
- `.avatar(key="root")` — the identity attached under `key`, if any; with no
  argument, the root one (the primary login).
- `.avatars` — a read-only view of every keyed identity on the session.
- `.dirty` — whether the session has unsaved changes.
- `.attach_avatar(avatar, key="root")` — bind an `Avatar` to the session under
  `key`; the default root key is what a login does.

## Attaching an avatar at login

At the point a caller proves who they are, attach their avatar to the session so
subsequent requests carry the identity:

```python
from kajenn import Avatar

# inside a handler that has verified the credentials:
session.attach_avatar(Avatar("alice", tags="admin,ops"))
```

From then on, requests bearing the session cookie resolve to that avatar — no
`Authorization` header needed. (Remember from the [auth
guide](authentication.md) that a valid `Authorization` header still takes
precedence over the session.)

## How to verify it

The first response sets the cookie; a second request replaying it reconnects the
same session:

```console
$ curl -i http://127.0.0.1:8000/index
HTTP/1.1 200 OK
set-cookie: session_id=...; HttpOnly; ...

$ curl -b "session_id=..." http://127.0.0.1:8000/index
{"ok": true}
```

## Gotchas

- `MemorySessionStore` loses everything on restart and does not share across
  processes — the shutdown snapshot (below) covers the development restart,
  not multi-process deployments.
- The cookie is always `HttpOnly`; you cannot turn that off. You *can* set
  `secure` and `samesite`.
- Configure the cookie under `middleware={"session": {...}}`, not under the
  `session_store` / `session_ttl` kwargs — those control the store and lifetime,
  the middleware controls the cookie.
- `SessionMixin`, `Session` and `MemorySessionStore` are all importable from
  `kajenn`.
