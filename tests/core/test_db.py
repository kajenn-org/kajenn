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

"""DB handler tests (core 1b Phase 6): ``AsgiDbHandlerBase`` proxy, the server
registry, and the ``databases`` section of a configured server.
"""

from __future__ import annotations

from typing import Any

import pytest

from kajenn import (
    AsgiConfigBuilder,
    AsgiDbHandlerBase,
    AsgiServer,
    BaseApplication,
    BaseServer,
)


class FakeDb:
    """Minimal db: holds params, exposes execute and closeConnection."""

    def __init__(self, **params: Any) -> None:
        self.params = params
        self.closed = False

    def execute(self, sql: str) -> str:
        return f"ran: {sql}"

    def closeConnection(self) -> None:
        self.closed = True


class TestAsgiDbHandlerBase:
    def test_proxies_attribute(self) -> None:
        """A non-underscore attribute is fetched from the wrapped db."""
        db = FakeDb(dbname="shop")
        handler = AsgiDbHandlerBase(db)
        assert handler.params is db.params

    def test_proxies_method(self) -> None:
        """A method call is forwarded to the wrapped db."""
        handler = AsgiDbHandlerBase(FakeDb())
        assert handler.execute("SELECT 1") == "ran: SELECT 1"

    def test_close_connection_delegates(self) -> None:
        """closeConnection delegates to the wrapped db when present."""
        db = FakeDb()
        AsgiDbHandlerBase(db).closeConnection()
        assert db.closed is True

    def test_close_connection_noop_when_absent(self) -> None:
        """closeConnection is a no-op when the db has none."""

        class Bare:
            pass

        AsgiDbHandlerBase(Bare()).closeConnection()  # must not raise

    def test_underscore_attributes_not_proxied(self) -> None:
        """Underscore names raise AttributeError instead of proxying (no recursion)."""
        handler = AsgiDbHandlerBase(FakeDb())
        with pytest.raises(AttributeError):
            handler._missing

    def test_missing_attribute_raises(self) -> None:
        """A public attribute the db lacks raises AttributeError."""
        handler = AsgiDbHandlerBase(FakeDb())
        with pytest.raises(AttributeError):
            handler.nonexistent

    def test_repr_shows_wrapped_type(self) -> None:
        """repr names the handler and the wrapped db type."""
        assert repr(AsgiDbHandlerBase(FakeDb())) == "AsgiDbHandlerBase(FakeDb)"

    def test_subclass_inherits_proxy(self) -> None:
        """A custom handler subclass keeps the proxy behaviour."""

        class CustomHandler(AsgiDbHandlerBase):
            pass

        handler = CustomHandler(FakeDb(dbname="x"))
        assert handler.params == {"dbname": "x"}
        assert isinstance(handler, AsgiDbHandlerBase)


class TestDatabaseRegistry:
    def test_add_database_registers_by_code(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        handler = AsgiDbHandlerBase(FakeDb())
        server.add_database("shop", handler)
        assert server.databases == {"shop": handler}

    def test_add_database_duplicate_code_raises(self) -> None:
        server = BaseServer(applications=[BaseApplication(mount="")])
        server.add_database("shop", AsgiDbHandlerBase(FakeDb()))
        with pytest.raises(ValueError):
            server.add_database("shop", AsgiDbHandlerBase(FakeDb()))

    def test_databases_empty_by_default(self) -> None:
        assert BaseServer(applications=[BaseApplication(mount="")]).databases == {}


# --- the databases section of a configured server ---


class ShopApp(BaseApplication):
    pass


class TwoDatabaseConfig(AsgiConfigBuilder):
    """A recipe with two ``database`` entries over ``FakeDb``."""

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8000)
        cfg.applications(default="shop").application(code="shop", app_class=ShopApp)
        self.databases_section(cfg)

    def databases_section(self, cfg: Any) -> None:
        """Two handlers: the default one and an explicit ``db_handler_class``."""
        dbs = cfg.databases()
        dbs.database(code="shop", db_class=FakeDb, dbname="shop")
        dbs.database(
            code="reports", db_class=FakeDb, db_handler_class=AsgiDbHandlerBase, dbname="ro"
        )


class TestConfiguredDatabases:
    def test_configured_server_registers_both_handlers(self) -> None:
        server = AsgiServer(config=TwoDatabaseConfig)
        assert isinstance(server, AsgiServer)
        assert set(server.databases) == {"shop", "reports"}
        shop = server.databases["shop"]
        assert isinstance(shop, AsgiDbHandlerBase)
        assert shop.params == {"dbname": "shop"}
        assert shop.execute("SELECT 1") == "ran: SELECT 1"
        reports = server.databases["reports"]
        assert reports.params == {"dbname": "ro"}

    def test_database_missing_db_class_raises(self) -> None:
        class MissingDbClassConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.applications(default="shop").application(code="shop", app_class=ShopApp)
                cfg.databases().database(code="shop")

        with pytest.raises(ValueError):
            AsgiServer(config=MissingDbClassConfig)
