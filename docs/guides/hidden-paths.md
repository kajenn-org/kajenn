# Hidden paths and well-known URLs

## What it does

The server applies two rules in its own demux, before any application is
reached. Both are the **server's own**, not a middleware: they hold on a bare
`BaseServer` with no chain, and there is no switch to turn them off.

- A **well-known path** is a path matching one fixed pattern:
  `/.well-known/...` (RFC 8615), `/robots.txt` or `/favicon.ico`. It is
  answered only when an application declares it. Otherwise the answer is 404,
  `text/plain`, body `unimplemented well-known URL`.
- A **hidden path** is any other path whose first segment starts with a dot:
  `/.git/config`, `/.env`, `/.foo`. It answers 404 `Not Found`.

In both 404 cases no application is reached: not a mount, not the root
application, not the `default` redirect.

Every other path is ordinary, the conventional probes included:
`/sitemap.xml` and `/apple-touch-icon.png` are demuxed like any other path.

## Who answers a path

| Request or situation | Who answers it | Construction stops when | 404 "unimplemented well-known URL" when |
| --- | --- | --- | --- |
| A declared well-known path (`/.well-known/foo`) | The declaring application, on its route | — | — |
| An undeclared well-known path | The server | — | always |
| Another dotted first segment (`/.foo`) | The server: 404 `Not Found` | — | never |
| An ordinary probe (`/sitemap.xml`) | The application mounted there, or the root application | — | never |
| Two applications declare the same path | — | always: `ValueError` naming the path and both codes | — |
| One application declares a path another gives as default | The declaring application, in any order of registration | — | — |
| Two applications give the same default | — | always: `ValueError` naming the path and both codes | — |
| A declared key does not match the pattern | — | always: `ValueError` naming the key | — |
| A declared route does not resolve exactly on the application's router | — | always: `ValueError` naming the route | — |
| `/.well-known/security.txt` | `_server` as a default, from its `security_txt` word | `contact` or `expires` missing, `expires` not ISO 8601 | the word is not written |
| `/robots.txt` | `_server` as a default, from its `robots` word | no `agent`, an empty `user_agent`, a rule not starting with `/` or `*`, a sitemap not an absolute http(s) URL | the word is not written and no application declares it |
| `/favicon.ico` | `_server` as a default, the file `static/favicon.ico` of the site home | — | the file does not exist and no application declares it |
| `/.well-known/apple-app-site-association` | An application with `MobileMixin`, from its `ios` word | `ios` written without `paths` | `ios` is not written |
| `/.well-known/assetlinks.json` | An application with `MobileMixin`, from its `android` word | — | `android` is not written |

`_server` gives its three documents as defaults (`default_well_known`): an
application that declares the same path in `well_known` answers it instead,
for example a favicon per host. When `_server` does not configure a document
(its word is not written, its file does not exist), the path answers only if
an application declares it.

```console
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/.env
404
$ curl -s http://127.0.0.1:8000/.well-known/absent
unimplemented well-known URL
```

## Declaring a path: `well_known`

An application declares the well-known paths it answers in the class dict
`well_known`: public path → route on its own router.

```python
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication


class Api(RoutedApplication):
    well_known = {"/.well-known/oauth-protected-resource": "protected_resource"}

    @route()
    def protected_resource(self) -> dict:
        return {"resource": "https://example.com/", "authorization_servers": []}


server = AsgiServer(applications=[(Api, {"code": "api", "mount": "api"})])
```

`GET /.well-known/oauth-protected-resource` reaches `Api` with the path
`/protected_resource`. The rest of the scope, query string included, is
unchanged. The route's own auth rules apply as for any route: kajenn does not
check that a well-known route is public.

### Merge along the MRO

Each class along the MRO may write its own `well_known`. The dicts are merged
base first, so the more derived class wins on the same key. A mixin brings its
paths, and the derived class adds or replaces:

```python
class OAuthDiscovery:
    well_known = {"/.well-known/oauth-protected-resource": "protected_resource"}

    @route()
    def protected_resource(self) -> dict:
        return {"resource": "https://example.com/", "authorization_servers": []}


class Api(OAuthDiscovery, RoutedApplication):
    well_known = {"/.well-known/foo": "foo"}

    @route()
    def foo(self) -> str:
        return "foo"
```

`Api` declares both `/.well-known/oauth-protected-resource` and
`/.well-known/foo`.

### The `{mount}` marker

