# Changelog

All notable changes to kajenn are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Releases before 0.9.0 are listed
on [GitHub Releases](https://github.com/kajenn-org/kajenn/releases).

## [Unreleased]

### Fixed

- An application with a catch-all route (`index(self, *segments, **query)`)
  starts again: only a route declared under the name `_meta` is refused, and
  `_meta/...` reaches the meta branch (#123).

## [0.9.0] - 2026-11-10

### Breaking

- `OpenApiApplication`, `McpOpenApiApplication`, `McpApplication` and the
  mounted mode (`routing_class`, `module`, `api_name`) are removed, with no
  alias. An application attaches its routing class in its own constructor
  and is a `RoutedApplication` (#111).
- The HTTP channel `rest` is renamed `api`. A route that declares no channel
  answers on `api` only (#111).
- A route named `_meta` is refused (#111).
- Every application but `_server` answers 503 with `Retry-After` until it is
  ready. The base class sends `ready` when its activation ends; an
  application that needs more overrides it (#112).
- An `on_startup` that raises leaves its application disabled instead of
  stopping the boot. An application whose failure must stop the server is
  declared `mandatory` (#112).

### Added

- Every `RoutedApplication` has the faces `_meta/index`,
  `_meta/api/schema_json`, `_meta/api/docs`, `_meta/mcp/tools` and answers
  MCP JSON-RPC on `_meta/mcp`. `_server` gains the same MCP face (#111).
- Applications added, switched off and removed on a running server: section
  `_server/applications` with `add`, `ready`, `disable`, `enable`, `remove`,
  `list`, administrator role. Writing or taking away a node of the
  `applications` branch of the live configuration does the same (#112).
- Hook `on_remove`, awaited when an application is removed (#112).
- Attribute `mandatory` on the `application` node: when a mandatory
  application fails, only `_server` answers and `/readyz` is not ready (#112).
- Option `abort_on_fatal_boot_error` on the `server` element (default
  `True`); with `False` a `FatalBootError` keeps the server up for `_server`
  only (#112).
- `server.application_failed`: an application gives up and removes itself
  (#112).

### Changed

- The 0.8.0 URLs `_meta/schema_json`, `_meta/docs` and `mcp` give the new
  answer with a `Deprecation` header, a `successor-version` link and a log
  line (#111).
- A disabled application answers 503 without `Retry-After`; a removed one
  answers 404 (#112).

### Fixed

- A GET on a POST route of an application answers 405 instead of 200: the
  `openapi` and `pydantic` plugins are attached before any route is resolved
  (#111).
