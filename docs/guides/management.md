# Management application

Prerequisites: [sessions](sessions.md), [authentication](authentication.md) and
[storage encryption](storage.md#optional-encryption).
Two applications serve the management of a site as JSON endpoints. `_auth`
(`AuthApplicationBase`) manages users, groups, tags and tokens, and serves the
login. `_server` (`ServerApplication`, mounted by every server) serves
monitoring, databases, applications and tasks. Neither is a ready-made
graphical console.

## Start an isolated local demo

Create a fresh writable directory. Set a test encryption key in that terminal,
then save the example as `management.py`:

```bash
export DEMO_STORAGE_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

```python
import os
from genro_routes import route
from kajenn import AsgiServer, AuthApplicationBase, RoutedApplication


class Members(RoutedApplication):
    mount = ""

    @property
    def declared_tags(self) -> list:
        return [{"code": "members_reader", "description": "Read the private page"}]

    @route(auth_rule="members_reader")
    def private(self, _request=None) -> dict:
        return {"identity": _request.avatar().identity}


if __name__ == "__main__":
    server = AsgiServer(
        applications=[AuthApplicationBase, Members],
        storage_key=os.environ["DEMO_STORAGE_KEY"],
    )
    server.serve(host="127.0.0.1", port=8000)
```

Run `python management.py`. The store is empty. At startup `_auth` writes the
tags the applications declare (`AUTH_ADMIN`, `SERVER_ADMIN`, `SERVER_METRICS`,
`members_reader`) and a temporary administrator, and the server log carries
one line with its password:

```text
application _auth: temporary administrator _temporary_admin, password <password>
```

The following commands run in another terminal. Copy the password there:

```bash
export DEMO_TEMP_PASSWORD='PASTE_THE_PASSWORD_OF_THE_LOG'
export DEMO_ADMIN_PASSWORD='local-demo-only-change-me'
```

Bind remains local. Real deployments must supply transport security and an
appropriate credential policy.

## Create the real administrator

The temporary administrator creates the operator, with the tags of both
management applications:

```bash
curl -u "_temporary_admin:$DEMO_TEMP_PASSWORD" -H 'Content-Type: application/json'   -d "{\"password\":\"$DEMO_ADMIN_PASSWORD\",\"password_confirm\":\"$DEMO_ADMIN_PASSWORD\",\"tags\":[\"AUTH_ADMIN\",\"SERVER_ADMIN\"]}"   'http://127.0.0.1:8000/_auth/users/create_user?identity=operator'

curl -i -u "_temporary_admin:$DEMO_TEMP_PASSWORD" http://127.0.0.1:8000/_auth/users/list
```

Create returns the operator's public record, without a password hash. From that
moment `_temporary_admin` does not exist: the second command answers 401. A
restart of the server logs no password.

## Create a group and a user, and log in

The operator creates a group that carries the tag of the private page, and a
user in that group:

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" -H 'Content-Type: application/json'   -d '{"tags":["members_reader"]}'   'http://127.0.0.1:8000/_auth/groups/create_group?code=members'

curl -u "operator:$DEMO_ADMIN_PASSWORD" -H 'Content-Type: application/json'   -d '{"password":"demo-password","password_confirm":"demo-password","groups":["members"],"primary_group":"members"}'   'http://127.0.0.1:8000/_auth/users/create_user?identity=alice'

curl -c cookies.txt -H 'Content-Type: application/json'   -d '{"identity":"alice","password":"demo-password"}'   http://127.0.0.1:8000/_auth/login

curl -b cookies.txt http://127.0.0.1:8000/private
```

Login returns `session_id`, `identity`, `group` (`members`, the primary group),
`tags` (`["members_reader"]`, from the group) and `groups`. The private route
returns `{"identity":"alice"}`. Without a cookie or credential, the private
route returns 401.

A tag that has no record is refused: a user, a group or a token naming one is
not written, and the answer is `{"error": "Unknown tags: ..."}`. Create the tag
first with `/_auth/tags/create_tag?code=...`.

Save the returned session_id. Logout requires that value explicitly:

```bash
curl -X POST 'http://127.0.0.1:8000/_auth/logout?session_id=PASTE_SESSION_ID'
curl -b cookies.txt http://127.0.0.1:8000/private
```

After logout, the old cookie no longer authorizes the private route (401).
Invalid login credentials return an error object; check the body, not only the
HTTP status. Repeated login failures are subject to the lockout of `_auth`.

## Issue and revoke an API token

A token belongs to a user and carries `"*"` or a list of tags the user holds:

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" -H 'Content-Type: application/json'   -d '{"user":"alice","tags":["members_reader"]}'   http://127.0.0.1:8000/_auth/tokens/issue
```

The `key` is returned only once. Use it as `Authorization: Bearer PASTE_KEY` to
call `/private`. List records to obtain its key_id, then revoke it:

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_auth/tokens/list
curl -u "operator:$DEMO_ADMIN_PASSWORD" -X POST   'http://127.0.0.1:8000/_auth/tokens/revoke?key_id=PASTE_KEY_ID'
```

The revoked key remains listed for audit but no longer authenticates (401).
The list does not return the original secret. Do not publish generated keys.

## The management routes of `_auth`

Every route asks `AUTH_ADMIN`. Errors answer status 200 with
`{"error": message}`, and nothing is written.

- `/_auth/users/`: `list`, `get(identity)`, `create_user(identity, body)`,
  `save(identity, body)`, `set_password(identity, body)`, `delete(identity)`.
  The body of `create_user` carries any field of the user record and,
  optionally, `password` and `password_confirm`, which must match. Without a
  password the user does not enter with a password until `set_password` gives
  it one. Defaults: no group, no tag, `status` `confirmed`, `system` false.
  `password_hash` never crosses the wire.
- `/_auth/groups/`: `list`, `get(code)`, `create_group(code, body)`,
  `save(code, body)`, `delete(code)`. A group that a user still has is not
  deleted.
- `/_auth/tags/`: `list`, `get(code)`, `create_tag(code, body)`,
  `save(code, body)`, `delete(code)`.
- `/_auth/tokens/`: `list`, `issue(body)` with `user`, `tags` and
  `expires_at` (a POSIX timestamp; absent for a token that never expires),
  `revoke(key_id)`, `delete(key_id)`. `revoke` and `delete` empty the
  credential cache of the server and of every joined process.

A successful write of `/_auth/users/` (`create_user`, `save`, `set_password`,
`delete`) or of `/_auth/groups/` (`create_group`, `save`, `delete`) empties the
credential cache of the server and of every joined process: a changed status,
tag or group applies from the next request (see
[authentication](authentication.md#authentication-on-demand)). A refused write
empties nothing. `delete` on a user also deletes its tokens and its login
failures.

## Monitor and permissions

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_server/monitor/snapshot
curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_server/monitor/panels
```

| Surface | Required tag |
| --- | --- |
| Users, groups, tags, tokens (`/_auth/...`) | AUTH_ADMIN |
| Monitor, databases, applications, tasks (`/_server/...`) | SERVER_ADMIN |
| Metrics (`/_server/metrics`) | SERVER_METRICS |
| Demo private route | members_reader |

Tags are explicit: `AUTH_ADMIN` does not imply `SERVER_ADMIN`. An anonymous
caller gets 401; an authenticated caller missing the tag gets 403. The example
operator deliberately carries both management tags; alice gets 403 on the
monitor.

The snapshot exposes server and application state. Applications can contribute
`app_snapshot` data and `app_panel` descriptors. Those are extension points for
a front end, not HTML management pages served by this package.

```{admonition} In revisione
:class: warning

The JSON management walkthrough is checked locally. A complete custom monitor
panel still needs its own executable example; use the API reference for this
extension surface.
```

Stop with Ctrl-C. Store records remain in the demo directory; retain the key if
you intend to reopen them. Delete the demo cookie jar when finished and keep test
credentials separate from any real deployment. For all methods, see the
[authentication API](../api/auth.rst) and the
[management API](../api/server-app.rst).