A key may contain `{mount}`. It is replaced with the application's mount when
the application is built. For the root mount (`mount=""`) the whole segment
`/{mount}` is removed.

```python
class Docs(RoutedApplication):
    well_known = {"/.well-known/foo/{mount}": "foo"}
```

- `Docs` mounted at `manual` declares `/.well-known/foo/manual`.
- `Docs` mounted at the root declares `/.well-known/foo`.
- `Docs` mounted twice, at `manual` and at `guide`, declares
  `/.well-known/foo/manual` and `/.well-known/foo/guide`: no conflict. Without
  the marker, the two instances would declare the same path and the server
  would stop with the conflict `ValueError`.

### Declaring a path only when configured: `active_well_known()`

The server indexes what `active_well_known()` returns, not `well_known`
itself. The base returns a copy of `well_known`. It is called after the
application is attached to its server, so an override can read the
configuration and leave out the paths that are not configured:

```python
class Api(RoutedApplication):
    well_known = {"/.well-known/foo": "foo"}

    def active_well_known(self) -> dict[str, str]:
        active = super().active_well_known()
        if not self.foo_enabled():
            active.pop("/.well-known/foo")
        return active
```

A path left out answers 404 "unimplemented well-known URL". `_server` and
`MobileMixin` use this method.

### Defaults: `default_well_known`

`default_well_known` has the shape of `well_known` — public path → route, the
same merge along the MRO, the same `{mount}` marker — and gives a path only as
a fallback. A default answers a path that no application declares in
`well_known`, whatever the order in which the applications are registered or
join over kbus. Two applications giving the same default stop the server, as
two declaring the same path do.

```python
class Site(RoutedApplication):
    default_well_known = {"/robots.txt": "robots"}
```

The server indexes what `active_default_well_known()` returns; an override
leaves out what is not configured, as with `active_well_known()`. `_server`
uses it for its three documents.

### The index on the server

`server.well_known_routes` is the index of the declared paths: public path →
(application, route). `server.default_well_known_routes` holds the defaults,
used when `well_known_routes` has no entry for the path.

```python
>>> server.well_known_routes
{'/.well-known/oauth-protected-resource': (<Api ...>, 'protected_resource')}
```

