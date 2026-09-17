# CLI — current state

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `2465fcc` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Commands and instance records

The `kajenn` entry point is `kajenn.__main__:main`. It provides `serve`,
`apps`, `stop` and `remove`. `serve` loads the same recipe as Python server
construction; named instances record their location and process state under
the configured kajenn home and wire a session snapshot. `apps` reads that
registry; `stop` signals the recorded process; `remove` removes its registry
entry according to the command's checks.

`serve --reload` runs through `reloading.factory`, which marks the child
shutdown mode `QUITTING`, so an application's `on_shutdown` can tell a reload
from a real stop and preserve restart state. `--debug`
declares a usage mode and is not the reload mechanism. The default listener
and explicit/configured address precedence are shared with `AsgiServer`.

These commands do not implement the proposed general administrative restart
liturgy.

Claim anchors: [`apps`](../../../src/kajenn/__main__.py#L459), [`remove`](../../../src/kajenn/__main__.py#L127), [`remove`](../../../src/kajenn/__main__.py#L489), [`factory`](../../../src/kajenn/__main__.py#L498), [`AsgiServer`](../../../src/kajenn/asgi_server.py#L91).

Behavior evidence: [`ServerLauncher`](../../../src/kajenn/__main__.py#L212), [`AppsRegistry`](../../../src/kajenn/__main__.py#L88), [`TargetResolver`](../../../src/kajenn/__main__.py#L163).

## Source and test evidence

- [src/kajenn/__main__.py](../../../src/kajenn/__main__.py)
- [src/kajenn/reloading.py](../../../src/kajenn/reloading.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [tests/core/test_cli.py](../../../tests/core/test_cli.py)
- [tests/core/test_reloading.py](../../../tests/core/test_reloading.py)
