# OpenAPI face — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Schema and interactive documentation

`OpenApiApplication` extends `RoutedApplication`. It can attach an existing
routing object or import a routing class, then exposes its schema and Swagger
page through the same application. `router_openapi` and the translator produce
OpenAPI 3.1 from the router's neutral description, including parameter schemas,
auth requirements, capability extensions and per-route OpenAPI metadata.

The meta endpoints hang under the branch `_meta`, attached by
`OpenApiApplication.__init__` as an `OpenApiMeta` instance in both modes:
`_meta/schema_json`, `_meta/docs` (Swagger UI, 404 when `docs_style` is
`"off"`) and `_meta/index`. `schema_json` builds the document from the
application's `discover` on the request's scope, so the request's channel and
the caller's tags filter the schema as they filter execution. Test:
`tests/core/test_unified_discovery.py::TestListedEqualsCallable::test_openapi_schema_lists_what_the_caller_may_call`.

A method declared with `openapi_method` is enforced: an HTTP request on another
method answers 405 with an `Allow` header naming the declared method, and the
handler does not run (`routed_application.py:266-268`). Test:
`tests/core/test_route_method.py::TestDeclaredMethod::test_post_on_a_get_route_is_405_naming_get`.
A route declaring no method answers every method, and the schema shows the
method guessed from its signature. The root configuration grammar's
`openapi` section is not consumed to configure this application; use the
application's actual constructor/class metadata.

Claim anchors: [`OpenApiApplication`](../../../../src/kajenn/applications/openapi.py#L67), [`OpenApiMeta`](../../../../src/kajenn/applications/openapi.py#L137), [`schema_json`](../../../../src/kajenn/applications/openapi.py#L144), [`router_openapi`](../../../../src/kajenn/plugins/openapi/__init__.py#L39).

## Source and test evidence

- [src/kajenn/applications/openapi.py](../../../../src/kajenn/applications/openapi.py)
- [src/kajenn/plugins/openapi/__init__.py](../../../../src/kajenn/plugins/openapi/__init__.py)
- [src/kajenn/plugins/openapi/translator.py](../../../../src/kajenn/plugins/openapi/translator.py)
- [tests/core/test_openapi_application.py](../../../../tests/core/test_openapi_application.py)
- [tests/core/test_plugins.py](../../../../tests/core/test_plugins.py)
- [tests/core/test_route_method.py](../../../../tests/core/test_route_method.py)
- [tests/core/test_unified_discovery.py](../../../../tests/core/test_unified_discovery.py)
