# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The ``kajenn`` command: boot a server, and manage the named ones.

Usage::

    kajenn serve ./config.py                      # a config.py recipe
    kajenn serve template=default                 # a ready-made configuration
    kajenn serve application=./hello.py:Hello     # one app, no config
    kajenn serve application=pkg.mod:App --name demo   # serve AND register
    kajenn serve demo                             # run the site named by its card
    kajenn configure demo --home /srv/demo        # write a card and lay out its home
    kajenn sites                                  # list the configured sites
    kajenn stop demo                              # stop a running site
    kajenn remove demo                            # drop a card

The ``serve`` source resolves in this order: an ``application=<target>``
assignment (quickstart — the target class is instantiated with no arguments and
handed to ``AsgiServer(applications=[...])``), a ``template=<name>`` assignment
(one of the ready-made configurations, ``CONFIGURATION_TEMPLATES``), an existing
``.py`` path (handed to ``AsgiServer(config=<absolute path>)``, whose contract is
the contrib handler's own: exactly one ``ConfigBuilder`` subclass defined in the
file — this command ships no loader and lets that error surface), otherwise a
NAME looked up in the registry.

Explicit ``--host``/``--port`` are forwarded as ``AsgiServer`` kwargs: the
server's own "explicit kwarg wins over the configured value" rule does the
precedence, this command computes nothing.

The registry under ``~/.kajenn`` stores ONE CARD per site
(``sites/<name>.json``: the home folder, the source and the saved options), never
a copy of the app, so running a name always runs the current code. A site home is
a folder whose every path is relative and named (``SiteHome``), and a card's
relative source is read inside it: ``serve demo`` runs ``<home>/config.py``. A
served name records its pid in ``run/<name>.pid`` so ``sites`` shows what is
running and ``stop`` can end it from another shell. A pid whose process is gone is
stale and reads as not running.

``configure <name>`` writes that card: it asks the home folder, the configuration
template, the applications to mount and the listener, and every answer given as an
option is not asked — with all of them given it runs without a prompt, which is
what Docker and Kubernetes need. It lays the home out (``SiteHome.prepare``) and
writes its ``config.py``. It is the ONLY command that creates anything: ``serve``
refuses a name with no card and a card whose home is not on disk, naming
``configure`` in both messages.

``serve <path>`` files a card when the CONFIGURATION names its site (the ``site``
section, or the ``site_name`` attribute of the recipe that writes it), so the next
boot is ``serve <name>``. A configuration that names none runs anonymous.

``--reload`` runs under uvicorn's reload supervisor, which accepts only an import
string — never a built instance. The source therefore crosses the process
boundary as one JSON object in ``KAJENN_LAUNCHER``, and ``factory()`` rebuilds
the very same server on every restart. Those two derogations (a module-level
function, state in the environment) are confined to this module.

Exit codes: 0 success, 2 usage errors (argparse), 1 runtime errors — reported as
one line on stderr.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable


from .asgi_server import AsgiServer
from .lifespan import QUITTING
from .reloading import LAUNCHER_ENV, serve_reloading
from .site_home import SiteHome
from .config.default_config import DefaultConfig
from .config.templates import CONFIGURATION_TEMPLATES, DEFAULT_TEMPLATE

__all__ = [
    "LAUNCHER_ENV",
    "SitesRegistry",
    "CliError",
    "Cli",
    "ServerLauncher",
    "SiteConfigurator",
    "TargetResolver",
    "factory",
    "main",
]

class CliError(Exception):
    """A runtime error the command reports as one stderr line and exit code 1."""


