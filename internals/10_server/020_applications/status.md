# Applications — current state

**Version**: 0.5 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Application contract and routing

`BaseApplication` provides `code`, `mount`, exactly-once server ownership,
application-relative configuration, no-op lifecycle hooks and the ASGI callable
contract. `mount=""` means the root; only `None` falls back to the code.
`app_snapshot` and `app_panel` supply generic monitor contributions. The monitor
also accepts an optional `panel_source` supplied by an application.

`RoutedApplication` combines that contract with `RoutingClass`. It plugs `auth`
at construction. On the first router access after attachment it calls
`server.arm_router`, which arms every plugin the server's configuration
resolves: the fixed `pydantic`/`openapi` pair and every extra the `plugins`
section enables. Branches use `add_branches`; the existing instance-form
consumers do not demonstrate the full declarative `cls`/`params` destination
described in the decisions.

`RoutedApplication.execute` is the single execution point every face reaches:
REST and WSX through `__call__`, an MCP `tools/call` through the transport, the
kbus through `__call__`. `RoutedApplication.discover` returns the neutral
`nodes()` tree the request may execute, which the OpenAPI schema and the MCP
`tools/list` read. Both call `authenticate_scope` first: a scope without `auth`
is authenticated through `server.authenticate`, and an invalid credential
raises 401. Both resolve with `auth_filters`, which passes the scope's channel
and the caller's tags. Tests:
`tests/core/test_unified_discovery.py::TestListedEqualsCallable::test_listed_equals_executable_on_both_faces`,
`tests/core/test_unified_discovery.py::TestInvalidCredential::test_execution_with_an_invalid_bearer_is_401`.

Each face has a channel name. `RoutedApplication.http_channel` is `"rest"`, and
`__call__` puts it on a scope that carries no `kajenn.channel`. WSX sets
`"wsx"` (`wsx.py:269`, `wsx.py:428`). The MCP transport sets `"mcp"`
(`applications/mcp.py:184`, `:204`). `McpOpenApiApplication` declares
`mcp_channel = "mcp"`, `rest_channel = "rest"` and `http_channel =
rest_channel` (`applications/mcp.py:401-403`).

No code passes a capabilities filter. The resolution filters on tags and
channel only. The routing-system status
carries the evidence: [025 status](../025_routing-system/status.md).

