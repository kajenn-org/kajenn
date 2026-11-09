# OpenAPI face — current state

**Version**: 0.3 · **Last Updated**: 2026-11-09 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against branch `feat/111-channel-faces` (0.9.0 work, based on `da0ecb5`). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Schema and interactive documentation

Every `RoutedApplication` attaches the branch `_meta` in `__init__`, an
`ApplicationMeta` instance, and refuses a route of its own named `_meta`
(`routed_application.py:164`). Under it: `_meta/index`, `_meta/api/schema_json`,
`_meta/api/docs` (Swagger UI, 404 when `docs_style` is `"off"`) and
`_meta/mcp/tools`. `router_openapi` and the translator produce OpenAPI 3.1 from
the router's neutral description, including parameter schemas, auth
requirements, capability extensions and per-route OpenAPI metadata.
`schema_json` builds the document from the application's `discover` on the
request's scope, so the request's channel and the caller's tags filter the
schema as they filter execution. The URLs of 0.8.0, `_meta/schema_json` and
`_meta/docs`, give the same answer with a `Deprecation` header
(`routed_application.py:106`). A route that declares no channel answers on
`api` only. There is no mounted mode: an application attaches a routing class
in its own constructor. Tests:
`tests/core/test_unified_discovery.py::TestListedEqualsCallable::test_openapi_schema_lists_what_the_caller_may_call`,
`tests/core/test_openapi_application.py::TestMovedUrls`,
`tests/core/test_openapi_application.py::TestReservedMeta`.

A method declared with `openapi_method` is enforced: an HTTP request on another
method answers 405 with an `Allow` header naming the declared method, and the
handler does not run (`routed_application.py:361`). Test:
`tests/core/test_route_method.py::TestDeclaredMethod::test_post_on_a_get_route_is_405_naming_get`.
A route declaring no method answers every method, and the schema shows the
method guessed from its signature. The root configuration grammar's
`openapi` section is not consumed to configure this application; use the
application's actual constructor/class metadata.

Claim anchors: [`ApplicationMeta`](../../../../src/kajenn/applications/openapi.py#L54), [`ApiMeta`](../../../../src/kajenn/applications/openapi.py#L83), [`schema_json`](../../../../src/kajenn/applications/openapi.py#L90), [`router_openapi`](../../../../src/kajenn/plugins/openapi/__init__.py#L39).

## Source and test evidence

- [src/kajenn/applications/openapi.py](../../../../src/kajenn/applications/openapi.py)
- [src/kajenn/plugins/openapi/__init__.py](../../../../src/kajenn/plugins/openapi/__init__.py)
- [src/kajenn/plugins/openapi/translator.py](../../../../src/kajenn/plugins/openapi/translator.py)
- [tests/core/test_openapi_application.py](../../../../tests/core/test_openapi_application.py)
- [tests/core/test_plugins.py](../../../../tests/core/test_plugins.py)
- [tests/core/test_route_method.py](../../../../tests/core/test_route_method.py)
- [tests/core/test_unified_discovery.py](../../../../tests/core/test_unified_discovery.py)
