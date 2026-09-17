# Requests and errors

> **Status:** Draft; implementation checked against the development source on 2026-09-12.

`RoutedApplication` creates a `Request` and awaits `init()` before resolving and
calling a handler. It drains the entire ASGI body and decodes it by content type.
It uses genro-tytx for serialization, not for reading the ASGI protocol.

## Decoded body or raw body

Decoding is an option of the application, and the configuration is where it is
written — there is no class attribute for it. The default, `decoded`, is the
table below. An application that wants the bytes untouched is declared `raw` in
the recipe that mounts it:

```python
app = cfg.applications(default="blobs").application(code="blobs", app_class=Blobs)
app.request(body="raw")
```

A raw application receives every body as `body_raw` and decodes it itself;
`Request.decode_body()`, `decode_json()`, `decode_multipart()` and
`get_transport()` are public for that.

## Body arguments

| Content type | Handler arguments |
|---|---|
| JSON, XML, msgpack (including TYTX media types) | Hydrated value in `body_data` |
| `application/x-www-form-urlencoded` | Individual field kwargs |
| `multipart/form-data` | Individual field kwargs; file parts are `UploadedFile` |
| Other or missing content type | Refused with `415` (decoded); bytes in `body_raw` (raw) |
| Empty body | No body argument |

Query parameters form the initial kwargs. Repeated query keys become lists;
form fields override query fields with the same name. Repeated multipart names
also become lists. A file has `name` (form field name), `filename` (client-supplied),
`content_type` and `data` (complete bytes). Uploaded files are not spooled to disk.

Save this as `bodies.py`, then run `python bodies.py`:

```python
from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from genro_routes import route


class Bodies(RoutedApplication):
    @route()
    def document(self, body_data):
        return {"received": body_data}

    @route()
    def upload(self, title, document):
        return {"title": title, "filename": document.filename,
                "bytes": len(document.data)}


class Blobs(RoutedApplication):
    @route()
    def count(self, body_raw):
        return {"bytes": len(body_raw)}


class SiteConfiguration(AsgiConfigBuilder):
    def main(self, root):
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8000)
        apps = cfg.applications()
        apps.application(code="bodies", mount="", app_class=Bodies)
        apps.application(code="blobs", mount="raw", app_class=Blobs).request(body="raw")


if __name__ == "__main__":
    AsgiServer(config=SiteConfiguration).serve()
```

```console
$ curl -H 'Content-Type: application/json' -d '{"name":"Ada"}' http://127.0.0.1:8000/document
{"received":{"name":"Ada"}}
$ curl -F title=Example -F document=@bodies.py http://127.0.0.1:8000/upload
{"title":"Example","filename":"bodies.py","bytes":...}
$ curl -H 'Content-Type: application/octet-stream' --data-binary abc http://127.0.0.1:8000/raw/count
{"bytes":3}
$ curl -i -H 'Content-Type: application/octet-stream' --data-binary abc http://127.0.0.1:8000/document
HTTP/1.1 415 Unsupported Media Type
```

Stop the process with Ctrl-C. There is no configurable total body-size limit in
`Request.read_body()`: memory grows with the body, including multipart uploads.
Use an ingress limit or an application that controls `receive` directly when
unbounded uploads are unacceptable. Direct HTTP response streaming does not
change this request buffering; see [Streaming](streaming.md).

## Validation and status codes

`AsgiServer` automatically arms `pydantic` and `openapi` on its routed
applications. Neither can be disabled; explicit entries configure their
options. A composition without `PluginMixin` does not supply this pair.

Pydantic coerces and validates annotated parameters. A JSON dictionary is
spread over declared scalar parameters
unless the handler declares `body_data` or accepts `**kwargs`. Extra JSON keys
are dropped in that spreading case. Forms and query kwargs still bind normally.

| Condition | Status |
|---|---|
| Unknown route | 404 |
| Missing required argument or unexpected kwarg | 400 |
| Body not decodable in its declared format, or a malformed form | 400 |
| Content type the core cannot decode (decoded mode) | 415 |
| Signature fits, but configured pydantic validation rejects values | 400 (`strict`), 422 (`fastapi`) |
| Exception raised inside the handler body | 500, unless it is an HTTP exception |
| Handler raises an `HTTPException` subclass | That exception's status |

The last row is the second option of the application. The default, `strict`,
answers 400 to every failure the core judges and leaves 422 to the handler, for
a domain rule of its own. An application declaring the FastAPI convention in its recipe —
`app.request(error_codes="fastapi")` — answers 422 to a rejected value, and 400
to everything else. The generated OpenAPI document declares the code the
application chose. See
[Coming from Starlette / FastAPI](../coming-from-fastapi.md#error-codes).

No decode failure becomes a 500: it is refused inside the request, with the
reason the decoder gave.

A handler that declares an **unannotated** `_request` parameter can receive the
live request when parameter metadata is available. `AsgiServer` supplies that
metadata through its automatically armed pydantic plugin.
It exposes the session and `avatar()`, and its `response` can be given cookies or
headers before the handler returns its result. This is explicit parameter
injection, not a thread-local current request.
