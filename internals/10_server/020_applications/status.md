# Applications — current state

**Version**: 0.4 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Application contract and routing

`BaseApplication` provides `code`, `mount`, exactly-once server ownership,
application-relative configuration, no-op lifecycle hooks and the ASGI callable
contract. `mount=""` means the root; only `None` falls back to the code.
`app_snapshot` and `app_panel` supply generic monitor contributions. The monitor
also accepts an optional `panel_source` supplied by an application.

`RoutedApplication` combines that contract with `RoutingClass`. It plugs `auth`
at construction and arms the server's fixed `pydantic`/`openapi` plugins on the
first router access after attachment. Branches use `add_branches`; the existing
instance-form consumers do not demonstrate the full declarative `cls`/`params`
destination described in the decisions.

Claim anchors: [`BaseApplication`](../../../src/kajenn/application.py#L86), [`app_snapshot`](../../../src/kajenn/application.py#L170), [`app_panel`](../../../src/kajenn/application.py#L180), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L111).

## Request parsing and handler arguments

`Request.init` owns headers, cookies, query parsing and body consumption. It
drains all body chunks even without a Content-Type. JSON, XML and MessagePack
are decoded with TYTX; URL-encoded fields use `from_qs`. Multipart text fields
are hydrated and files become `UploadedFile` values (`name`, `filename`,
`content_type`, `data`); repeated field names produce a list. Unknown media and
absent Content-Type are refused with 415, and a body that is not what its
content type declares with 400; an application declaring `request(body="raw")`
receives the bytes untouched instead, and decodes them itself (issue #87). The
request body is buffered in full.

`handler_kwargs` starts with query values. Form fields override colliding query
values; decoded non-form data becomes `body_data`, and the bytes of a raw
application become `body_raw`. `bind_kwargs` spreads a decoded dict over declared parameters unless
the handler accepts `body_data` or `**kwargs`. A declared, unannotated `_request`
receives the live request in every `RoutedApplication`, not only `_server`.
This common opt-in seam was explicitly approved in N37 on 2026-09-06; its
relationship to the older D31 effects target remains recorded in the
[decision follow-up](decisions.md).

Claim anchors: [`Request`](../../../src/kajenn/request.py#L107), [`init`](../../../src/kajenn/request.py#L152), [`UploadedFile`](../../../src/kajenn/request.py#L89), [`content_type`](../../../src/kajenn/request.py#L303), [`handler_kwargs`](../../../src/kajenn/request.py#L407), [`bind_kwargs`](../../../src/kajenn/routed_application.py#L272), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L111).

## Dispatch, authorization and failures

Async handlers run on the event loop. Sync handlers use `server.run_sync` and
run `route_cleanup` on that same thread even on failure. Router misses yield
404, anonymous access to a ruled route yields 401, and insufficient tags yield
403. The outer error middleware may turn a browser's 401 into a login redirect.

Signature mismatch yields 400. Values rejected by Pydantic after successful
binding yield the application's `validation_error_status`: 400 under the
default strict reading, 422 for an application declaring
`request(error_codes="fastapi")` (issue #87). An exception from the handler body, including `TypeError`,
propagates to the error middleware as 500. The `Response.set_error` helper's
standalone exception table must not be substituted for that dispatch contract.

Claim anchors: [`run_sync`](../../../src/kajenn/server.py#L222), [`route_cleanup`](../../../src/kajenn/routed_application.py#L261), [`Response`](../../../src/kajenn/response.py#L57), [`set_error`](../../../src/kajenn/response.py#L242).

Behavior evidence: [`__call__`](../../../src/kajenn/routed_application.py#L173), [`make_callable`](../../../src/kajenn/routed_application.py#L233).

## Buffered responses, streams and database cleanup

`Response` emits one start and one body message. `set_result` serializes
collections as JSON or the requested TYTX transport, handles text/bytes/paths,
and applies node metadata. A returned `StreamingResponse` instead emits chunks
without collecting them; `SseStream` provides framing, keepalive and iterator
cleanup. This core streaming path does not make the SPA worker channel stream:
`AsgiSeam` collects a hosted response into one reply.

`Request.db` resolves the configured database handler and registers its
`closeConnection` cleanup on the current request item. `get_db(name)` only
looks up the handler. The handler-specific thread cleanup hook is a separate seam.

Claim anchors: [`Response`](../../../src/kajenn/response.py#L57), [`set_result`](../../../src/kajenn/response.py#L192), [`StreamingResponse`](../../../src/kajenn/streaming.py#L41), [`SseStream`](../../../src/kajenn/sse.py#L50), [`AsgiSeam`](../../../src/kajenn_orchestra/environ.py#L71), [`Request`](../../../src/kajenn/request.py#L107), [`db`](../../../src/kajenn/request.py#L374), [`get_db`](../../../src/kajenn/request.py#L400).

## WebSocket and package boundaries

Applications can receive WSX messages as synthetic HTTP scopes with method
`WSK`; the server can also hand an application its raw `serve_websocket` scope.
The HTTP middleware does not run per WSX message.

`OpenApiApplication`, `McpApplication`, `McpOpenApiApplication`,
`ServerApplication` and `ConfigurationProfilesApplication` live in the core.
`SpaApplication` and its grammar live in `kajenn_orchestra`, shipped by
the same distribution. The old SPA import paths have no compatibility re-export.
Dynamic movability/removal/failure declarations remain design distance.

Behavior evidence: [`on_websocket`](../../../src/kajenn/server.py#L365), [`_call_application`](../../../src/kajenn/wsx.py#L348), [`SpaApplication`](../../../src/kajenn_orchestra/spa_app.py#L517).

## Source and test evidence

- [src/kajenn/application.py](../../../src/kajenn/application.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/request.py](../../../src/kajenn/request.py)
- [src/kajenn/response.py](../../../src/kajenn/response.py)
- [src/kajenn/streaming.py](../../../src/kajenn/streaming.py)
- [src/kajenn/sse.py](../../../src/kajenn/sse.py)
- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [src/kajenn_orchestra/environ.py](../../../src/kajenn_orchestra/environ.py)
- [tests/core/test_contract.py](../../../tests/core/test_contract.py)
- [tests/core/test_routed_application.py](../../../tests/core/test_routed_application.py)
- [tests/core/test_request.py](../../../tests/core/test_request.py)
- [tests/core/test_response.py](../../../tests/core/test_response.py)
- [tests/core/test_streaming.py](../../../tests/core/test_streaming.py)
- [tests/core/test_sse.py](../../../tests/core/test_sse.py)
- [tests/core/test_websocket_raw_seam.py](../../../tests/core/test_websocket_raw_seam.py)
- [tests/spa/orchestration/test_orchestration_asgi_seam.py](../../../tests/spa/orchestration/test_orchestration_asgi_seam.py)
