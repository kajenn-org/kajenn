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

"""The read door reaching the environment and the applications.

Two properties of the resolver model, one file. **Resolvers resolve in place**:
a recipe stores an ``EnvResolver`` where a value would go and the read stack
resolves it at READ time, so the value the runtime consumes is the environment's
— the layer the old ``^pointer`` model silently dropped for ``storage_key``,
which is why the encryption round-trip below is a regression test and not a
nicety. **Applications read their own subtree**: ``app.config(path)`` prefixes
``applications.<code>.`` and delegates to the server's handler, so an app holds
an address in the tree, never a slice of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from genro_storage import StorageManager
from genro_storage.exceptions import StorageConfigError
from cryptography.fernet import Fernet
from genro_bag.resolvers import EnvResolver
from genro_builders.builder import element

from kajenn import (
    AsgiConfigBuilder,
    AsgiServer,
    BaseApplication,
    ConfigurationHandler,
)
from kajenn.application import ApplicationGrammar
from kajenn.types import Receive, Scope, Send

HOST_ENV_VAR = "GENRO_TEST_HOST"
PORT_ENV_VAR = "GENRO_TEST_PORT"
STORAGE_KEY_ENV_VAR = "GENRO_TEST_STORAGE_KEY"
BASIC_PW_ENV_VAR = "GENRO_TEST_BASIC_PW"
DB_PW_ENV_VAR = "GENRO_TEST_DB_PW"


class ShopGrammar(ApplicationGrammar):
    """A richer app grammar: the ``catalog`` block on top of ``parameters``."""

    @element(node_label="catalog")
    def catalog(self, title: str | None = None, page_size: int = 20) -> None:
        """The catalog block, read back as ``applications.<code>.catalog.<attr>``."""


class ShopApp(BaseApplication):
    """App with its own grammar, answering ``shop``."""

    grammar = ShopGrammar

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"shop"})


class PlainApp(BaseApplication):
    """App with the inherited minimal grammar."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"plain"})


class AppReadsConfig(AsgiConfigBuilder):
    """One app whose mounted subtree carries both a ``parameters`` and a ``catalog``."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8000)
        self.applications_section(cfg)

    def applications_section(self, cfg: Any) -> None:
        """``shop`` claims the root and declares its own vocabulary."""
        apps = cfg.applications(default="shop")
        app = apps.application(code="shop", mount="", app_class=ShopApp)
        app.parameters(currency="EUR")
        app.catalog(title="Outlet")


@pytest.fixture
def key() -> str:
    """A single fresh Fernet key."""
    return Fernet.generate_key().decode()


class TestServerAttributesFromTheEnvironment:
    """``host``/``port`` supplied by resolvers sitting in the attributes."""

    def test_host_and_typed_port_resolve_at_read_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOST_ENV_VAR, "10.0.0.7")
        monkeypatch.setenv(PORT_ENV_VAR, "9443")

        class EnvServerConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(
                    host=EnvResolver(HOST_ENV_VAR),
                    port=EnvResolver(PORT_ENV_VAR, dtype="L"),
                )
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=EnvServerConfig)
        assert server.config_host == "10.0.0.7"
        assert server.config_port == 9443           # dtype="L" → a real int

    def test_the_environment_is_read_not_frozen_into_the_recipe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOST_ENV_VAR, "first.example.com")

        class EnvHostConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host=EnvResolver(HOST_ENV_VAR), port=8000)
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        handler = ConfigurationHandler(EnvHostConfig)
        assert handler("server.host") == "first.example.com"
        monkeypatch.setenv(HOST_ENV_VAR, "second.example.com")
        assert handler("server.host") == "second.example.com"


class TestStorageKeyFromTheEnvironment:
    """The regression the pointer model caused: encryption silently disarmed."""

    def test_resolved_storage_key_arms_encryption_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
    ) -> None:
        monkeypatch.setenv(STORAGE_KEY_ENV_VAR, key)
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()

        class VaultConfig(AsgiConfigBuilder):
            def setup(self, data: Any) -> None:
                """The mount path travels through the datastore, not a closure."""
                data["vault_path"] = str(vault_dir)

            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.storage(
                    app=StorageManager,
                    storage_key=EnvResolver(STORAGE_KEY_ENV_VAR),
                ).local(
                    name="vault", base_path=self.data["vault_path"], default_encrypted=True
                )
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=VaultConfig)
        assert server.storage.encryption_active
        node = server.storage.node("vault:secret.txt")
        node.write_text("top-secret")
        assert (vault_dir / "secret.txt").read_bytes() != b"top-secret"
        assert node.read_text() == "top-secret"

    def test_a_storage_key_resolving_empty_is_a_boot_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(STORAGE_KEY_ENV_VAR, raising=False)

        class EmptyKeyConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.storage(
                    app=StorageManager,
                    storage_key=EnvResolver(STORAGE_KEY_ENV_VAR, default=""),
                ).memory(name="scratch")
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        # An empty string is NOT "missing": the recipe promised an encryption
        # key, so a resolution that yields nothing is a boot error, never a
        # silent downgrade to plaintext.
        with pytest.raises(StorageConfigError, match="empty key material"):
            AsgiServer(config=EmptyKeyConfig)


class TestSecretsFromTheEnvironment:
    """A secret written as an attribute resolves through the read stack."""

    def test_basic_user_password_attribute_resolves_into_the_auth_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(BASIC_PW_ENV_VAR, "attr-s3cret")

        class BasicConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                creds = cfg.authentication().credentials()
                creds.basic_user(
                    username="admin",
                    password=EnvResolver(BASIC_PW_ENV_VAR),
                    tags="admin",
                )
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        entries = ConfigurationHandler(BasicConfig).auth_entries()
        assert entries is not None
        assert entries["basic"]["admin"]["password"] == "attr-s3cret"

class RecordingDb:
    """A ``db_class`` stand-in: the fold hands it the connection params."""

    def __init__(self, **params: Any) -> None:
        self.params = params


class TestOpenAttributesFromTheEnvironment:
    """A resolver in an OPEN element's ``**kwargs`` resolves through the bulk read."""

    def test_database_password_resolves_in_the_open_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DB_PW_ENV_VAR, "db-s3cret")

        class DbConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.databases().database(
                    code="main",
                    db_class=RecordingDb,
                    dsn="postgres://localhost/main",
                    password=EnvResolver(DB_PW_ENV_VAR),
                )
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        [descriptor] = ConfigurationHandler(DbConfig).databases()
        params = descriptor["params"]
        assert params["password"] == "db-s3cret"
        assert params["dsn"] == "postgres://localhost/main"


