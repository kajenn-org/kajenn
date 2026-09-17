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

"""The site home: one way to identify a site, in every deployment context (#92).

Three things are pinned here: the LAYOUT (the named paths inside the folder),
the ROAD the home takes to the configuration (recipe attribute, shortcut kwarg,
card) and the RESOLUTION of the installation root in the five contexts. No
server is booted: ``build_server`` is exercised on its own, as in
``test_cli.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kajenn import AsgiServer, SiteHome
from kajenn.__main__ import Cli, CliError, ServerLauncher, SiteConfigurator, SitesRegistry
from kajenn.config import DefaultConfig, DefaultConfiguration

ANONYMOUS_RECIPE = (
    "from kajenn.config import DefaultConfiguration\n"
    "\n"
    "\n"
    "class ServerConfiguration(DefaultConfiguration):\n"
    "    pass\n"
)

HOME_RECIPE = (
    "from kajenn.config import DefaultConfiguration\n"
    "\n"
    "\n"
    "class ServerConfiguration(DefaultConfiguration):\n"
    "    site_name = 'shop'\n"
    "    site_home = {home!r}\n"
)


def configure(cli: Cli, argv: list[str], ask=None) -> int:
    """Run ``configure`` with the given command line, optionally answering it."""
    options = cli.parser().parse_args(argv)
    return SiteConfigurator(options, cli.registry, ask=ask or refuse_to_ask).run()


def refuse_to_ask(question: str) -> str:
    """The stub of a silent run: being asked anything at all is the failure."""
    raise AssertionError(f"asked a question with every option given: {question!r}")


class TestTheLayoutOfAHome:
    """Inside the home every path is relative and named."""

    def test_every_path_hangs_from_the_folder_by_name(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        assert home.recipe == tmp_path / "shop" / "config.py"
        assert home.static == tmp_path / "shop" / "static"
        assert home.data == tmp_path / "shop" / "data"
        assert home.frozen_users == tmp_path / "shop" / "data" / "frozen_users"
        assert home.sessions == tmp_path / "shop" / "data" / "sessions"
        assert home.sockets == tmp_path / "shop" / "sockets"
        assert home.logs == tmp_path / "shop" / "logs"
        assert home.run == tmp_path / "shop" / "run"

    def test_prepare_lays_out_an_empty_folder(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        assert [folder.is_dir() for folder in home.folders] == [True] * 7

    def test_prepare_leaves_a_laid_out_home_alone(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        (home.static / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
        home.prepare()
        assert (home.static / "robots.txt").read_text(encoding="utf-8") == "User-agent: *\n"

    def test_the_folder_is_absolute_and_expanded(self, tmp_path: Path) -> None:
        assert SiteHome("~/shop").path == Path.home() / "shop"
        assert SiteHome(tmp_path / "shop") == SiteHome(str(tmp_path / "shop"))


class TestTheHomeReachesTheConfiguration:
    """The home is a configuration word: nothing reads it from anywhere else."""

    def test_a_recipe_declares_it_and_the_server_reads_it_back(self, tmp_path: Path) -> None:
        SiteHome(tmp_path / "shop").prepare()

        class Recipe(DefaultConfiguration):
            site_name = "shop"
            site_home = str(tmp_path / "shop")

        server = AsgiServer(config=Recipe)
        assert server.config("site.home") == str(tmp_path / "shop")
        assert server.site_name == "shop"
        assert server.site_home == SiteHome(tmp_path / "shop")

    def test_the_shortcut_writes_it_into_the_tree_like_any_other_option(
        self, tmp_path: Path
    ) -> None:
        SiteHome(tmp_path / "shop").prepare()
        server = AsgiServer(site_name="shop", site_home=str(tmp_path / "shop"))
        assert server.config("site.name") == "shop"
        assert server.config("site.home") == str(tmp_path / "shop")
        assert server.site_home == SiteHome(tmp_path / "shop")

    def test_a_homeless_server_has_no_site_section_at_all(self) -> None:
        server = AsgiServer()
        assert server.site_home is None
        assert server.site_name is None
        assert server.config.node("site") is None

    def test_the_home_anchors_the_home_volume_and_site_stays_where_it_was(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        deployment = tmp_path / "checkout"
        deployment.mkdir()
        monkeypatch.chdir(deployment)

        class Recipe(DefaultConfiguration):
            site_home = str(tmp_path / "shop")

        server = AsgiServer(config=Recipe)
        server.storage.node("home:probe.txt").write_text("kept")
        server.storage.node("site:probe.txt").write_text("code")
        assert (home.path / "probe.txt").read_text(encoding="utf-8") == "kept"
        assert (deployment / "probe.txt").read_text(encoding="utf-8") == "code"

    def test_the_shortcut_anchors_the_same_two_volumes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        deployment = tmp_path / "checkout"
        deployment.mkdir()
        monkeypatch.chdir(deployment)
        server = AsgiServer(site_home=str(home))
        server.storage.node("home:probe.txt").write_text("kept")
        server.storage.node("site:probe.txt").write_text("code")
        assert (home.path / "probe.txt").read_text(encoding="utf-8") == "kept"
        assert (deployment / "probe.txt").read_text(encoding="utf-8") == "code"

    def test_with_no_home_the_two_volumes_coincide(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        server = AsgiServer()
        server.storage.node("site:probe.txt").write_text("here")
        assert server.storage.node("home:probe.txt").read_text() == "here"

    def test_the_named_paths_of_the_home_are_reachable_on_the_home_volume(
        self, tmp_path: Path
    ) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        server = AsgiServer(site_home=str(home))
        server.storage.node("home:data/sessions/probe.pickle").write_text("snapshot")
        assert (home.sessions / "probe.pickle").read_text(encoding="utf-8") == "snapshot"

    def test_an_explicit_kwarg_wins_over_the_configured_home(self, tmp_path: Path) -> None:
        SiteHome(tmp_path / "configured").prepare()
        SiteHome(tmp_path / "given").prepare()

        class Recipe(DefaultConfiguration):
            site_home = str(tmp_path / "configured")

        server = AsgiServer(config=Recipe, site_home=str(tmp_path / "given"))
        assert server.site_home == SiteHome(tmp_path / "given")


class TestTheInstallationRootInFiveContexts:
    """One mechanism, five values — no search order, no precedence to learn."""

    contexts = {
        "development": "{tmp}/home/developer/.kajenn",
        "classic production": "{tmp}/srv/genro/.kajenn",
        "virtualenv": "{tmp}/srv/shop/.venv/.kajenn",
        "docker": "{tmp}/opt/kajenn",
        "kubernetes": "{tmp}/mnt/kajenn",
    }

    def test_each_context_is_one_value_of_the_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for spelling in self.contexts.values():
            root = spelling.format(tmp=tmp_path)
            monkeypatch.setenv("KAJENN_HOME", root)
            registry = SitesRegistry()
            assert registry.base_dir == Path(root)
            assert registry.sites_dir == Path(root) / "sites"
            assert registry.run_dir == Path(root) / "run"

    def test_the_explicit_argument_wins_over_the_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAJENN_HOME", str(tmp_path / "from-the-environment"))
        assert SitesRegistry(base_dir=tmp_path / "given").base_dir == tmp_path / "given"

    def test_without_the_variable_the_user_home_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KAJENN_HOME", raising=False)
        assert DefaultConfig().base_dir == Path.home() / ".kajenn"


class TestTheCardResolvesTheName:
    """Resolving a name means reading its card: no search, no precedence."""

    def test_a_relative_source_is_read_inside_the_home(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        home.recipe.write_text(HOME_RECIPE.format(home=str(home)), encoding="utf-8")
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        cli.registry.save("shop", {"source": "config.py", "home": str(home)})
        launcher = ServerLauncher(cli.parser().parse_args(["serve", "shop"]), cli.registry)
        assert launcher.resolved_source == str(home.recipe)
        assert launcher.build_server().site_home == home

    def test_the_card_home_reaches_the_server_as_a_kwarg(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        cli.registry.save("shop", {"source": "template=default", "home": str(home)})
        launcher = ServerLauncher(cli.parser().parse_args(["serve", "shop"]), cli.registry)
        assert launcher.constructor_kwargs["site_home"] == str(home)
        assert launcher.build_server().site_home == home

    def test_the_command_line_home_wins_over_the_card(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        cli.registry.save("shop", {"source": "template=default", "home": str(tmp_path / "stored")})
        options = cli.parser().parse_args(["serve", "shop", "--home", str(tmp_path / "given")])
        assert ServerLauncher(options, cli.registry).home == SiteHome(tmp_path / "given")

    def test_a_named_serve_writes_the_snapshot_inside_the_home(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        options = cli.parser().parse_args(
            ["serve", "template=default", "--name", "shop", "--home", str(home)]
        )
        launcher = ServerLauncher(options, cli.registry)
        assert launcher.save_session_path == str(home.sessions / "shop.pickle")

    def test_a_homeless_named_serve_keeps_the_installation_root(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        options = cli.parser().parse_args(["serve", "template=default", "--name", "shop"])
        launcher = ServerLauncher(options, cli.registry)
        assert launcher.save_session_path == str(tmp_path / "root" / "sessions" / "shop.pickle")

    def test_a_home_that_is_not_there_stops_the_boot(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        cli.registry.save(
            "shop", {"source": "template=default", "home": str(tmp_path / "mounted")}
        )
        launcher = ServerLauncher(cli.parser().parse_args(["serve", "shop"]), cli.registry)
        with pytest.raises(CliError, match="is not there"):
            launcher.build_server()
        assert not (tmp_path / "mounted").exists()

    def test_a_name_with_no_card_names_the_command_that_configures_it(
        self, tmp_path: Path
    ) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        with pytest.raises(CliError, match="run 'kajenn configure shop'"):
            ServerLauncher(cli.parser().parse_args(["serve", "shop"]), cli.registry)

    def test_a_configuration_that_names_its_site_gets_a_card(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        recipe = tmp_path / "config.py"
        recipe.write_text(HOME_RECIPE.format(home=str(home)), encoding="utf-8")
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        launcher = ServerLauncher(cli.parser().parse_args(["serve", str(recipe)]), cli.registry)
        launcher.adopt_site_identity(launcher.build_server())
        assert (launcher.name, launcher.home) == ("shop", home)
        assert launcher.entry["home"] == str(home)

    def test_a_configuration_that_names_none_runs_anonymous(self, tmp_path: Path) -> None:
        recipe = tmp_path / "config.py"
        recipe.write_text(ANONYMOUS_RECIPE, encoding="utf-8")
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        launcher = ServerLauncher(cli.parser().parse_args(["serve", str(recipe)]), cli.registry)
        launcher.adopt_site_identity(launcher.build_server())
        assert launcher.name is None
        assert cli.registry.names() == []

    def test_the_command_line_name_wins_over_the_configured_one(self, tmp_path: Path) -> None:
        home = SiteHome(tmp_path / "shop")
        home.prepare()
        recipe = tmp_path / "config.py"
        recipe.write_text(HOME_RECIPE.format(home=str(home)), encoding="utf-8")
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        options = cli.parser().parse_args(["serve", str(recipe), "--name", "staging"])
        launcher = ServerLauncher(options, cli.registry)
        launcher.adopt_site_identity(launcher.build_server())
        assert launcher.name == "staging"


class TestConfigureWritesTheCardAndTheHome:
    """The interactive command, and the same answers given as options."""

    def test_every_option_given_runs_without_a_question(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        home = tmp_path / "shop"
        assert configure(
            cli,
            [
                "configure", "shop",
                "--home", str(home),
                "--template", "default",
                "--applications", "kajenn.application:BaseApplication",
                "--host", "0.0.0.0",
                "--port", "8100",
            ],
        ) == 0
        card = json.loads(cli.registry.card_path("shop").read_text(encoding="utf-8"))
        assert card == {
            "source": "config.py",
            "home": str(home),
            "host": "0.0.0.0",
            "port": 8100,
            "reload": False,
            "debug": False,
        }
        assert [folder.is_dir() for folder in SiteHome(home).folders] == [True] * 7

    def test_the_written_recipe_serves_the_site_it_describes(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        home = SiteHome(tmp_path / "shop")
        configure(
            cli,
            [
                "configure", "shop",
                "--home", str(home),
                "--template", "default",
                "--applications", "kajenn.application:BaseApplication",
                "--host", "0.0.0.0",
                "--port", "8100",
            ],
        )
        launcher = ServerLauncher(cli.parser().parse_args(["serve", "shop"]), cli.registry)
        server = launcher.build_server()
        assert server.site_name == "shop"
        assert server.site_home == home
        assert (server.config_host, server.config_port) == ("0.0.0.0", 8100)
        assert "baseapplication" in server.applications
        server.storage.node("home:probe.txt").write_text("here")
        assert (home.path / "probe.txt").read_text(encoding="utf-8") == "here"

    def test_a_site_with_no_application_is_configured_too(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        configure(
            cli,
            ["configure", "shop", "--home", str(tmp_path / "shop"),
             "--template", "default", "--applications", "", "--host", "127.0.0.1", "--port", "8000"],
        )
        server = ServerLauncher(
            cli.parser().parse_args(["serve", "shop"]), cli.registry
        ).build_server()
        assert server.applications == {}

    def test_the_questions_propose_what_the_options_would_have_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        asked: list[str] = []

        def press_enter(question: str) -> str:
            asked.append(question)
            return ""

        assert configure(cli, ["configure", "shop"], ask=press_enter) == 0
        assert len(asked) == 5
        card = json.loads(cli.registry.card_path("shop").read_text(encoding="utf-8"))
        assert card["home"] == str(tmp_path.resolve() / "shop")
        assert (card["host"], card["port"]) == ("127.0.0.1", 8000)

    def test_an_answer_typed_in_replaces_the_proposal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        replies = iter([str(tmp_path / "elsewhere"), "", "", "", "9000"])

        assert configure(cli, ["configure", "shop"], ask=lambda question: next(replies)) == 0
        card = json.loads(cli.registry.card_path("shop").read_text(encoding="utf-8"))
        assert card["home"] == str(tmp_path / "elsewhere")
        assert card["port"] == 9000

    def test_the_site_home_cannot_be_the_installation_root(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        with pytest.raises(CliError, match="cannot be the kajenn home"):
            configure(cli, ["configure", "shop", "--home", str(tmp_path / "root"),
                            "--template", "default", "--applications", "",
                            "--host", "127.0.0.1", "--port", "8000"])

    def test_an_unknown_template_names_the_known_ones(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        with pytest.raises(CliError, match="known: default"):
            configure(cli, ["configure", "shop", "--home", str(tmp_path / "shop"),
                            "--template", "nope", "--applications", "",
                            "--host", "127.0.0.1", "--port", "8000"])

    def test_a_single_file_target_cannot_be_written_as_an_import(self, tmp_path: Path) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        with pytest.raises(CliError, match="importable targets only"):
            configure(cli, ["configure", "shop", "--home", str(tmp_path / "shop"),
                            "--template", "default", "--applications", "./hello.py:Hello",
                            "--host", "127.0.0.1", "--port", "8000"])

    def test_the_configured_site_is_listed(self, tmp_path: Path, capsys) -> None:
        cli = Cli(registry=SitesRegistry(base_dir=tmp_path / "root"))
        configure(cli, ["configure", "shop", "--home", str(tmp_path / "shop"),
                        "--template", "default", "--applications", "",
                        "--host", "127.0.0.1", "--port", "8000"])
        assert cli.run(["sites"]) == 0
        out = capsys.readouterr().out
        assert "shop" in out
        assert str(tmp_path / "shop") in out
