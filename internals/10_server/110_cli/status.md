# CLI — current state

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 evidence refreshed; design ratification unchanged

Verified against source revision `63fb326` (develop baseline). Test references
below identify the executable contracts; they are not a new coverage percentage.

## Commands and site cards

The `kajenn` entry point is `kajenn.__main__:main`. `Cli.parser` declares six
commands: `serve`, `configure`, `sites`, `stop`, `remove` and `db`. No `apps`
command exists.

- `serve` boots a server from a `config.py` path, a `template=<name>`
  assignment, an `application=<target>` assignment or a configured site name.
- `configure <name>` writes a site card and lays out the site home.
- `sites` lists the configured sites with status, address, home and source.
- `stop <name>` sends SIGTERM to the recorded pid.
- `remove <name>` drops a card and its pid file.
- `db plan|apply <source> <NAME>` reads or applies the migration of a declared
  database, on a configured server that is never started (`DatabaseCommand`).

The registry is `SitesRegistry`. It keeps one card per site in
`<base_dir>/sites/<name>.json` and one pid file per running site in
`<base_dir>/run/<name>.pid`. `base_dir` comes from `DefaultConfig`.
`SitesRegistry.read_pid` answers a pid only when a process with that pid is
alive (`os.kill(pid, 0)`); a missing, unreadable or dead pid file reads as not
running.

`sites` prints one line per card. With no card it prints the `configure`
command to run.

`remove` refuses a running site with "stop it first" and an unknown name with
"not registered" (test `test_remove_refuses_a_running_app`,
`test_remove_an_unregistered_name_is_an_error`).

