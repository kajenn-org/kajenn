# CLI — decisions

**Version**: 0.2 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

## Still open

**S1 [commands] — design.md names an `apps` command the code does not have.**
design.md lines 7-8 list `serve`, `apps`, `stop` and `remove`. `Cli.parser`
(`src/kajenn/__main__.py`) declares `serve`, `configure`, `sites`, `stop`,
`remove` and `db`. The listing command is `sites`. design.md does not name
`configure`, `sites` or `db`. The owner has not said which list is the target.

**S2 [recipe] — design.md says the CLI adds no words of its own; `configure` writes them.**
design.md lines 9-10 say the CLI reads the same recipe the server reads and adds
no words of its own. `SiteConfigurator.run` writes a recipe `config.py` into the
site home and a card `sites/<name>.json` with the source, home, host, port,
reload and debug. `serve <name>` reads that card and its values replace the
options the command line did not give (`ServerLauncher.adopt_registered`). The
card is a store the server itself does not read.

**S3 [tense] — design.md line 12 uses the tense of the implementation.**
design.md line 12 says "the stop/serve pair is today's only restart". The
overview rule "One tense per document" bars "today" from design.md; the present
state belongs in status.md. The code also restarts in two other ways: `serve --reload` rebuilds
the server on every source change (`factory`), and `SubprocessSpawner.relaunch`
starts again the process of an external application.