Claim anchors: [`BaseApplication`](../../../src/kajenn/application.py#L163), [`app_snapshot`](../../../src/kajenn/application.py#L353), [`app_panel`](../../../src/kajenn/application.py#L363), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L127), [`execute`](../../../src/kajenn/routed_application.py#L233), [`authenticate_scope`](../../../src/kajenn/routed_application.py#L284), [`discover`](../../../src/kajenn/routed_application.py#L300), [`auth_filters`](../../../src/kajenn/routed_application.py#L315).

## Well-known URLs and mixin grammars

An application declares the well-known URLs it answers in the class dict
`well_known`, public path → route. `BaseApplication.__init__` merges the dicts
along the MRO, base first, so the more derived class wins on the same path. A
`{mount}` marker is replaced by the instance's mount; on the root mount the
segment `/{mount}` is removed. A path that does not match `WELL_KNOWN_PATTERN`
raises `ValueError`. The server indexes what `active_well_known()` returns.
`RoutedApplication.active_well_known` checks every route against the router:
a route that names no entry, or that starts with a slash, raises `ValueError`.
A ruled entry counts like any other, and its auth rules answer the request.
Tests: `tests/core/test_well_known.py`, `tests/core/test_well_known_routes.py::test_the_route_auth_rules_answer_a_well_known_request`.

`BaseApplication.__init_subclass__` composes every `grammar` a class along the
MRO writes in its own `__dict__`, so an application accepts a mixin's recipe
words beside its own. Test:
`tests/core/test_grammar_composition.py::test_a_richer_grammar_and_a_mixin_grammar_compose`.

`MobileMixin` (`kajenn.mobile`, not exported from `kajenn`) adds the recipe
words `ios` and `android`. It declares `/.well-known/apple-app-site-association`
and `/.well-known/assetlinks.json`, each only when its word is written, and
answers them from `MobileSection`, attached under `mobile`. A missing required
word stops the boot. Tests: `tests/core/test_mobile.py`,
`tests/core/test_mobile_required_words.py`.

Claim anchors: [`well_known`](../../../src/kajenn/application.py#L182), [`__init_subclass__`](../../../src/kajenn/application.py#L184), [`active_well_known`](../../../src/kajenn/application.py#L343), [`RoutedApplication.active_well_known`](../../../src/kajenn/routed_application.py#L185), [`MobileMixin`](../../../src/kajenn/mobile.py#L192), [`MobileSection`](../../../src/kajenn/mobile.py#L107).

## Request parsing and handler arguments

`Request.init` owns headers, cookies, query parsing and body consumption. It
drains all body chunks even without a Content-Type. JSON, XML and MessagePack
are decoded with TYTX; URL-encoded bodies are split and
percent-decoded like the query string (`parse_qs`, UTF-8), values hydrated. Multipart text fields
are hydrated and files become `UploadedFile` values (`name`, `filename`,
`content_type`, `data`); repeated field names produce a list. Unknown media and
absent Content-Type are refused with 415, and a body that is not what its
content type declares with 400; an application declaring `request(body="raw")`
receives the bytes untouched instead, and decodes them itself (issue #87). The
request body is buffered in full.

`handler_kwargs` starts with query values. Form fields override colliding query
values; decoded non-form data becomes `body_data`, and the bytes of a raw
application become `body_raw`. `bind_kwargs` spreads a decoded dict over declared parameters unless
the handler accepts `body_data` or `**kwargs`. On the `mcp` channel the body is
always spread, whatever the handler declares (`routed_application.py:417-422`). A declared, unannotated `_request`
receives the live request in every `RoutedApplication`, not only `_server`.
This common opt-in seam was explicitly approved in N37 on 2026-09-06; its
relationship to the older D31 effects target remains recorded in the
[decision follow-up](decisions.md).

Claim anchors: [`Request`](../../../src/kajenn/request.py#L120), [`init`](../../../src/kajenn/request.py#L166), [`UploadedFile`](../../../src/kajenn/request.py#L102), [`content_type`](../../../src/kajenn/request.py#L362), [`handler_kwargs`](../../../src/kajenn/request.py#L451), [`bind_kwargs`](../../../src/kajenn/routed_application.py#L389), [`RoutedApplication`](../../../src/kajenn/routed_application.py#L127).

## Dispatch, authorization and failures

Async handlers run on the event loop. Sync handlers use `server.run_sync` and
run `route_cleanup` on that same thread even on failure. Router misses yield
404, anonymous access to a ruled route yields 401 with a `WWW-Authenticate:
Bearer` challenge, and insufficient tags yield 403. The error middleware
answers a 401 with the bare status and forwards the challenge; it never
redirects to a login page (`middleware/errors.py:32-36`). An HTTP request on a
method other than the one the entry declares with `openapi_method` yields 405
with an `Allow` header naming the declared method, and the handler does not
run (`routed_application.py:266-268`). Tests:
`tests/core/test_routed_application.py::test_anonymous_is_401_on_ruled_entry`,
`tests/core/test_route_method.py::TestDeclaredMethod::test_get_on_a_post_route_is_405_with_allow_and_never_runs`.

Signature mismatch yields 400. Values rejected by Pydantic after successful
binding yield the application's `validation_error_status`: 400 under the
default strict reading, 422 for an application declaring
`request(error_codes="fastapi")` (issue #87). An exception from the handler body, including `TypeError`,
propagates to the error middleware as 500. The `Response.set_error` helper's
standalone exception table must not be substituted for that dispatch contract.

Claim anchors: [`run_sync`](../../../src/kajenn/server.py#L398), [`route_cleanup`](../../../src/kajenn/routed_application.py#L374), [`Response`](../../../src/kajenn/response.py#L60), [`set_error`](../../../src/kajenn/response.py#L250), [`method_filters`](../../../src/kajenn/routed_application.py#L332).

Behavior evidence: [`__call__`](../../../src/kajenn/routed_application.py#L210), [`make_callable`](../../../src/kajenn/routed_application.py#L346).

## Buffered responses, streams and database cleanup

`Response` emits one start and one body message. `set_result` serializes
collections as JSON or the requested TYTX transport, handles text/bytes/paths,
and applies node metadata. A returned `StreamingResponse` instead emits chunks
without collecting them; `SseStream` provides framing, keepalive and iterator
cleanup. Streaming is a property of this process: an application served through
an adapter that buffers a whole response loses it, and the adapter says so.

`Request.db` and `Request.async_db` delegate to the bound application: the
database is taken from its handler once per name per call, and the handler's
`release(call)` runs at the end of the call on a pool thread — after a sync
route, from `route_cleanup` on the route's own thread. The database contract
is in [065_db](../065_db/status.md).

Claim anchors: [`Response`](../../../src/kajenn/response.py#L60), [`set_result`](../../../src/kajenn/response.py#L200), [`StreamingResponse`](../../../src/kajenn/streaming.py#L41), [`SseStream`](../../../src/kajenn/sse.py#L53), [`Request`](../../../src/kajenn/request.py#L120), [`db`](../../../src/kajenn/request.py#L432), [`async_db`](../../../src/kajenn/request.py#L440), [`route_cleanup`](../../../src/kajenn/routed_application.py#L374).

## WebSocket and package boundaries

Applications can receive WSX messages as synthetic HTTP scopes with method
`WSK`; the server can also hand an application its raw `serve_websocket` scope.
The HTTP middleware does not run per WSX message.

`OpenApiApplication`, `McpApplication` and `McpOpenApiApplication` ship in
`kajenn`; `ServerApplication` ships in `kajenn.server_app`. An application that
brings its own vocabulary arrives from its own distribution and is mounted by
class reference, so the core ships no list of the applications that exist.

An application declared with a `spawner` is mounted as a `RemoteApplication`
(`asgi_server.py:222-226`). It forwards each request over the kbus to the
spawned process that builds the real class, and answers 503 while that process
has not joined, 413 on a body over the limit and 502 on an error reply. Test:
`tests/core/test_external_application.py::test_a_mount_whose_process_has_not_joined_answers_503`.

The design's applications mounted and unmounted while the server runs are not
built: design.md §1 states that the installed set changes while the server runs
and that an application declares whether it can be moved and whether its
failure is survivable. `register_application` documents "the set of
applications is fixed at construction" (`server.py:244-251`), no method removes
an application, and `BaseApplication` carries no movability or survivability
attribute.

Behavior evidence: [`on_websocket`](../../../src/kajenn/server.py#L607), [`_call_application`](../../../src/kajenn/wsx.py#L370), [`OpenApiApplication`](../../../src/kajenn/applications/openapi.py#L67), [`McpApplication`](../../../src/kajenn/applications/mcp.py#L323), [`RemoteApplication`](../../../src/kajenn/remote_application.py#L139), [`register_application`](../../../src/kajenn/server.py#L244).

## Source and test evidence

- [src/kajenn/application.py](../../../src/kajenn/application.py)
- [src/kajenn/routed_application.py](../../../src/kajenn/routed_application.py)
- [src/kajenn/request.py](../../../src/kajenn/request.py)
- [src/kajenn/response.py](../../../src/kajenn/response.py)
- [src/kajenn/streaming.py](../../../src/kajenn/streaming.py)
- [src/kajenn/sse.py](../../../src/kajenn/sse.py)
- [src/kajenn/server.py](../../../src/kajenn/server.py)
- [tests/core/test_contract.py](../../../tests/core/test_contract.py)
- [tests/core/test_routed_application.py](../../../tests/core/test_routed_application.py)
- [tests/core/test_request.py](../../../tests/core/test_request.py)
- [tests/core/test_response.py](../../../tests/core/test_response.py)
- [tests/core/test_streaming.py](../../../tests/core/test_streaming.py)
- [tests/core/test_sse.py](../../../tests/core/test_sse.py)
- [tests/core/test_websocket_raw_seam.py](../../../tests/core/test_websocket_raw_seam.py)