class SitesRegistry:
    """The ``~/.kajenn`` store: the site cards and the pids of the running ones.

    ONE card per site, ``sites/<name>.json``: the home folder the site owns, the
    configuration source it runs (a file inside that home, a ``template=`` name
    or an ``application=`` target) and the serve options. Resolving a name means
    reading its card — no search order and no precedence.

    The directory is also where a deployment keeps its defaults layer, so
    ``base_dir`` comes from ``DefaultConfig`` — one default for both, one
    ``KAJENN_HOME`` relocating both, and one parameter a test can point at a
    temporary directory.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.default_config = DefaultConfig(base_dir)
        self.base_dir = self.default_config.base_dir
        self.sites_dir = self.base_dir / "sites"
        self.run_dir = self.base_dir / "run"
        self.sessions_dir = self.base_dir / "sessions"

    def card_path(self, name: str) -> Path:
        return self.sites_dir / f"{name}.json"

    def pid_path(self, name: str) -> Path:
        return self.run_dir / f"{name}.pid"

    def save(self, name: str, entry: dict) -> None:
        """Register (or update) *name* with its serve options."""
        self.sites_dir.mkdir(parents=True, exist_ok=True)
        self.card_path(name).write_text(json.dumps(entry, indent=2), encoding="utf-8")

    def load(self, name: str) -> dict | None:
        """The registration stored for *name*, ``None`` when there is none."""
        path = self.card_path(name)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def names(self) -> list[str]:
        """The registered names, sorted."""
        if not self.sites_dir.is_dir():
            return []
        return sorted(path.stem for path in self.sites_dir.glob("*.json"))

    def remove(self, name: str) -> bool:
        """Drop *name*'s registration and any leftover pidfile. ``False`` if absent."""
        path = self.card_path(name)
        if not path.is_file():
            return False
        path.unlink()
        self.clear_pid(name)
        return True

    def write_pid(self, name: str, pid: int) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path(name).write_text(str(pid), encoding="utf-8")

    def clear_pid(self, name: str) -> None:
        self.pid_path(name).unlink(missing_ok=True)

    def read_pid(self, name: str) -> int | None:
        """The recorded pid IF its process is alive — the file is never trusted.

        A pidfile that is missing, unreadable or names a dead process all read
        the same way: not running.
        """
        path = self.pid_path(name)
        if not path.is_file():
            return None
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return None
        return pid


