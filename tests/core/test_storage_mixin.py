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

"""StorageMixin + ``storage`` config section tests.

Two layers: the capability mixin (``storage=``/``storage_key=`` peeled by
``AsgiServer``, the plain ``BaseServer`` never gaining a ``storage`` attribute)
and the config path (a recipe's ``storage`` section reaching the server's
``StorageManager`` through ``AsgiServer(config=...)``, an explicit kwarg still
winning over the configured one).

``storage=`` takes exactly three shapes, one test class each: ``None`` (the
single ``site:`` mount on the deployment directory), a ``StorageManager``
(adopted as-is) and a ``list[dict]`` (genro-storage's own mount configuration,
passed through to ``configure()``). Encryption is declared per WRITE, so the
key test asserts both halves at once: a credential write lands as an envelope
on disk while a session file in the same tree stays plain, and both read back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from genro_storage import StorageManager
from genro_storage.exceptions import (
    StorageConfigError,
    StorageError,
    StorageNotFoundError,
)

from tests.storage_support import site_mounts, site_storage

from kajenn import (
    AsgiConfigBuilder,
    AsgiServer,
    BaseApplication,
    BaseServer,
    ConfigurationHandler,
    StorageMixin,
)
from kajenn.middleware.base import BaseMiddleware
from kajenn.types import Receive, Scope, Send


class ShopApp(BaseApplication):
    """Minimal primary app for the config-driven tests."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"shop"})


@pytest.fixture
def key() -> str:
    """A single fresh Fernet key."""
    return Fernet.generate_key().decode()


def chain_types(server: AsgiServer) -> list[str]:
    """The class names of the middlewares in the server's chain, outermost first."""
    names: list[str] = []
    node: object = server.middleware_chain
    while isinstance(node, BaseMiddleware):
        names.append(type(node).__name__)
        node = node.app
    return names


class TestMixinComposition:
    """The two shapes ``storage=`` accepts, plus the composition without the mixin."""

    def test_default_storage_is_the_two_mounts_on_the_deployment_directory(self) -> None:
        server = AsgiServer(applications=[(BaseApplication, {"mount": ""})])
        assert isinstance(server.storage, StorageManager)
        assert server.storage.get_mount_names() == ["site", "home"]
        assert server.storage.node("site:pyproject.toml").exists()   # anchored to the cwd
        assert server.storage.node("home:pyproject.toml").exists()   # no home: the same folder

    def test_a_built_manager_is_not_a_configuration_value(self, tmp_path: Path) -> None:
        """#91: the server builds its storage from the declared mounts, never adopts one."""
        with pytest.raises((TypeError, AttributeError)):
            AsgiServer(
                applications=[(BaseApplication, {"mount": ""})],
                storage=site_storage(tmp_path),
            )

    def test_mount_list_is_passed_through_to_configure(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()      # genro-storage mounts an existing directory
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            storage=[{"name": "data", "protocol": "local", "base_path": str(tmp_path / "data")}],
        )
        node = server.storage.node("data:hello.txt")
        node.write_text("hi")
        assert node.read_text() == "hi"

    def test_base_server_has_no_storage_attribute(self) -> None:
        assert not hasattr(BaseServer(applications=[BaseApplication(mount="")]), "storage")


class TestStorageKey:
    def test_encrypted_and_plain_writes_share_one_tree(self, tmp_path: Path, key: str) -> None:
        """A credential is an envelope on disk, a session next to it stays plain."""
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            storage=site_mounts(tmp_path),
            storage_key=key,
        )
        assert server.storage.encryption_active
        credential = server.storage.node("site:users/admin.json")
        credential.write_text('{"k": 1}', encrypted=True)
        session = server.storage.node("site:sessions/abc.json")
        session.write_text('{"s": 2}')

        assert (tmp_path / "users" / "admin.json").read_bytes().startswith(b"#GNRE1:")
        assert (tmp_path / "sessions" / "abc.json").read_bytes() == b'{"s": 2}'
        assert credential.read_text() == '{"k": 1}'
        assert session.read_text() == '{"s": 2}'

    def test_encrypted_write_without_key_material_raises(self, tmp_path: Path) -> None:
        """Dormancy is loud: ``encrypted=True`` with no key installed fails at the write."""
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            storage=site_mounts(tmp_path),
        )
        with pytest.raises(StorageError):
            server.storage.node("site:users/admin.json").write_text("{}", encrypted=True)

    def test_empty_storage_key_raises_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(StorageConfigError):
            AsgiServer(
                applications=[(BaseApplication, {"mount": ""})],
                storage=site_mounts(tmp_path),
                storage_key="",
            )

    def test_omitted_storage_key_leaves_encryption_dormant(self, tmp_path: Path) -> None:
        server = AsgiServer(
            applications=[(BaseApplication, {"mount": ""})],
            storage=site_mounts(tmp_path),
        )
        assert not server.storage.encryption_active


class TestBareMixin:
    def test_mixin_over_base_server_exposes_storage(self) -> None:
        class Srv(StorageMixin, BaseServer):
            pass

        server = Srv(applications=[BaseApplication(mount="")])
        assert isinstance(server.storage, StorageManager)


class TestConfigDriven:
    def test_recipe_storage_section_serves_declared_mounts(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        class StorageConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.storage(app=StorageManager).local(name="data", base_path=str(data_dir))
                cfg.applications(default="shop").application(code="shop", app_class=ShopApp)

        server = AsgiServer(config=StorageConfig)
        node = server.storage.node("data:file.txt")
        node.write_text("x")
        assert node.read_text() == "x"

    def test_recipe_storage_key_installs_encryption(self, tmp_path: Path, key: str) -> None:
        secure_root = tmp_path

        class StorageConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.storage(app=StorageManager, storage_key=key).local(
                    name="site", base_path=str(secure_root)
                )
                cfg.applications(default="shop").application(code="shop", app_class=ShopApp)

        server = AsgiServer(config=StorageConfig)
        assert server.storage.encryption_active

    def test_explicit_kwarg_wins_over_the_configured_one(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        override_dir = tmp_path / "override"
        override_dir.mkdir()

        class StorageConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.storage(app=StorageManager).local(name="data", base_path=str(data_dir))
                cfg.applications(default="shop").application(code="shop", app_class=ShopApp)

        server = AsgiServer(
            config=StorageConfig,
            storage=[{"name": "only", "protocol": "local", "base_path": str(override_dir)}],
        )
        assert server.storage.node("only:w.txt") is not None
        with pytest.raises(StorageNotFoundError, match="data"):
            server.storage.node("data:w.txt")


class TestDefaultLayoutFallback:
    """An empty mount list means "the default layout", not "no storage"."""

    def test_an_empty_mount_list_falls_back_to_the_default_layout(self) -> None:
        server = AsgiServer(applications=[(BaseApplication, {"mount": ""})], storage=[])
        assert server.storage.get_mount_names() == ["site", "home"]

    def test_a_key_only_recipe_section_still_serves_the_default_layout(self, key: str) -> None:
        """The recipe declares the key and no mount; the default layout applies."""

        class KeyOnlyConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.storage(app=StorageManager, storage_key=key)
                cfg.applications(default="shop").application(code="shop", app_class=ShopApp)

        server = AsgiServer(config=ConfigurationHandler(KeyOnlyConfig))
        assert server.storage.get_mount_names() == ["site", "home"]
        assert server.storage.encryption_active
