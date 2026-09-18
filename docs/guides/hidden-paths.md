# Hidden paths

## What it does

A path whose **first segment starts with a dot** is hidden or of service:
`/.git/config`, `/.env`, `/.aws/credentials`. Nothing of your site lives there,
and bots probe those paths on every host on the internet. The server answers
them **404** in its own demux, and no application is ever reached: not a mount,
not the root application, not the `default` redirect.

The rule is the **server's own**, not a middleware: it holds on a bare
`BaseServer` with no chain at all, and there is no switch to turn it off.

A path **without** a dot is ordinary, the conventional probes included.
`/favicon.ico`, `/robots.txt`, `/sitemap.xml` and `/apple-touch-icon-*.png`
are demuxed like any other path and reach the application mounted there: the
server silences none of them.

```console
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/.env
404
```

## The exception: `.well-known`

RFC 8615 reserves `/.well-known/` for discovery documents — the OAuth protected
resource metadata an MCP client reads, an ACME challenge, an OpenID
configuration. That one segment is served, and only for the names an
application declares.

An application declares them as the children of a `_well_known` branch on its
own router, attached like any other reserved branch:

```python
from kajenn import AsgiServer, RoutedApplication
from kajenn.well_known import WELL_KNOWN_ROOT
from genro_routes import RoutingClass, route


class Discovery(RoutingClass):
    """The documents this application answers under /.well-known/."""

    @route(name="oauth-protected-resource")
    def oauth_protected_resource(self):
        return {"resource": "https://example.test/", "authorization_servers": []}


class Api(RoutedApplication):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.route.add_branches({"name": WELL_KNOWN_ROOT, "instance": Discovery()})

    @route()
    def index(self):
        return {"api": self.code}


server = AsgiServer(applications=[(Api, {"code": "api", "mount": "api"})])
```

`GET /.well-known/oauth-protected-resource` now answers from `Api`, whatever
its mount is: the server resolves it on that application's own router as
`_well_known/oauth-protected-resource`. A request with a path under the name —
`/.well-known/acme/challenge/xyz` — resolves as
`_well_known/acme/challenge/xyz`, so a sub-branch named `acme` serves its own
tree. There is no translation to invent: what you declare is what is resolved.

## How the names are found

The server reads them **at mount time**, once, from every application it is
composed with: `BaseApplication.well_known_names` answers `()` and
`RoutedApplication.well_known_names` answers the children of its `_well_known`
branch. The result is one flat index, `name → application`, readable on the
server:

```python
>>> server.well_known_applications
{'oauth-protected-resource': <Api ...>}
```

Two applications declaring the same name is not an error: **the last declared
wins**, in the order of `applications=`. The rule is fixed and has no option.

A name reached this way is served by its application with that application's
own rules — an entry carrying `auth_rule` answers `401` to the anonymous and
`403` to an identity whose tags do not match, exactly as it would on any other
path.

## How to verify it

```console
$ curl -s http://127.0.0.1:8000/.well-known/oauth-protected-resource
{"resource":"https://example.test/","authorization_servers":[]}
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/.well-known/absent
404
```

## Gotchas

- The names are read **once, at mount time**. A branch attached to an
  application after the server was built is not indexed, and its documents
  answer 404.
- A discovery document is a public contract: name the route exactly as the
  standard spells it (`@route(name="oauth-protected-resource")`), because the
  name on the wire is the name in the router.
- The rule reads the **first** segment only. `/static/.hidden.css` is not a
  hidden path and reaches the application that serves `/static`.
- A websocket handshake takes the same demux, so a hidden path finds no
  application there either and the socket is closed.