class TargetResolver:
    """Resolves an application target to its class.

    Two spellings: ``package.module:ClassName`` (a plain import) and
    ``path/to/file.py:ClassName`` (a single file, no packaging needed).
    """

    def __init__(self, target: str) -> None:
        self.target = target

    @property
    def parts(self) -> tuple[str, str]:
        """The module part and the class name; a target without ``:`` is an error."""
        module_part, separator, class_name = self.target.partition(":")
        if not (separator and module_part and class_name):
            raise CliError(
                f"application target must be 'package.module:ClassName' or "
                f"'path/to/file.py:ClassName', got {self.target!r}"
            )
        return module_part, class_name

    def resolve(self) -> type:
        """The target class itself."""
        module_part, class_name = self.parts
        if module_part.endswith(".py"):
            module = self.load_file(module_part)
        else:
            module = importlib.import_module(module_part)
        app_class = getattr(module, class_name, None)
        if app_class is None:
            raise CliError(f"{module_part} does not define {class_name!r}")
        return app_class

    def load_file(self, module_part: str) -> ModuleType:
        """Import a single ``.py`` file as a module of its own."""
        path = Path(module_part).resolve()
        if not path.is_file():
            raise CliError(f"application file not found: {path}")
        spec = importlib.util.spec_from_file_location(f"kajenn_target_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise CliError(f"cannot load application module: {path}")
        module = importlib.util.module_from_spec(spec)
        # Registered BEFORE exec (importlib contract): the app class must be able
        # to find its own module through ``sys.modules[cls.__module__]``.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


class ServerLauncher:
    """One ``serve`` invocation: resolves its source, builds the server, boots it.

    A source that is neither a quickstart assignment nor an existing ``.py`` file
    is a registered name, adopted at construction: its stored source and options
    replace the ones the command line did not give.
    """

    def __init__(self, options: argparse.Namespace, registry: SitesRegistry) -> None:
        self.registry = registry
        self.source = options.source
        self.name = options.name
        self.home = SiteHome(options.home) if options.home else None
        self.host = options.host
        self.port = options.port
        self.reload = options.reload
        self.debug = options.debug
        if not (self.is_quickstart or self.is_template or self.is_config_path):
            self.adopt_registered(self.source)

    @property
    def resolved_source(self) -> str:
        """The source as a path: a bare file name of a carded site lives in its home.

        An absolute source and the two assignments pass through — ``Path("/a") /
        "/b"`` is ``/b``, so no branch is needed for the absolute case.
        """
        if self.home is None or self.is_quickstart or self.is_template:
            return self.source
        return str(self.home.path / self.source)

    @property
    def is_quickstart(self) -> bool:
        return self.source.startswith("application=")

    @property
    def is_template(self) -> bool:
        """True when the source names one of the ready-made configurations."""
        return self.source.startswith("template=")

    @property
    def template_name(self) -> str:
        """The template the source names; an unknown one is a usage error."""
        name = self.source.partition("=")[2]
        if name not in CONFIGURATION_TEMPLATES:
            known = ", ".join(sorted(CONFIGURATION_TEMPLATES))
            raise CliError(f"unknown configuration template {name!r} (known: {known})")
        return name

    @property
    def is_config_path(self) -> bool:
        return self.resolved_source.endswith(".py") and Path(self.resolved_source).is_file()

    @property
    def entry(self) -> dict:
        """The card stored under ``--name``: the home, the source and the options.

        The source is stored AS GIVEN, so a site whose home moves keeps running
        the same relative recipe.
        """
        return {
            "source": self.source,
            "home": str(self.home) if self.home is not None else None,
            "host": self.host,
            "port": self.port,
            "reload": bool(self.reload),
            "debug": self.debug,
        }

    @property
    def server_kwargs(self) -> dict:
        """The explicitly-given host/port only — an absent key keeps the config's."""
        kwargs: dict = {}
        if self.host is not None:
            kwargs["host"] = self.host
        if self.port is not None:
            kwargs["port"] = self.port
        return kwargs

    @property
    def save_session_path(self) -> str | None:
        """The session snapshot file a NAMED serve arms, ``None`` for a nameless one.

        Giving the instance a name IS the switch: sessions of ``--name demo``
        survive a restart through ``<home>/data/sessions/demo.pickle``, or
        ``<base_dir>/sessions/demo.pickle`` when the site has no home.
        """
        if not self.name:
            return None
        folder = self.home.sessions if self.home is not None else self.registry.sessions_dir
        return str(folder / f"{self.name}.pickle")

    @property
    def constructor_kwargs(self) -> dict:
        """What ``AsgiServer(...)`` receives: the site, host/port, the snapshot."""
        kwargs = dict(self.server_kwargs)
        if self.home is not None:
            kwargs["site_home"] = str(self.home)
        if self.name:
            kwargs["site_name"] = self.name
        if self.save_session_path is not None:
            kwargs["save_session"] = self.save_session_path
        if self.debug is not False:
            kwargs["debug"] = self.debug
        return kwargs

    @property
    def quickstart_target(self) -> str:
        """The ``application=`` target, a file spelling made absolute."""
        module_part, class_name = TargetResolver(self.source.partition("=")[2]).parts
        if module_part.endswith(".py"):
            module_part = str(Path(module_part).resolve())
        return f"{module_part}:{class_name}"

    @property
    def launcher_payload(self) -> dict:
        """What ``factory`` needs to rebuild this server in the reloaded process.

        One source key (``application`` or ``config``, always absolute) plus the
        explicitly-given host/port and the armed session snapshot: an absent
        key lets the config's own value apply, exactly as it does here.
        """
        payload = dict(self.constructor_kwargs)
        if self.is_quickstart:
            payload["application"] = self.quickstart_target
        elif self.is_template:
            payload["config"] = self.template_name
        else:
            payload["config"] = str(Path(self.resolved_source).resolve())
        return payload

    @property
    def reload_dir(self) -> str:
        """The directory uvicorn watches: the one holding the source file.

        A dotted target has no file of its own to anchor on, so the working
        directory is watched instead.
        """
        if self.is_template:
            return str(Path.cwd())
        module_part = (
            self.quickstart_target.partition(":")[0] if self.is_quickstart else self.resolved_source
        )
        if module_part.endswith(".py"):
            return str(Path(module_part).resolve().parent)
        return str(Path.cwd())

    def adopt_registered(self, name: str) -> None:
        """Replace the source and the unset options with the ones stored under *name*."""
        stored = self.registry.load(name)
        if stored is None:
            known = ", ".join(self.registry.names()) or "none configured"
            raise CliError(
                f"{name} is not a configured site, run 'kajenn configure {name}' "
                f"(configured: {known})"
            )
        self.name = self.name or name
        self.source = stored["source"]
        if self.home is None and stored.get("home"):
            self.home = SiteHome(stored["home"])
        if self.host is None:
            self.host = stored.get("host")
        if self.port is None:
            self.port = stored.get("port")
        if not self.reload:
            self.reload = bool(stored.get("reload"))
        if self.debug is False:
            self.debug = stored.get("debug", False)

    def ensure_importable(self, directory: Path) -> None:
        """Put *directory* on ``sys.path`` so a config.py can import its siblings.

        ``python -m kajenn`` puts the working directory there by itself; the
        installed console script does not — without this, ``from hello import
        Hello`` inside a config.py resolves under one invocation and not the
        other.
        """
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))

    def build_server(self) -> AsgiServer:
        """The server this source describes, host/port forwarded when given.

        A declared home must ALREADY be there: laying one out is ``configure``'s
        job and nobody else's, so a home that is not on disk stops the boot with
        the command that creates it instead of starting an empty site.
        """
        self.check_home()
        if self.is_quickstart:
            app_class = TargetResolver(self.source.partition("=")[2]).resolve()
            return AsgiServer(applications=[app_class], **self.constructor_kwargs)
        if self.is_template:
            return AsgiServer(config=self.template_name, **self.constructor_kwargs)
        if self.is_config_path:
            config_path = Path(self.resolved_source).resolve()
            self.ensure_importable(config_path.parent)
            return AsgiServer(config=str(config_path), **self.constructor_kwargs)
        raise CliError(
            f"cannot serve {self.resolved_source!r}: not an existing config.py path, "
            "not a 'template=<name>' assignment, "
            "not an 'application=<target>' assignment"
        )

    def check_home(self) -> None:
        """Stop when the declared home is not on disk."""
        if self.home is None or self.home.path.is_dir():
            return
        name = self.name or self.source
        raise CliError(
            f"the home of {name} is not there: {self.home.path} — "
            f"run 'kajenn configure {name}' to lay it out"
        )

    def adopt_site_identity(self, server: AsgiServer) -> None:
        """Take the identity the CONFIGURATION declares, when the command line gave none.

        A configuration that names its site is a site the machine recognises: its
        card is written on this boot, so the next one is ``serve <name>``. One
        that names none runs anonymous and files nothing.
        """
        if self.name is None and server.site_name:
            self.name = server.site_name
        if self.home is None and server.site_home is not None:
            self.home = server.site_home

    def address(self, server: AsgiServer) -> tuple[str, int]:
        """The address this boot binds: the explicit option, else what the server has.

        The same rule ``AsgiServer.serve`` applies — spelled out here because the
        reload supervisor binds by itself and never calls ``serve``.
        """
        host = self.host if self.host is not None else (server.config_host or "127.0.0.1")
        port = self.port if self.port is not None else (server.config_port or 0)
        return host, port

    def run_reloading(self, host: str, port: int) -> None:
        """Boot under the reload supervisor, with this CLI's own derivations.

        The derivation is the CLI's convenience and stays here: the watch root
        is the source file's directory. The launch itself is the package's
        public one (``reloading``, #39) — any other launcher reaches it with
        roots of its own choosing.
        """
        payload = self.launcher_payload
        serve_reloading(
            host=host,
            port=port,
            reload_dirs=[self.reload_dir],
            config=payload.get("config"),
            application=payload.get("application"),
            save_session=payload.get("save_session"),
            site_name=payload.get("site_name"),
            site_home=payload.get("site_home"),
            debug=payload.get("debug", False),
        )

    def run(self) -> int:
        """Boot the server (blocking), filing the card and the pid first."""
        server = self.build_server()
        self.adopt_site_identity(server)
        if self.name:
            self.registry.save(self.name, self.entry)
            # The pidfile goes down BEFORE uvicorn starts: with --reload this
            # records the supervisor, which is the process ``stop`` must signal.
            self.registry.write_pid(self.name, os.getpid())
        host, port = self.address(server)
        print(f"kajenn serving http://{host}:{port}", flush=True)
        try:
            if self.reload:
                self.run_reloading(host, port)
            else:
                server.serve(**self.server_kwargs)
        except KeyboardInterrupt:
            print("Shutdown.")
        finally:
            if self.name:
                self.registry.clear_pid(self.name)
        return 0


