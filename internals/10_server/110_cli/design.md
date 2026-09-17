# CLI

**Version**: 0.1 · **Last Updated**: 2026-08-22 · **Status**: 🔴 DA REVISIONARE

**The need.** An admin drives installations from the shell: start one by name, see what runs, stop it, remove it.

The `kajenn` command: `serve` (by instance name, with `--reload` for
development), `apps`, `stop`, `remove`; the instance registry behind them.
The CLI reads the same recipe the server reads — it adds no words of its
own.

Interactions: configuration (the recipe and the instance names) · restart (the stop/serve pair is today's only restart).
