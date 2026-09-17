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

"""CLI tests: the site cards, target resolution, and the built (never booted) server.

No server is ever started here: ``ServerLauncher.build_server`` is exercised on
its own, so a recipe error surfaces as a boot error exactly as it does in
``test_config.py``. The registry always gets ``tmp_path`` as its base_dir.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import pytest

from kajenn.__main__ import (
    LAUNCHER_ENV,
    SitesRegistry,
    Cli,
    CliError,
    ServerLauncher,
    TargetResolver,
    factory,
)

CONFIG_RECIPE = (
    "from kajenn.config import AsgiConfigBuilder\n"
    "\n"
    "\n"
    "class ServerConfiguration(AsgiConfigBuilder):\n"
    "    def main(self, root):\n"
    "        cfg = root.configuration()\n"
    "        cfg.server(host='10.0.0.1', port=8321)\n"
)

APP_MODULE = (
    "from kajenn.application import BaseApplication\n"
    "\n"
    "\n"
    "class Hello(BaseApplication):\n"
    "    pass\n"
)


def parse(cli: Cli, argv: list[str]):
    """The parsed options of one invocation (no handler is called)."""
    return cli.parser().parse_args(argv)


class TestSitesRegistry:
    def test_save_load_and_names(self, tmp_path: Path) -> None:
        registry = SitesRegistry(base_dir=tmp_path)
        registry.save("beta", {"source": "./b.py", "host": None, "port": None, "reload": False})
        registry.save("alpha", {"source": "./a.py", "host": "0.0.0.0", "port": 9000, "reload": True})
        assert registry.names() == ["alpha", "beta"]
        stored = registry.load("alpha")
        assert stored is not None and stored["port"] == 9000
        assert registry.load("missing") is None

    def test_names_with_no_store_yet(self, tmp_path: Path) -> None:
        assert SitesRegistry(base_dir=tmp_path / "nothing").names() == []

    def test_remove_drops_entry_and_pidfile(self, tmp_path: Path) -> None:
        registry = SitesRegistry(base_dir=tmp_path)
        registry.save("demo", {"source": "./a.py"})
        registry.write_pid("demo", os.getpid())
        assert registry.remove("demo") is True
        assert registry.names() == []
        assert not registry.pid_path("demo").is_file()
        assert registry.remove("demo") is False

    def test_a_live_pid_reads_back(self, tmp_path: Path) -> None:
        registry = SitesRegistry(base_dir=tmp_path)
        registry.write_pid("demo", os.getpid())
        assert registry.read_pid("demo") == os.getpid()

    def test_a_stale_pidfile_reads_as_not_running(self, tmp_path: Path) -> None:
        registry = SitesRegistry(base_dir=tmp_path)
        registry.run_dir.mkdir(parents=True)
        registry.pid_path("demo").write_text("999999999", encoding="utf-8")
        assert registry.read_pid("demo") is None

    def test_an_unreadable_pidfile_reads_as_not_running(self, tmp_path: Path) -> None:
        registry = SitesRegistry(base_dir=tmp_path)
        registry.run_dir.mkdir(parents=True)
        registry.pid_path("demo").write_text("not-a-pid", encoding="utf-8")
        assert registry.read_pid("demo") is None
        assert registry.read_pid("never-written") is None


class TestTargetResolver:
    def test_dotted_target(self) -> None:
        resolved = TargetResolver("kajenn.application:BaseApplication").resolve()
        assert resolved.__name__ == "BaseApplication"

    def test_file_target(self, tmp_path: Path) -> None:
        module = tmp_path / "hello.py"
        module.write_text(APP_MODULE)
        resolved = TargetResolver(f"{module}:Hello").resolve()
        assert resolved.__name__ == "Hello"

    def test_a_target_without_colon_is_an_error(self) -> None:
        with pytest.raises(CliError, match="package.module:ClassName"):
            TargetResolver("kajenn.application").resolve()

    def test_a_missing_file_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(CliError, match="application file not found"):
            TargetResolver(f"{tmp_path / 'absent.py'}:Hello").resolve()

    def test_an_undefined_class_is_an_error(self) -> None:
        with pytest.raises(CliError, match="does not define 'Nope'"):
            TargetResolver("kajenn.application:Nope").resolve()


class TestServeSourceResolution:
    def test_a_config_py_path_builds_a_configured_server(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", str(module)]), cli.registry)
        server = launcher.build_server()
        assert server.config_host == "10.0.0.1"
        assert server.config_port == 8321

    def test_a_config_importing_a_sibling_module_resolves(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # The console script — unlike ``python -m`` — does not put the working
        # directory on sys.path: the launcher must insert the config's own one.
        (tmp_path / "sibling_app.py").write_text(APP_MODULE)
        module = tmp_path / "config.py"
        module.write_text(
            "from kajenn.config import AsgiConfigBuilder\n"
            "from sibling_app import Hello\n"
            "\n"
            "\n"
            "class ServerConfiguration(AsgiConfigBuilder):\n"
            "    def main(self, root):\n"
            "        cfg = root.configuration()\n"
            "        cfg.applications().application(code='hello', mount='', app_class=Hello)\n"
        )
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(tmp_path)])
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", str(module)]), cli.registry)
        server = launcher.build_server()
        assert "hello" in server.applications

    def test_explicit_host_and_port_win_over_the_recipe(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(cli, ["serve", str(module), "--host", "0.0.0.0", "--port", "7000"])
        server = ServerLauncher(options, cli.registry).build_server()
        assert (server.config_host, server.config_port) == ("0.0.0.0", 7000)

    def test_a_quickstart_target_builds_the_named_application(self, tmp_path: Path) -> None:
        module = tmp_path / "hello.py"
        module.write_text(APP_MODULE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(cli, ["serve", f"application={module}:Hello"])
        server = ServerLauncher(options, cli.registry).build_server()
        assert "Hello" in {type(app).__name__ for app in server.applications.values()}

    def test_a_template_name_builds_the_ready_made_configuration(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(cli, ["serve", "template=default", "--port", "7100"])
        server = ServerLauncher(options, cli.registry).build_server()
        assert server.config.node("storage") is not None
        assert server.config_port == 7100

    def test_an_unknown_template_names_the_known_ones(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", "template=ghost"]), cli.registry)
        with pytest.raises(CliError, match="unknown configuration template 'ghost'"):
            launcher.build_server()

    def test_a_template_travels_by_name_through_the_reload_payload(
        self, tmp_path: Path
    ) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", "template=default"]), cli.registry)
        assert launcher.launcher_payload["config"] == "default"
        assert launcher.reload_dir == str(Path.cwd())

    def test_an_unknown_name_lists_the_configured_ones(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": "./a.py"})
        with pytest.raises(
            CliError,
            match="ghost is not a configured site, run 'kajenn configure ghost' "
            "\\(configured: demo\\)",
        ):
            ServerLauncher(parse(cli, ["serve", "ghost"]), cli.registry)

    def test_an_unknown_name_with_an_empty_registry(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        with pytest.raises(CliError, match="none configured"):
            ServerLauncher(parse(cli, ["serve", "ghost"]), cli.registry)

    def test_a_registered_name_restores_source_and_options(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save(
            "demo", {"source": str(module), "host": "127.0.0.5", "port": 9111, "reload": True}
        )
        launcher = ServerLauncher(parse(cli, ["serve", "demo"]), cli.registry)
        assert (launcher.source, launcher.host, launcher.port) == (str(module), "127.0.0.5", 9111)
        assert launcher.reload is True
        assert launcher.name == "demo"
        assert launcher.build_server().config_port == 9111

    def test_a_command_line_option_wins_over_the_stored_one(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": str(module), "host": "127.0.0.5", "port": 9111})
        launcher = ServerLauncher(parse(cli, ["serve", "demo", "--port", "9500"]), cli.registry)
        assert (launcher.host, launcher.port) == ("127.0.0.5", 9500)

    def test_a_stored_source_that_no_longer_exists_is_an_error(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": str(tmp_path / "gone.py")})
        launcher = ServerLauncher(parse(cli, ["serve", "demo"]), cli.registry)
        with pytest.raises(CliError, match="cannot serve"):
            launcher.build_server()


class TestArgumentParsing:
    def test_serve_options_become_the_registry_entry(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(
            cli, ["serve", str(module), "--host", "0.0.0.0", "--port", "8080", "--reload", "--name", "demo"]
        )
        launcher = ServerLauncher(options, cli.registry)
        assert launcher.entry == {
            "source": str(module),
            "home": None,
            "host": "0.0.0.0",
            "port": 8080,
            "reload": True,
            "debug": False,
        }
        assert launcher.server_kwargs == {"host": "0.0.0.0", "port": 8080}

    def test_no_option_given_forwards_no_kwarg(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", str(module)]), cli.registry)
        assert launcher.server_kwargs == {}


class TestSaveSessionWiring:
    """Naming an instance IS the switch: the snapshot file takes the name."""

    def test_a_named_serve_arms_the_snapshot(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", str(module), "--name", "demo"]), cli.registry)
        expected = str(tmp_path / "sessions" / "demo.pickle")
        assert launcher.save_session_path == expected
        assert launcher.constructor_kwargs == {"save_session": expected, "site_name": "demo"}
        assert launcher.server_kwargs == {}  # serve() never sees the snapshot kwarg
        assert launcher.build_server().save_session == Path(expected)

    def test_a_nameless_serve_stays_volatile(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", str(module)]), cli.registry)
        assert launcher.save_session_path is None
        assert "save_session" not in launcher.constructor_kwargs
        assert launcher.build_server().save_session is None

    def test_relaunching_a_registered_name_arms_the_same_file(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": str(module)})
        launcher = ServerLauncher(parse(cli, ["serve", "demo"]), cli.registry)
        assert launcher.save_session_path == str(tmp_path / "sessions" / "demo.pickle")

    def test_a_missing_subcommand_is_a_usage_error(self) -> None:
        with pytest.raises(SystemExit) as exit_info:
            Cli().run([])
        assert exit_info.value.code == 2


class TestRegistryCommands:
    def test_sites_reports_nothing_configured(self, tmp_path: Path, capsys) -> None:
        assert Cli(registry=SitesRegistry(base_dir=tmp_path)).run(["sites"]) == 0
        assert "No configured sites" in capsys.readouterr().out

    def test_sites_reports_status_source_and_address(self, tmp_path: Path, capsys) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": "./a.py", "host": "0.0.0.0", "port": 8080})
        cli.registry.save("idle", {"source": "./b.py", "host": None, "port": None})
        cli.registry.write_pid("demo", os.getpid())
        assert cli.run(["sites"]) == 0
        out = capsys.readouterr().out
        assert f"running (pid {os.getpid()})" in out
        assert "0.0.0.0:8080" in out
        assert "./a.py" in out
        assert "stopped" in out
        assert "-:-" in out

    def test_stop_signals_the_recorded_pid(self, tmp_path: Path, capsys, monkeypatch) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.write_pid("demo", os.getpid())
        signalled: list[tuple[int, int]] = []
        original = os.kill

        def spy(pid: int, sig: int) -> None:
            if sig == 0:  # the liveness probe stays real
                original(pid, sig)
            else:
                signalled.append((pid, sig))

        monkeypatch.setattr(os, "kill", spy)
        assert cli.run(["stop", "demo"]) == 0
        assert signalled == [(os.getpid(), signal.SIGTERM)]
        assert "stopped" in capsys.readouterr().out

    def test_stop_a_process_dying_between_probe_and_signal(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        # TOCTOU: the probe (sig 0) sees the process alive, the SIGTERM finds
        # it gone — same outcome as finding it already stopped, one line out.
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.write_pid("demo", os.getpid())
        original = os.kill

        def dies_after_probe(pid: int, sig: int) -> None:
            if sig == 0:
                original(pid, sig)
            else:
                raise ProcessLookupError(pid)

        monkeypatch.setattr(os, "kill", dies_after_probe)
        assert cli.run(["stop", "demo"]) == 0
        assert not cli.registry.pid_path("demo").is_file()
        assert "not running" in capsys.readouterr().out

    def test_stop_a_dead_app_cleans_the_stale_pidfile(self, tmp_path: Path, capsys) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.run_dir.mkdir(parents=True)
        cli.registry.pid_path("demo").write_text("999999999", encoding="utf-8")
        assert cli.run(["stop", "demo"]) == 0
        assert not cli.registry.pid_path("demo").is_file()
        assert "not running" in capsys.readouterr().out

    def test_remove_refuses_a_running_app(self, tmp_path: Path, capsys) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": "./a.py"})
        cli.registry.write_pid("demo", os.getpid())
        assert cli.run(["remove", "demo"]) == 1
        assert "stop it first" in capsys.readouterr().err
        assert cli.registry.names() == ["demo"]

    def test_remove_drops_a_stopped_app(self, tmp_path: Path, capsys) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        cli.registry.save("demo", {"source": "./a.py"})
        assert cli.run(["remove", "demo"]) == 0
        assert cli.registry.names() == []
        assert "removed" in capsys.readouterr().out

    def test_remove_an_unregistered_name_is_an_error(self, tmp_path: Path, capsys) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        assert cli.run(["remove", "ghost"]) == 1
        assert "not registered" in capsys.readouterr().err


class TestReloadPayload:
    """What crosses the process boundary — the supervisor itself is not started."""

    def test_a_config_source_travels_as_an_absolute_path(self, tmp_path: Path, monkeypatch) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        monkeypatch.chdir(tmp_path)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", "config.py", "--reload"]), cli.registry)
        assert launcher.launcher_payload == {"config": str(module.resolve())}
        assert launcher.reload_dir == str(tmp_path.resolve())

    def test_a_file_target_travels_absolute_too(self, tmp_path: Path, monkeypatch) -> None:
        module = tmp_path / "hello.py"
        module.write_text(APP_MODULE)
        monkeypatch.chdir(tmp_path)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launcher = ServerLauncher(parse(cli, ["serve", "application=hello.py:Hello"]), cli.registry)
        assert launcher.launcher_payload == {"application": f"{module.resolve()}:Hello"}
        assert launcher.reload_dir == str(tmp_path.resolve())

    def test_a_dotted_target_watches_the_working_directory(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(cli, ["serve", "application=kajenn.application:BaseApplication"])
        launcher = ServerLauncher(options, cli.registry)
        payload = launcher.launcher_payload
        assert payload == {"application": "kajenn.application:BaseApplication"}
        assert launcher.reload_dir == str(tmp_path.resolve())

    def test_only_the_explicit_host_and_port_are_carried(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(cli, ["serve", str(module), "--reload", "--port", "7100"])
        payload = ServerLauncher(options, cli.registry).launcher_payload
        assert payload == {"config": str(module.resolve()), "port": 7100}

    def test_the_reload_boot_exports_the_payload_and_binds_the_configured_address(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        launched: dict = {}
        monkeypatch.setattr(
            "kajenn.reloading.uvicorn.run",
            lambda target, **kwargs: launched.update(target=target, **kwargs),
        )
        # monkeypatch owns the variable, so the value the launcher writes is
        # restored after the test instead of leaking into the environment.
        monkeypatch.setenv(LAUNCHER_ENV, "")
        assert cli.run(["serve", str(module), "--reload", "--name", "demo"]) == 0
        assert json.loads(os.environ[LAUNCHER_ENV]) == {
            "config": str(module.resolve()),
            "save_session": str(tmp_path / "sessions" / "demo.pickle"),
            "site_name": "demo",
            "host": "10.0.0.1",
            "port": 8321,
        }
        assert launched == {
            "target": "kajenn.__main__:factory",
            "factory": True,
            "reload": True,
            "reload_dirs": [str(tmp_path.resolve())],
            "reload_excludes": None,
            "host": "10.0.0.1",
            "port": 8321,
        }
        assert not cli.registry.pid_path("demo").is_file()
        assert "10.0.0.1:8321" in capsys.readouterr().out


class TestFactory:
    """The rebuild on the far side of the boundary."""

    def test_a_config_payload_rebuilds_the_configured_server(self, tmp_path: Path, monkeypatch) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps({"config": str(module)}))
        server = factory()
        assert (server.config_host, server.config_port) == ("10.0.0.1", 8321)

    def test_an_application_payload_rebuilds_the_named_application(self, tmp_path: Path, monkeypatch) -> None:
        module = tmp_path / "hello.py"
        module.write_text(APP_MODULE)
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps({"application": f"{module}:Hello"}))
        server = factory()
        assert "Hello" in {type(app).__name__ for app in server.applications.values()}

    def test_explicit_host_and_port_in_the_payload_win_over_the_recipe(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        payload = {"config": str(module), "host": "0.0.0.0", "port": 7100}
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps(payload))
        server = factory()
        assert (server.config_host, server.config_port) == ("0.0.0.0", 7100)

    def test_a_config_payload_with_a_sibling_import_resolves(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # The reloaded process starts fresh: factory() re-inserts the config's
        # directory exactly as the parent's launcher did.
        (tmp_path / "sibling_reload.py").write_text(APP_MODULE.replace("Hello", "Hot"))
        module = tmp_path / "config.py"
        module.write_text(
            "from kajenn.config import AsgiConfigBuilder\n"
            "from sibling_reload import Hot\n"
            "\n"
            "\n"
            "class ServerConfiguration(AsgiConfigBuilder):\n"
            "    def main(self, root):\n"
            "        cfg = root.configuration()\n"
            "        cfg.applications().application(code='hot', mount='', app_class=Hot)\n"
        )
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(tmp_path)])
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps({"config": str(module)}))
        server = factory()
        assert "hot" in server.applications

    def test_a_missing_variable_names_it(self, monkeypatch) -> None:
        monkeypatch.delenv(LAUNCHER_ENV, raising=False)
        with pytest.raises(CliError, match=f"{LAUNCHER_ENV} is not set"):
            factory()

    def test_malformed_json_is_an_error(self, monkeypatch) -> None:
        monkeypatch.setenv(LAUNCHER_ENV, "{not json")
        with pytest.raises(CliError, match="is not valid JSON"):
            factory()

    def test_a_payload_without_a_source_key_is_an_error(self, monkeypatch) -> None:
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps({"host": "0.0.0.0"}))
        with pytest.raises(CliError, match="neither an 'application' nor a 'config' key"):
            factory()

    def test_save_session_in_the_payload_arms_the_snapshot(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        snapshot = str(tmp_path / "sessions" / "demo.pickle")
        payload = {"config": str(module), "save_session": snapshot}
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps(payload))
        assert factory().save_session == Path(snapshot)


class TestDebugAndReloadTrigger:
    def test_debug_travels_from_the_flag_to_the_server(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        options = parse(cli, ["serve", str(module), "--debug", "sql,timing"])
        server = ServerLauncher(options, cli.registry).build_server()
        assert server.debug == "sql,timing"

    def test_a_bare_debug_flag_reads_true_and_its_absence_false(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path))
        flagged = parse(cli, ["serve", str(module), "--debug"])
        plain = parse(cli, ["serve", str(module)])
        assert ServerLauncher(flagged, cli.registry).build_server().debug is True
        assert ServerLauncher(plain, cli.registry).build_server().debug is False

    def test_the_reloaded_child_declares_its_exits_save(self, tmp_path: Path, monkeypatch) -> None:
        from kajenn.lifespan import QUITTING, STOPPING

        module = tmp_path / "config.py"
        module.write_text(CONFIG_RECIPE)
        monkeypatch.setenv(LAUNCHER_ENV, json.dumps({"config": str(module), "debug": True}))
        server = factory()
        assert server.shutdown_mode == QUITTING
        assert server.debug is True
        # and outside the supervisor the default stays the dry exit
        from kajenn import BaseServer
        from kajenn.application import BaseApplication
        assert BaseServer(applications=[BaseApplication(mount="")]).shutdown_mode == STOPPING