RECIPE_HEADER = '''"""Configuration of the {name} site, written by ``kajenn configure``."""

from kajenn.config import {template_class}
{imports}

class ServerConfiguration({template_class}):
    """The {name} site: its home, its listener, its applications."""

    site_name = "{name}"
    site_home = "{home}"

    def server_section(self, cfg):
        """The listener."""
        cfg.server(host="{host}", port={port})
'''
"""The recipe ``configure`` writes into the home. One class, its home declared."""

RECIPE_APPLICATIONS = '''
    def applications_section(self, cfg):
        """The applications this site mounts."""
        apps = cfg.applications()
{mounts}
'''
"""The applications override, appended only when the operator named some."""


class SiteConfigurator:
    """The ``configure`` command: the questions of a card, asked or given as options.

    Every field has a question and a proposed answer. An option given on the
    command line answers its field and no question is asked for it, so a fully
    optioned invocation runs without a prompt — which is what an init container
    needs. What it writes: the home with its folders and its ``config.py``, and
    the card ``sites/<name>.json`` pointing at both.
    """

    questions = (
        ("home", "site home folder"),
        ("template", "configuration template to start from"),
        ("applications", "applications to mount (comma-separated package.module:Class)"),
        ("host", "bind host"),
        ("port", "bind port"),
    )

    def __init__(
        self,
        options: argparse.Namespace,
        registry: SitesRegistry,
        ask: Callable[[str], str] = input,
    ) -> None:
        self.name = options.name
        self.options = options
        self.registry = registry
        self.ask = ask

    @property
    def proposals(self) -> dict[str, str]:
        """What each question proposes when the operator just presses enter."""
        return {
            "home": str(Path.cwd() / self.name),
            "template": DEFAULT_TEMPLATE,
            "applications": "",
            "host": "127.0.0.1",
            "port": "8000",
        }

    def answer(self, field: str, question: str) -> str:
        """The value of *field*: the given option, else the asked question."""
        given = getattr(self.options, field)
        if given is not None:
            return str(given)
        proposed = self.proposals[field]
        return self.ask(f"{question} [{proposed}]: ").strip() or proposed

    @property
    def answers(self) -> dict[str, str]:
        """Every field of the card, in the order the questions are asked."""
        return {field: self.answer(field, question) for field, question in self.questions}

    def application_entries(self, declared: str) -> list[tuple[str, str]]:
        """The declared targets as ``(module, class)`` pairs.

        Only importable targets: the recipe is a Python file that imports what it
        mounts, and a single-file target has no import line to write.
        """
        entries = [TargetResolver(part.strip()).parts for part in declared.split(",") if part.strip()]
        for module_part, _ in entries:
            if module_part.endswith(".py"):
                raise CliError(
                    f"configure mounts importable targets only "
                    f"('package.module:ClassName'), got {module_part!r}"
                )
        return entries

    def recipe_source(self, home: SiteHome, answers: dict[str, str]) -> str:
        """The ``config.py`` this site starts from: the template, narrowed to it."""
        entries = self.application_entries(answers["applications"])
        template_class = CONFIGURATION_TEMPLATES[answers["template"]].__name__
        source = RECIPE_HEADER.format(
            name=self.name,
            template_class=template_class,
            imports="".join(f"from {module} import {klass}\n" for module, klass in entries),
            home=home.path,
            host=answers["host"],
            port=int(answers["port"]),
        )
        if not entries:
            return source
        mounts = "\n".join(
            f'        apps.application(code="{klass.lower()}", app_class={klass})'
            for _, klass in entries
        )
        return source + RECIPE_APPLICATIONS.format(mounts=mounts)

    def card(self, home: SiteHome, answers: dict[str, str]) -> dict:
        """The card written under the site's name."""
        return {
            "source": home.recipe_name,
            "home": str(home),
            "host": answers["host"],
            "port": int(answers["port"]),
            "reload": False,
            "debug": False,
        }

    def run(self) -> int:
        """Ask what was not given, lay the home out, write the recipe and the card."""
        answers = self.answers
        if answers["template"] not in CONFIGURATION_TEMPLATES:
            known = ", ".join(sorted(CONFIGURATION_TEMPLATES))
            raise CliError(f"unknown configuration template {answers['template']!r} (known: {known})")
        home = SiteHome(answers["home"])
        if home.path == self.registry.base_dir:
            raise CliError(
                f"the site home cannot be the kajenn home {self.registry.base_dir}: "
                "the installation keeps the cards and the defaults layer there"
            )
        source = self.recipe_source(home, answers)
        home.prepare()
        home.recipe.write_text(source, encoding="utf-8")
        self.registry.save(self.name, self.card(home, answers))
        print(f"{self.name}: configured in {home}")
        return 0


