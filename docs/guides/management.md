# Management application

Prerequisites: [sessions](sessions.md), [authentication](authentication.md) and
[storage encryption](storage.md#optional-encryption).
The optional ServerApplication provides JSON endpoints for login, users, tokens,
tasks and monitoring. It is not a ready-made graphical console.

## Start an isolated local demo

Create a fresh writable directory. Set an operator password and a test encryption
key in that terminal, then save the example as `management.py`:

```bash
export DEMO_ADMIN_PASSWORD='local-demo-only-change-me'
export DEMO_STORAGE_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

```python
import os
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn_server_app import ServerApplication


class Members(RoutedApplication):
    mount = ""

    @route(auth_rule="member")
    def private(self, _request=None) -> dict:
        return {"identity": _request.avatar().identity}


if __name__ == "__main__":
    server = AsgiServer(
        applications=[ServerApplication, Members],
        storage_key=os.environ["DEMO_STORAGE_KEY"],
        users={"mount": "site", "prefix": "users"},
        tokens={"mount": "site", "prefix": "api_keys"},
        auth={"basic": {"operator": {
            "password": os.environ["DEMO_ADMIN_PASSWORD"],
            "tags": "SUPERADMIN,SERVER_ADMIN",
        }}},
    )
    server.serve(host="127.0.0.1", port=8000)
```

Run `python management.py`. The operator credential authenticates HTTP Basic
requests; it does not create a stored user or enable password login for operator.
The following commands run in another terminal; set the same operator password
there. Bind remains local. Real deployments must supply transport security and
an appropriate credential policy.

## Create a user and log in

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD"   -H 'Content-Type: application/json'   -d '{"password":"demo-password","password_confirm":"demo-password","tags":["member"]}'   'http://127.0.0.1:8000/_server/users/create_user?identity=alice'

curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_server/users/list

curl -c cookies.txt -H 'Content-Type: application/json'   -d '{"identity":"alice","password":"demo-password"}'   http://127.0.0.1:8000/_server/login

curl -b cookies.txt http://127.0.0.1:8000/private
```

Create returns alice's public record, without a password hash. Login returns an
identity, tags and session_id; the private route returns `{"identity":"alice"}`.
Without a cookie or credential, the private route returns 401.

Save the returned session_id. Logout requires that value explicitly:

```bash
curl -X POST 'http://127.0.0.1:8000/_server/logout?session_id=PASTE_SESSION_ID'
curl -b cookies.txt http://127.0.0.1:8000/private
```

After logout, the old cookie no longer authorizes the private route (401).
Invalid login credentials return an error object; check the body, not only the
HTTP status. Repeated login failures are subject to the application's backoff.

## Issue and revoke an API token

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" -H 'Content-Type: application/json'   -d '{"label":"local-client","tags":["member"]}'   http://127.0.0.1:8000/_server/tokens/issue
```

The `key` is returned only once. Use it as `Authorization: Bearer PASTE_KEY` to
call `/private`. List records to obtain its key_id, then revoke it:

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_server/tokens/list
curl -u "operator:$DEMO_ADMIN_PASSWORD" -X POST   'http://127.0.0.1:8000/_server/tokens/revoke?key_id=PASTE_KEY_ID'
```

The revoked key remains listed for audit but no longer authenticates (401).
The list does not return the original secret. Do not publish generated keys.

## Monitor and permissions

```bash
curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_server/monitor/snapshot
curl -u "operator:$DEMO_ADMIN_PASSWORD" http://127.0.0.1:8000/_server/monitor/panels
```

| Surface | Required tag |
| --- | --- |
| User management | SUPERADMIN |
| Token management | SUPERADMIN |
| Task management | SUPERADMIN |
| Monitor snapshot and panels | SERVER_ADMIN |
| Demo private route | member |

Tags are explicit: SUPERADMIN does not automatically imply SERVER_ADMIN. An
anonymous caller gets 401; an authenticated caller missing the tag gets 403.
The example operator deliberately carries both management tags.

The snapshot exposes server and application state. Applications can contribute
`app_snapshot` data and `app_panel` descriptors. Those are extension points for
a front end, not HTML management pages served by this package.

```{admonition} In revisione
:class: warning

The JSON management walkthrough is checked locally. A complete custom monitor
panel and JWT issuance walkthrough still need their own executable examples;
use the API reference for these extension surfaces.
```

Stop with Ctrl-C. Store records remain in the demo directory; retain the key if
you intend to reopen them. Delete the demo cookie jar when finished and keep test
credentials separate from any real deployment. For all methods, see the
[management API](../api/server-app.rst).