An application with `spawner="subprocess"` sends its `active_well_known()` and
`active_default_well_known()` when its process joins over kbus; the server indexes it like any other, and
forwards the request to the process as a request on its mount. See the
[kbus protocol](../design/kbus-protocol.md#joined).

## The `_server` documents

`_server` answers three documents from its own recipe words and from the site
home, as defaults. Each is given only when configured, and an application
declaring the same path answers it instead. The words are written on the
`_server` application node of the recipe:

```python
server_app = apps.application(code="_server")
```

### security.txt

```python
server_app.security_txt(
    contact=["mailto:security@example.com", "https://example.com/security"],
    expires="2027-06-30T00:00:00Z",
    preferred_languages=["en", "it"],
    policy="https://example.com/policy",
)
```

Parameters: `contact`, `expires`, `encryption`, `acknowledgments`,
`preferred_languages`, `canonical`, `policy`, `hiring`. `contact` and
`expires` are required; `expires` is an ISO 8601 date-time. An `expires`
already past logs a WARNING and the document is still served. Every field but
`expires` takes a string or a list; a list gives one line per item, except
`preferred_languages`, which is joined with `", "`.

`GET /.well-known/security.txt`, `text/plain`, fields in RFC 9116 order:

```text
Contact: mailto:security@example.com
Contact: https://example.com/security
Expires: 2027-06-30T00:00:00Z
Preferred-Languages: en, it
Policy: https://example.com/policy
```

### robots.txt

```python
robots = server_app.robots(sitemaps="https://example.com/sitemap.xml")
robots.agent(user_agent="*", disallow=["/admin/", "/private/"])
robots.agent(user_agent="Googlebot", allow="/", disallow="/drafts/")
```

`robots` takes `sitemaps` (a string or a list of absolute http(s) URLs) and at
least one `agent`. `agent` takes `user_agent` (default `*`, never empty),
`allow` and `disallow` (a string or a list; every rule starts with `/` or
`*`).

`GET /robots.txt`, `text/plain`: one group per agent in declaration order,
`Allow:` lines before `Disallow:` lines, the sitemaps last:

```text
User-agent: *
Disallow: /admin/
Disallow: /private/

User-agent: Googlebot
Allow: /
Disallow: /drafts/

Sitemap: https://example.com/sitemap.xml
```

### favicon.ico

`_server` answers `/favicon.ico` with the bytes of the file
`home:/static/favicon.ico` — `static/favicon.ico` in the
[site home](cli.md#the-site-home) — as `image/x-icon`. A server with no site
home, or a home without the file, does not declare the path.

## The mobile mixin: `MobileMixin`

`MobileMixin` answers the two files Apple and Google read on a site to open
its links in an app. It is not exported from `kajenn`:

```python
from kajenn import RoutedApplication
from kajenn.mobile import MobileMixin


class Shop(MobileMixin, RoutedApplication):
    """The shop site, with its iOS and Android apps."""
```

The mixin brings two recipe words, `ios` and `android`, to the application
node. Every list parameter takes a string or a list.

```python
shop = apps.application(code="shop", mount="", app_class=Shop)
shop.ios(
    team_id="ABCDE12345",
    bundle_id="com.example.shop",
    paths=["/orders/*", "/products/*"],
    excluded_paths="/orders/admin/*",
)
shop.android(
    package_name="com.example.shop",
    fingerprints="14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5",
    paths=["/orders/*", "/products/*"],
    excluded_paths="/orders/admin/*",
)
```

Each file is declared only when its word is written. An application with the
mixin and neither word written logs a WARNING naming its code and declares
neither file. The documents are built from the configuration at request time
and answered as `application/json`.

### iOS: `/.well-known/apple-app-site-association`

`ios` takes `team_id`, `bundle_id`, `paths` and `excluded_paths`.
`team_id`, `bundle_id` and `paths` are required, `paths` with at least one
path: there is no `/*` default. An `ios` word missing one of them stops the
server with `ValueError` naming it. Paths are copied as written,
in Apple's `*` and `?` syntax. The excluded paths come first:

```json
{
  "applinks": {
    "details": [
      {
        "appIDs": ["ABCDE12345.com.example.shop"],
        "components": [
          {"/": "/orders/admin/*", "exclude": true},
          {"/": "/orders/*"},
          {"/": "/products/*"}
        ]
      }
    ]
  },
  "webcredentials": {"apps": ["ABCDE12345.com.example.shop"]}
}
```

### Android: `/.well-known/assetlinks.json`

`android` takes `package_name`, `fingerprints` (the SHA-256 fingerprints of
the signing certificates), `paths` and `excluded_paths`. `package_name` and
at least one fingerprint are required: an `android` word missing one of them
stops the server with `ValueError` naming it.

```json
[
  {
    "relation": [
      "delegate_permission/common.handle_all_urls",
      "delegate_permission/common.get_login_creds"
    ],
    "target": {
      "namespace": "android_app",
      "package_name": "com.example.shop",
      "sha256_cert_fingerprints": ["14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5"]
    },
    "relation_extensions": {
      "delegate_permission/common.handle_all_urls": {
        "dynamic_app_link_components": [
          {"/": "/orders/admin/*", "exclude": true},
          {"/": "/orders/*"},
          {"/": "/products/*"}
        ]
      }
    }
  }
]
```

`relation_extensions` is present only when `paths` or `excluded_paths` is
written. Android reads it as Dynamic App Links, on Android 15 and later; older
devices take the paths from the app manifest.

`webcredentials` on iOS and `get_login_creds` on Android are always emitted:
there is no word to turn them off.

## Breaking change

The `_well_known` branch on an application's router is gone, with
`kajenn.well_known.WELL_KNOWN_ROOT`, `well_known_names` and
`server.well_known_applications`. Declare the paths in `well_known` instead.

`/favicon.ico` and `/robots.txt` are now well-known paths. A site whose root
application answered them from its own routes must declare them (a site that
only served the file `favicon.ico` can move it to `home:/static/favicon.ico`,
which `_server` serves as a default):

```python
class Site(RoutedApplication):
    well_known = {"/favicon.ico": "favicon", "/robots.txt": "robots"}
```

Otherwise they answer 404 "unimplemented well-known URL".

## Gotchas

- The paths are read **once, when the application is registered**. A path
  configured afterwards is not indexed.
- A declared route must resolve exactly to an entry of the application's
  router: a route resolved by falling back to a shorter path does not count.
- The hidden-path rule reads the **first** segment only.
  `/static/.hidden.css` is not a hidden path and reaches the application that
  serves `/static`.