class Cli:
    """The command: one parser, one subcommand method each, one exit code."""

    def __init__(self, registry: SitesRegistry | None = None) -> None:
        self.registry = registry or SitesRegistry()

    def parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="kajenn", description="Run and manage ASGI servers.")
        commands = parser.add_subparsers(dest="command", required=True)

        serve = commands.add_parser(
            "serve", help="run a server from a config.py, a template, a target or a name"
        )
        serve.add_argument(
            "source",
            help="a config.py path, 'template=<name>', 'application=<target>', "
            "or a registered name",
        )
        serve.add_argument(
            "--home", help="the site home folder (overrides the one on the card)"
        )
        serve.add_argument("--host", help="bind host (overrides the configured one)")
        serve.add_argument("--port", type=int, help="bind port (overrides the configured one)")
        serve.add_argument("--reload", action="store_true", help="restart on source changes")
        serve.add_argument(
            "--debug",
            nargs="?",
            const=True,
            default=False,
            help="declare the server runs in debug mode (optional comma-separated parameters)",
        )
        serve.add_argument("--name", help="register the server under this name")
        serve.set_defaults(handler=self.serve)

        configure = commands.add_parser(
            "configure", help="write a site card and lay out its home folder"
        )
        configure.add_argument("name", help="the name the site is filed under")
        configure.add_argument("--home", help="the site home folder")
        configure.add_argument("--template", help="the configuration template to start from")
        configure.add_argument(
            "--applications", help="comma-separated 'package.module:ClassName' targets to mount"
        )
        configure.add_argument("--host", help="bind host")
        configure.add_argument("--port", type=int, help="bind port")
        configure.set_defaults(handler=self.configure)

        commands.add_parser("sites", help="list the configured sites").set_defaults(
            handler=self.sites
        )

        stop = commands.add_parser("stop", help="stop a running registered server")
        stop.add_argument("name")
        stop.set_defaults(handler=self.stop)

        remove = commands.add_parser("remove", help="drop a registration")
        remove.add_argument("name")
        remove.set_defaults(handler=self.remove)
        return parser

    def run(self, argv: list[str] | None = None) -> int:
        options = self.parser().parse_args(argv)
        try:
            return options.handler(options)
        except CliError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    def serve(self, options: argparse.Namespace) -> int:
        return ServerLauncher(options, self.registry).run()

    def configure(self, options: argparse.Namespace) -> int:
        return SiteConfigurator(options, self.registry).run()

    def sites(self, options: argparse.Namespace) -> int:
        names = self.registry.names()
        if not names:
            print("No configured sites (configure one with: kajenn configure <name>)")
            return 0
        for name in names:
            card = self.registry.load(name) or {}
            pid = self.registry.read_pid(name)
            status = f"running (pid {pid})" if pid else "stopped"
            address = f"{card.get('host') or '-'}:{card.get('port') or '-'}"
            home = card.get("home") or "-"
            print(f"{name:<20} {status:<20} {address:<24} {home:<40} {card.get('source') or '-'}")
        return 0

    def stop(self, options: argparse.Namespace) -> int:
        pid = self.registry.read_pid(options.name)
        if pid is None:
            self.registry.clear_pid(options.name)
            print(f"{options.name}: not running")
            return 0
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # The process died between the liveness probe and the signal
            # (TOCTOU): same outcome as finding it already stopped.
            self.registry.clear_pid(options.name)
            print(f"{options.name}: not running")
            return 0
        print(f"{options.name}: stopped (pid {pid})")
        return 0

    def remove(self, options: argparse.Namespace) -> int:
        if self.registry.read_pid(options.name) is not None:
            raise CliError(f"{options.name} is running — stop it first")
        if not self.registry.remove(options.name):
            raise CliError(f"{options.name}: not registered")
        print(f"{options.name}: removed")
        return 0