Claim anchors: [`Cli.parser`](../../../src/kajenn/__main__.py#Cli.parser), [`sites`](../../../src/kajenn/__main__.py#Cli.sites), [`remove`](../../../src/kajenn/__main__.py#Cli.remove), [`SitesRegistry.remove`](../../../src/kajenn/__main__.py#SitesRegistry.remove), [`SitesRegistry.read_pid`](../../../src/kajenn/__main__.py#SitesRegistry.read_pid).

Behavior evidence: [`SitesRegistry`](../../../src/kajenn/__main__.py#SitesRegistry), [`ServerLauncher`](../../../src/kajenn/__main__.py#ServerLauncher), [`TargetResolver`](../../../src/kajenn/__main__.py#TargetResolver).

## `configure` and the site card

`SiteConfigurator` asks five questions: home folder, configuration template,
applications to mount, bind host and bind port. An answer given as an option is
not asked. With every option given the command runs without a prompt.

`SiteConfigurator.run` writes three things:

- the site home, laid out by `SiteHome.prepare`;
- the recipe `config.py` inside the home, generated from the chosen template
  and the declared application targets;
- the card `sites/<name>.json`, pointing at the home and the recipe.

`configure` refuses an unknown template, a single-file application target and
a home equal to the kajenn home (`base_dir`).

Tests: `TestConfigureWritesTheCardAndTheHome` in `tests/core/test_site_home.py`.

Claim anchors: [`SiteConfigurator`](../../../src/kajenn/__main__.py#SiteConfigurator), [`SiteConfigurator.run`](../../../src/kajenn/__main__.py#SiteConfigurator.run).

## `serve`: sources, refusals and identity

`ServerLauncher` treats a source that is neither an `application=` assignment,
a `template=` assignment nor an existing `.py` file as a site name.
`adopt_registered` reads its card: the stored source, home, host, port,
reload and debug replace the options the command line did not give.

`serve` refuses three cases with exit code 1:

- a name with no card: the message names `kajenn configure <name>` and lists the
  configured names (`adopt_registered`);
- a declared home that is not on disk: the message names `kajenn configure
  <name>` (`check_home`);
- a source that matches no accepted form (`build_server`).

Tests: `test_a_name_with_no_card_names_the_command_that_configures_it`,
`test_a_home_that_is_not_there_stops_the_boot` in `tests/core/test_site_home.py`;
`test_an_unknown_name_lists_the_configured_ones` in `tests/core/test_cli.py`.

`--home` overrides the home on the card. A bare file name of a carded site is
read inside its home (`resolved_source`). `template=<name>` builds
`AsgiServer(config=<name>)`; an unknown template name lists the known ones.

`adopt_site_identity` takes the site name and home the configuration declares
when the command line gave none. A configuration that names its site gets a
card on that boot. One that names none runs anonymous and files nothing
(`test_a_configuration_that_names_its_site_gets_a_card`,
`test_a_configuration_that_names_none_runs_anonymous`).

`ensure_importable` puts the directory of a `config.py` on `sys.path`, so a
recipe imports its sibling modules under the installed console script too.
`factory` re-inserts the same directory in the reloaded child
(`test_a_config_importing_a_sibling_module_resolves`,
`test_a_config_payload_with_a_sibling_import_resolves`).

A named `serve` writes the card, then the pid file, before uvicorn starts. With
`--reload` the recorded pid is the supervisor's. The `finally` of
`ServerLauncher.run` removes the pid file.

Claim anchors: [`adopt_registered`](../../../src/kajenn/__main__.py#ServerLauncher.adopt_registered), [`ensure_importable`](../../../src/kajenn/__main__.py#ServerLauncher.ensure_importable), [`build_server`](../../../src/kajenn/__main__.py#ServerLauncher.build_server), [`check_home`](../../../src/kajenn/__main__.py#ServerLauncher.check_home), [`adopt_site_identity`](../../../src/kajenn/__main__.py#ServerLauncher.adopt_site_identity), [`ServerLauncher.run`](../../../src/kajenn/__main__.py#ServerLauncher.run).

## `serve --role`

`--role application:<code>` builds `AsgiServer(config=<path>, role=<role>)`
from a `config.py` path and runs `server.run_role()`. The role form passes no
host, port, home, name, snapshot or debug to the server. It writes no card and
no pid file. `run_role` opens no HTTP listener: it reads the connection URL
from `KAJENN_KBUS_PARENT` and joins the server's kbus.

`SubprocessSpawner` is the caller of this form: it starts
`python -m kajenn serve <source> --role <role>`
(`test_the_token_is_not_on_the_command_line` in
`tests/core/test_external_application.py`).

Claim anchors: [`build_server` role branch](../../../src/kajenn/__main__.py#ServerLauncher.build_server), [`run_role`](../../../src/kajenn/kbus_mixin.py#KBusMixin.run_role).

## `serve --reload`

`--reload` calls `ServerLauncher.run_reloading`, which calls
`kajenn.reloading.serve_reloading`. `serve_reloading` writes the description of
the server as JSON into `KAJENN_LAUNCHER` and calls `uvicorn.run` on
`kajenn.__main__:factory` with `factory=True` and `reload=True`.

`factory` is defined in `kajenn/__main__.py`. `reloading.py` holds only its
import string, `FACTORY_TARGET`. `factory` rebuilds the server from
`KAJENN_LAUNCHER` and sets its `shutdown_mode` to `QUITTING`, so an application's
`on_shutdown` tells a reload from a real stop
(`test_the_reloaded_child_declares_its_exits_save`).

`serve_reloading` calls `uvicorn.run` without `timeout_graceful_shutdown`. A
server started with `--reload` therefore ignores `shutdown_timeout_seconds`.
uvicorn's default is `None`: its wait for open connections has no bound
(`uvicorn/server.py`, `asyncio.wait_for(..., timeout=None)`). `BaseServer.serve`
passes `timeout_graceful_shutdown=self.shutdown_timeout_seconds`; the reload
path does not reach `BaseServer.serve`.

`--debug` declares a usage mode and is not the reload mechanism.

Claim anchors: [`run_reloading`](../../../src/kajenn/__main__.py#ServerLauncher.run_reloading), [`factory`](../../../src/kajenn/__main__.py#factory), [`serve_reloading`](../../../src/kajenn/reloading.py#serve_reloading), [`uvicorn.run` call](../../../src/kajenn/reloading.py#serve_reloading), [`LAUNCHER_ENV`](../../../src/kajenn/reloading.py#LAUNCHER_ENV).

## Bind address

The address precedence is written twice, not shared. `ServerLauncher.address`
computes host and port for the boot: the explicit option, else the configured
value, else `127.0.0.1` and port `0`. `AsgiServer.serve` applies the same rule
in its own code. `ServerLauncher.address` exists because the reload supervisor
binds by itself and never calls `serve`.

Claim anchors: [`ServerLauncher.address`](../../../src/kajenn/__main__.py#ServerLauncher.address), [`AsgiServer.serve`](../../../src/kajenn/asgi_server.py#AsgiServer.serve), [`AsgiServer`](../../../src/kajenn/asgi_server.py#AsgiServer).

## `stop` and exit codes

`stop` sends SIGTERM and returns at once. It does not wait for the process to
end. The pid file is removed by the serving process, in the `finally` of
`ServerLauncher.run`. A process that ends between the liveness probe and the
signal raises `ProcessLookupError`; `stop` then removes the pid file and prints
"not running" (`test_stop_a_process_dying_between_probe_and_signal`). The
liveness probe checks only that some process holds the recorded pid.

Exit codes: 0 on success, 2 on usage errors raised by argparse, 1 on a
`CliError`, reported as one line on stderr (`Cli.run`;
`test_a_missing_subcommand_is_a_usage_error`).

Claim anchors: [`stop`](../../../src/kajenn/__main__.py#Cli.stop), [`Cli.run`](../../../src/kajenn/__main__.py#Cli.run).

## Distance from design.md

- design.md lines 7-8 list an `apps` command. The code has none: the listing
  command is `sites` (`Cli.parser`). design.md does not name `configure`,
  `sites` or `db`.

These commands do not implement the proposed general administrative restart
liturgy.

## Source and test evidence

- [src/kajenn/__main__.py](../../../src/kajenn/__main__.py)
- [src/kajenn/reloading.py](../../../src/kajenn/reloading.py)
- [src/kajenn/asgi_server.py](../../../src/kajenn/asgi_server.py)
- [src/kajenn/kbus_mixin.py](../../../src/kajenn/kbus_mixin.py)
- [tests/core/test_cli.py](../../../tests/core/test_cli.py)
- [tests/core/test_site_home.py](../../../tests/core/test_site_home.py)
- [tests/core/test_reloading.py](../../../tests/core/test_reloading.py)
- [tests/core/test_external_application.py](../../../tests/core/test_external_application.py)
