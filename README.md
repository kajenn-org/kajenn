# kajenn

**Status**: Pre-Alpha · version 0.0.0 reserves the name on PyPI. The server arrives with 0.1.0.

kajenn is an ASGI server with the features FastAPI leaves to the user — sessions, authentication,
websocket channels, tasks, MCP, storage — and a base server application included.

Based on genropy history and genro-modules.

## Packages in this distribution

| Import package | Content |
|---|---|
| `kajenn` | server core and app-side contract |
| `kajenn_server_app` | base server application, mounted by configuration |

The core never imports the server application. A contract test asserts it.

## Project

- Documentation: https://kajenn.org
- Organization: https://github.com/kajenn-org
- Orchestration: [kajenn-orchestra](https://github.com/kajenn-org/kajenn-orchestra)
- Django adapter: [kajenn-django](https://github.com/kajenn-org/kajenn-django)

## License

Apache License 2.0, copyright Softwell S.r.l.