class TestApplicationSideReads:
    """``app.config(path)`` addresses ``applications.<code>.<path>``."""

    def test_written_value_in_the_mounted_subtree(self) -> None:
        server = AsgiServer(config=AppReadsConfig)
        shop = server.applications["shop"]
        assert shop.config("parameters.currency") == "EUR"
        assert shop.config("catalog.title") == "Outlet"

    def test_signature_default_of_the_mounted_grammar(self) -> None:
        server = AsgiServer(config=AppReadsConfig)
        # Never written by the recipe: the read walks up to ShopGrammar.catalog.
        assert server.applications["shop"].config("catalog.page_size") == 20

    def test_call_site_default_answers_an_unwritten_attribute(self) -> None:
        server = AsgiServer(config=AppReadsConfig)
        assert server.applications["shop"].config("parameters.locale", default="it") == "it"

    def test_a_missing_path_raises_the_noisy_key_error(self) -> None:
        server = AsgiServer(config=AppReadsConfig)
        with pytest.raises(KeyError, match="applications.shop.parameters.locale"):
            server.applications["shop"].config("parameters.locale")

    def test_each_app_reads_only_its_own_prefix(self) -> None:
        class TwoAppsConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                apps = cfg.applications(default="shop")
                apps.application(code="shop", mount="", app_class=ShopApp).parameters(
                    currency="EUR"
                )
                apps.application(code="plain", app_class=PlainApp).parameters(
                    currency="USD"
                )

        server = AsgiServer(config=TwoAppsConfig)
        assert server.applications["shop"].config("parameters.currency") == "EUR"
        assert server.applications["plain"].config("parameters.currency") == "USD"


class TestUnconfiguredServer:
    """An app on a server composed in code reads a tree that declares nothing."""

    def test_call_site_default_answers(self) -> None:
        app = AsgiServer(applications=[(ShopApp, {"mount": ""})]).applications["shopapp"]
        assert app.config("catalog.title", default="none") == "none"

    def test_without_a_default_it_raises(self) -> None:
        app = AsgiServer(applications=[(ShopApp, {"mount": ""})]).applications["shopapp"]
        with pytest.raises(KeyError, match="applications.shopapp.catalog.title"):
            app.config("catalog.title")

    def test_a_detached_app_raises_too(self) -> None:
        assert ShopApp().config("catalog.title", default="none") == "none"
