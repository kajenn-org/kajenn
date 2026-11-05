# MCP face — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Router-backed tools

`McpApplication` exposes a routing object as MCP tools. `McpOpenApiApplication`
combines the OpenAPI surface with MCP through the shared `McpEngine`. The engine
handles initialize, tool listing and calls over Streamable HTTP; it reads the
same router descriptions and applies auth tags and channel filters.

`McpTransport` sets `kajenn.channel = "mcp"` on the MCP POST and on each
tool-call scope (`applications/mcp.py:184`, `:204`). `tools/list` reads the
application's `discover`, and `tools/call` runs through the application's
`execute`, so a tool is listed and called on the same filters as any other face.
On the `mcp` channel `bind_kwargs` always spreads the tool's `arguments` over
the handler's parameters, whatever the handler declares
(`routed_application.py:417-422`). A request method other than GET and POST
answers 405. Tests:
`tests/core/test_unified_discovery.py::TestListedEqualsCallable::test_mcp_tools_list_lists_what_the_caller_may_call`,
`tests/core/test_mcp_application.py::TestMcpApplication::test_other_method_is_method_not_allowed`.

Sync tool handlers run through the server pool and async handlers stay on the
loop. Protocol/session bookkeeping and optional SSE push do not create a second
application state store. The task integration publishes lifecycle and progress
to the launching MCP session; the spool remains the durable snapshot source.

Claim anchors: [`McpApplication`](../../../../src/kajenn/applications/mcp.py#L323), [`McpOpenApiApplication`](../../../../src/kajenn/applications/mcp.py#L387), [`McpTransport`](../../../../src/kajenn/applications/mcp.py#L82), [`McpEngine`](../../../../src/kajenn/mcp/engine.py#L106).

## Source and test evidence

- [src/kajenn/applications/mcp.py](../../../../src/kajenn/applications/mcp.py)
- [src/kajenn/mcp/engine.py](../../../../src/kajenn/mcp/engine.py)
- [src/kajenn/tasks/hub.py](../../../../src/kajenn/tasks/hub.py)
- [tests/core/test_mcp_application.py](../../../../tests/core/test_mcp_application.py)
- [tests/core/test_mcp_engine.py](../../../../tests/core/test_mcp_engine.py)
- [tests/core/test_unified_discovery.py](../../../../tests/core/test_unified_discovery.py)
- [tests/core/test_mcp_push.py](../../../../tests/core/test_mcp_push.py)
- [tests/core/mcp/test_mcp_dispatcher_tree.py](../../../../tests/core/mcp/test_mcp_dispatcher_tree.py)
