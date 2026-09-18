# kajenn

[![PyPI](https://img.shields.io/pypi/v/kajenn)](https://pypi.org/project/kajenn/)
[![Tests](https://github.com/kajenn-org/kajenn/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/kajenn-org/kajenn/actions/workflows/tests.yml)
[![Codecov](https://codecov.io/gh/kajenn-org/kajenn/branch/main/graph/badge.svg)](https://app.codecov.io/gh/kajenn-org/kajenn)
[![Documentation](https://readthedocs.org/projects/kajenn/badge/?version=latest)](https://kajenn.readthedocs.io/en/latest/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/kajenn-org/kajenn/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/branding/kajenn-logo-dark.png">
    <img src="assets/branding/kajenn-logo.png" alt="kajenn" width="200">
  </picture>
</p>

*A spicy ASGI application server.*

**Status**: Pre-Alpha. This README describes the `main` checkout; the PyPI badge
shows the published package version. For the examples documented here, follow
the [checkout installation instructions](docs/getting-started.md#installation).

kajenn is an ASGI application server with the features FastAPI leaves to the user — sessions, authentication,
websocket channels, tasks, MCP, storage — and a base server application included.

Based on genropy history and genro-modules.

## Why kajenn?

Pronounced “KAY-jen”, kajenn plays on cayenne and Cajun: spicy, with rhythm.

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
