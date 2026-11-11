# MCP face — current state

**Version**: 0.3 · **Last Updated**: 2026-11-09 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against branch `feat/111-channel-faces` (0.9.0 work, based on `da0ecb5`). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Router-backed tools

Every `RoutedApplication` builds a `McpTransport` over its own router in
`__init__` and answers it on `_meta/mcp` (`routed_application.py:164`, `:264`).
The engine handles initialize, tool listing and calls over Streamable HTTP; it
reads the same router description and applies auth tags and channel filters.
`OpenApiApplication`, `McpOpenApiApplication` and `McpApplication` no longer
exist (0.9.0). The URL of 0.8.0, `mcp`, answers through `MOVED_URLS` with a
`Deprecation` header while the application has no route of that name
(`routed_application.py:106`).

`McpTransport` sets `kajenn.channel = "mcp"` on the MCP POST and on each
tool-call scope (`applications/mcp.py:173`, `:217`). `tools/list` reads the
application's `discover`, and `tools/call` runs through the application's
`execute`, so a tool is listed and called on the same filters as any other face.
`McpMeta.tools` answers the same list on `GET _meta/mcp/tools`
(`applications/mcp.py:336`). On the `mcp` channel `bind_kwargs` always spreads
the tool's `arguments` over the handler's parameters, whatever the handler
declares (`routed_application.py:511`). A request method other than GET and
POST answers 405. Tests:
`tests/core/test_unified_discovery.py::TestListedEqualsCallable::test_mcp_tools_list_lists_what_the_caller_may_call`,
`tests/core/test_mcp_application.py::TestMcpFace::test_other_method_is_method_not_allowed`,
`tests/core/test_mcp_application.py::TestRoutedApplication::test_tools_readable_with_a_plain_get`.

Sync tool handlers run through the server pool and async handlers stay on the
loop. Protocol/session bookkeeping and optional SSE push do not create a second
application state store. The task integration publishes lifecycle and progress
to the launching MCP session; the spool remains the durable snapshot source.

Claim anchors: [`RoutedApplication`](../../../../src/kajenn/routed_application.py#RoutedApplication), [`McpTransport`](../../../../src/kajenn/applications/mcp.py#McpTransport), [`McpMeta`](../../../../src/kajenn/applications/mcp.py#McpMeta), [`McpEngine`](../../../../src/kajenn/mcp/engine.py#McpEngine).

## Source and test evidence

- [src/kajenn/applications/mcp.py](../../../../src/kajenn/applications/mcp.py)
- [src/kajenn/mcp/engine.py](../../../../src/kajenn/mcp/engine.py)
- [src/kajenn/tasks/hub.py](../../../../src/kajenn/tasks/hub.py)
- [tests/core/test_mcp_application.py](../../../../tests/core/test_mcp_application.py)
- [tests/core/test_mcp_engine.py](../../../../tests/core/test_mcp_engine.py)
- [tests/core/test_unified_discovery.py](../../../../tests/core/test_unified_discovery.py)
- [tests/core/test_mcp_push.py](../../../../tests/core/test_mcp_push.py)
- [tests/core/mcp/test_mcp_dispatcher_tree.py](../../../../tests/core/mcp/test_mcp_dispatcher_tree.py)
