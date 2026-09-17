# MCP face — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Router-backed tools

`McpApplication` exposes a routing object as MCP tools. `McpOpenApiApplication`
combines the OpenAPI surface with MCP through the shared `McpEngine`. The engine
handles initialize, tool listing and calls over Streamable HTTP; it reads the
same router descriptions and applies auth tags and channel filters.

Sync tool handlers run through the server pool and async handlers stay on the
loop. Protocol/session bookkeeping and optional SSE push do not create a second
application state store. The task integration publishes lifecycle and progress
to the launching MCP session; the spool remains the durable snapshot source.

Claim anchors: [`McpApplication`](../../../../src/kajenn/applications/mcp.py#L274), [`McpOpenApiApplication`](../../../../src/kajenn/applications/mcp.py#L328), [`McpEngine`](../../../../src/kajenn/mcp/engine.py#L105).

## Source and test evidence

- [src/kajenn/applications/mcp.py](../../../../src/kajenn/applications/mcp.py)
- [src/kajenn/mcp/engine.py](../../../../src/kajenn/mcp/engine.py)
- [src/kajenn/tasks/hub.py](../../../../src/kajenn/tasks/hub.py)
- [tests/core/test_mcp_application.py](../../../../tests/core/test_mcp_application.py)
- [tests/core/test_mcp_engine.py](../../../../tests/core/test_mcp_engine.py)
- [tests/core/test_mcp_push.py](../../../../tests/core/test_mcp_push.py)
- [tests/core/mcp/test_mcp_dispatcher_tree.py](../../../../tests/core/mcp/test_mcp_dispatcher_tree.py)