def factory() -> AsgiServer:
    """Rebuild the server described in ``KAJENN_LAUNCHER``.

    The import-string target of the reload supervisor, and the only module-level
    function here: uvicorn imports it by name in each restarted process, where
    nothing of the parent survives except the environment.
    """
    payload = os.environ.get(LAUNCHER_ENV)
    if payload is None:
        raise CliError(f"{LAUNCHER_ENV} is not set — factory() runs only under 'kajenn serve --reload'")
    try:
        described = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CliError(f"{LAUNCHER_ENV} is not valid JSON: {error}") from error
    kwargs = {
        key: described[key]
        for key in ("host", "port", "save_session", "site_name", "site_home", "debug")
        if key in described
    }
    if "application" in described:
        server = AsgiServer(
            applications=[TargetResolver(described["application"]).resolve()], **kwargs
        )
    elif "config" in described:
        # The reloaded process starts fresh: the sibling-import path the parent
        # inserted (ServerLauncher.ensure_importable) must be re-inserted here.
        # A template name has no file and no siblings to reach.
        parent = str(Path(described["config"]).parent)
        if described["config"].endswith(".py") and parent not in sys.path:
            sys.path.insert(0, parent)
        server = AsgiServer(config=described["config"], **kwargs)
    else:
        raise CliError(f"{LAUNCHER_ENV} carries neither an 'application' nor a 'config' key")
    # Under the reload supervisor every exit is a deliberate save: the child is
    # killed at each source change, and the next one adopts what this one froze
    # (dev-reload auto-soft, the orientations' §4).
    server.shutdown_mode = QUITTING
    return server


def main(argv: list[str] | None = None) -> int:
    """The ``kajenn`` console entry point."""
    return Cli().run(argv)


if __name__ == "__main__":
    sys.exit(main())
