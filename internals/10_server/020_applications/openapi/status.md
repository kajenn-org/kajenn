# OpenAPI face — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Schema and interactive documentation

`OpenApiApplication` extends `RoutedApplication`. It can attach an existing
routing object or import a routing class, then exposes its schema and Swagger
page through the same application. `router_openapi` and the translator produce
OpenAPI 3.1 from the router's neutral description, including parameter schemas,
auth requirements, capability extensions and per-route OpenAPI metadata.

The schema's method is descriptive: the HTTP dispatcher does not reject a call
solely because it arrived using another verb. The root configuration grammar's
`openapi` section is not consumed to configure this application; use the
application's actual constructor/class metadata.

Claim anchors: [`OpenApiApplication`](../../../../src/kajenn/applications/openapi.py#L64), [`router_openapi`](../../../../src/kajenn/plugins/openapi/__init__.py#L39).

## Source and test evidence

- [src/kajenn/applications/openapi.py](../../../../src/kajenn/applications/openapi.py)
- [src/kajenn/plugins/openapi/__init__.py](../../../../src/kajenn/plugins/openapi/__init__.py)
- [src/kajenn/plugins/openapi/translator.py](../../../../src/kajenn/plugins/openapi/translator.py)
- [tests/core/test_openapi_application.py](../../../../tests/core/test_openapi_application.py)
- [tests/core/test_plugins.py](../../../../tests/core/test_plugins.py)
