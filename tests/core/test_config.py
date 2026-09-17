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

"""Config tests: the ``asgiconfig`` dialect, the read door, the self-configuring server.

A recipe subclasses ``AsgiConfigBuilder`` and declares the site sections under
one ``configuration`` root; ``AsgiServer(config=source)`` builds its own
``ConfigurationHandler`` over that source, derives its kwargs from it and stays
reachable as ``server.config``. Requests are driven at the ASGI level (no
uvicorn), the same style as ``test_session.py``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
import pytest
from genro_bag.resolvers import EnvResolver
from genro_storage import StorageManager

from kajenn import (
    AsgiConfigBuilder,
    AsgiServer,
    BaseApplication,
    ConfigError,
    ConfigurationHandler,
)
from kajenn.__main__ import SitesRegistry
from kajenn.config import HOME_ENV, BaseConfiguration, DefaultConfig
from kajenn.exceptions import HTTPUnauthorized
from kajenn.middleware.base import BaseMiddleware
from kajenn.storage_mixin import DEFAULT_HOME_MOUNT, DEFAULT_SITE_MOUNT
from kajenn.types import Message, Receive, Scope, Send



class ShopApp(BaseApplication):
    """Root app: answers ``shop``."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"shop"})


class ApiApp(BaseApplication):
    """Secondary app: answers ``api``."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"api"})


class TwoAppConfig(AsgiConfigBuilder):
    """Two apps (shop on the root, api secondary), cors + basic auth, host/port.

    Declares ``external_url`` too — the whole-site recipe of these tests, and a
    site that configures an OIDC provider must name its public base address (the
    absolute ``redirect_uri`` prefix) or the server refuses to boot. The
    without-``external_url`` case is covered on its own in ``test_oidc.py``.
    """

    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="0.0.0.0", port=9100, external_url="https://shop.example.com")
        cfg.middleware(cors=True)
        self.authentication_section(cfg)
        self.applications_section(cfg)

    def authentication_section(self, cfg: Any) -> None:
        """One Basic user, handed to ``AuthCore`` through ``credentials``."""
        creds = cfg.authentication().credentials()
        creds.basic_user(username="admin", password="secret", tags="admin")

    def applications_section(self, cfg: Any) -> None:
        """``shop`` claims the site root, ``api`` answers its own mount."""
        apps = cfg.applications(default="shop")
        apps.application(code="shop", mount="", app_class=ShopApp)
        apps.application(code="api", app_class=ApiApp)


def chain_types(server: AsgiServer) -> list[str]:
    """The class names of the middlewares in the server's chain, outermost first."""
    names: list[str] = []
    node: object = server.middleware_chain
    while isinstance(node, BaseMiddleware):
        names.append(type(node).__name__)
        node = node.app
    return names


def basic_header(username: str, password: str) -> list[tuple[bytes, bytes]]:
    """An ``Authorization: Basic`` header list for the given credentials."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return [(b"authorization", f"Basic {token}".encode())]


async def http_get(server: AsgiServer, path: str) -> bytes:
    """Drive one GET through ``server`` at the ASGI level; return the response body."""
    scope: Scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


async def http_status_headers(
    server: AsgiServer, path: str
) -> tuple[int, list[tuple[bytes, bytes]]]:
    """Drive one GET through ``server``; return its status and response headers."""
    scope: Scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await server(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], start["headers"]


class TestSelfConfiguringServer:
    def test_server_section_reaches_the_serve_defaults(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        assert server.config_host == "0.0.0.0"
        assert server.config_port == 9100
        assert server.external_url == "https://shop.example.com"

    def test_default_app_answers_the_root_others_are_mounts(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        assert isinstance(server.root_application, ShopApp)
        assert server.root_application.mount == ""
        assert set(server.applications) == {"shop", "api"}
        assert isinstance(server.applications["api"], ApiApp)

    def test_a_server_composed_in_code_has_one_too(self) -> None:
        """#91: the configuration always exists — the kwargs build it."""
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})])
        assert isinstance(server.config, ConfigurationHandler)
        assert server.config("applications.shopapp.mount") == ""

    def test_the_handler_stays_reachable_as_the_read_door(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        assert isinstance(server.config, ConfigurationHandler)
        assert server.config("server.host") == "0.0.0.0"
        assert server.config("server.port") == 9100

    def test_an_explicit_kwarg_wins_over_the_configured_one(self) -> None:
        server = AsgiServer(config=TwoAppConfig, port=0)
        assert server.config_port == 0
        assert server.config_host == "0.0.0.0"       # untouched kwargs still apply

    def test_a_recipe_instance_is_accepted(self) -> None:
        assert AsgiServer(config=TwoAppConfig(name="site")).config_port == 9100

    def test_a_ready_handler_is_adopted_as_is(self) -> None:
        handler = ConfigurationHandler(TwoAppConfig)
        server = AsgiServer(config=handler)
        assert server.config is handler

    def test_a_config_py_path_is_loaded(self, tmp_path: Path) -> None:
        module = tmp_path / "config.py"
        module.write_text(
            "from kajenn.config import AsgiConfigBuilder\n"
            "\n"
            "\n"
            "class ServerConfiguration(AsgiConfigBuilder):\n"
            "    def main(self, root):\n"
            "        cfg = root.configuration()\n"
            "        cfg.server(host='127.0.0.1', port=8123)\n"
        )
        server = AsgiServer(config=module)
        assert server.config_port == 8123
        assert server.applications == {}


class TestDemux:
    async def test_serves_both_apps(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        assert await http_get(server, "/") == b"shop"
        assert await http_get(server, "/api") == b"api"


class TestMiddlewareChain:
    def test_chain_contains_cors_and_errors(self) -> None:
        types = chain_types(AsgiServer(config=TwoAppConfig))
        assert "CORSMiddleware" in types
        assert "ErrorMiddleware" in types

    def test_an_explicit_switch_off_survives_the_read(self) -> None:
        class NoCorsConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.middleware(cors=False)
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        assert "CORSMiddleware" not in chain_types(AsgiServer(config=NoCorsConfig))


class TestCredentials:
    def test_basic_user_is_verified_by_the_auth_core(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        scope: Scope = {"headers": basic_header("admin", "secret")}
        avatar = server.authenticate(scope)
        assert avatar is not None
        assert avatar.identity == "admin"
        assert "admin" in avatar.tags

    def test_wrong_password_raises_unauthorized(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        scope: Scope = {"headers": basic_header("admin", "wrong")}
        with pytest.raises(HTTPUnauthorized):
            server.authenticate(scope)

    def test_bearer_token_is_verified_by_its_identity(self) -> None:
        class BearerConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                creds = cfg.authentication().credentials()
                creds.bearer_token(identity="svc", token="sk_live_xyz", tags="api")
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=BearerConfig)
        scope: Scope = {"headers": [(b"authorization", b"Bearer sk_live_xyz")]}
        avatar = server.authenticate(scope)
        assert avatar is not None
        assert avatar.identity == "svc"
        assert avatar.tags == ["api"]

    def test_jwt_entries_stay_an_ordered_list(self) -> None:
        class JwtConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                creds = cfg.authentication().credentials()
                creds.jwt(name="hmac", secret="topsecret")
                creds.jwt(name="rsa", public_key="PUBKEY", algorithm="RS256")
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        entries = ConfigurationHandler(JwtConfig).auth_entries()
        assert [entry["name"] for entry in entries["jwt"]] == ["hmac", "rsa"]
        assert entries["jwt"][0]["algorithm"] == "HS256"        # signature default
        assert entries["jwt"][1]["public_key"] == "PUBKEY"

    def test_no_credentials_section_arms_no_backend(self) -> None:
        class BareConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().applications().application(
                    code="shop", mount="", app_class=ShopApp
                )

        assert ConfigurationHandler(BareConfig).auth_entries() is None


class TestSession:
    async def test_session_attached_after_a_request(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request"}

        async def send(message: Message) -> None:
            sent.append(message)

        await server(scope, receive, send)
        assert scope.get("session") is not None
        assert server.session(scope) is scope["session"]

    def test_session_child_reaches_the_store_ttl(self) -> None:
        class SessionConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000).session(ttl=1234)
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=SessionConfig)
        assert server.session_store.create().meta["ttl"] == 1234


class TestShutdownTimeout:
    async def test_recipe_shutdown_timeout_reaches_uvicorn(self, monkeypatch) -> None:
        # One endless response (an SSE stream a client never closes) used to hold
        # uvicorn's shutdown for ever, so the lifespan shutdown never ran and the
        # applications were never stopped (measured 2026-09-08). The bound is a
        # server setpoint and travels to uvicorn as timeout_graceful_shutdown.
        class BoundedConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000, shutdown_timeout_seconds=2)
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        captured: list[Any] = []

        class XT_Server:
            def __init__(self, base_server: Any, config: Any) -> None:
                captured.append(config)

            def run(self) -> None:
                pass

        import kajenn.server as server_module

        monkeypatch.setattr(server_module, "UvicornServer", XT_Server)
        server = AsgiServer(config=BoundedConfig)
        assert server.shutdown_timeout_seconds == 2.0
        server.serve()
        assert captured[0].timeout_graceful_shutdown == 2.0

    def test_the_default_is_five_seconds(self) -> None:
        assert AsgiServer(config=TwoAppConfig).shutdown_timeout_seconds == 5.0


class TestMaxThreads:
    async def test_recipe_max_threads_reaches_the_pool(self) -> None:
        class SizedPoolConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000, max_threads=2)
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=SizedPoolConfig)
        await server.run_sync(lambda: None)
        assert server.pool.executor._max_workers == 2


class TestGrammarValidation:
    def test_unknown_tag_raises(self) -> None:
        class BadConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().nonexistent(foo=1)

        with pytest.raises(AttributeError):
            ConfigurationHandler(BadConfig)

    def test_a_section_outside_the_root_is_rejected(self) -> None:
        class LooseConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.server(host="127.0.0.1")

        with pytest.raises(ValueError, match="parent_tags"):
            ConfigurationHandler(LooseConfig)

    def test_application_without_app_class_rejected_by_grammar(self) -> None:
        class NoClassConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().applications().application(code="shop")

        with pytest.raises(ValueError, match="app_class"):
            ConfigurationHandler(NoClassConfig)

    def test_a_mount_without_base_path_is_rejected_by_the_foreign_grammar(self) -> None:
        # The storage subtree is validated by genro-storage's own signatures,
        # not by this dialect: the error comes from THERE.
        class NoBasePathConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().storage(app=StorageManager).local(name="data")

        with pytest.raises(ValueError, match="base_path"):
            ConfigurationHandler(NoBasePathConfig)

    def test_storage_without_app_is_rejected_by_the_grammar(self) -> None:
        # ``app`` cannot be defaulted in the signature: the subbuilder
        # reference reads the CALL SITE, so an omitted ``app`` would silently
        # leave the node a leaf of this dialect. It is required instead.
        class NoAppConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().storage()

        with pytest.raises(ValueError, match="app"):
            ConfigurationHandler(NoAppConfig)

    def test_an_empty_application_code_is_a_boot_error(self) -> None:
        # code="" would file the subtree under an empty label while the app
        # registers under its class-name fallback: the read door would then
        # never reach the written values. The grammar refuses the code before
        # the node is written (Annotated[str, Regex(CODE_PATTERN)]).
        class EmptyCodeConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.applications().application(code="", mount="", app_class=ShopApp)

        with pytest.raises(ValueError, match="'code'"):
            AsgiServer(config=EmptyCodeConfig)

    def test_a_second_server_section_is_rejected(self) -> None:
        class TwiceConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1")
                cfg.server(host="0.0.0.0")

        with pytest.raises(ValueError):
            ConfigurationHandler(TwiceConfig)


class TestSkippedSections:
    def test_openapi_and_databases_boot_without_error(self) -> None:
        class OrchestrationConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                cfg.applications(default="shop").application(
                    code="shop", mount="", app_class=ShopApp
                )
                cfg.databases().database(code="default", db_class=object)
                cfg.openapi(title="Demo", version="1.0")

        server = AsgiServer(config=OrchestrationConfig)
        assert isinstance(server.root_application, ShopApp)
        assert set(server.applications) == {"shop"}
        assert server.config("openapi.title") == "Demo"


class TestSingleAppNoDefault:
    def test_lone_app_answers_its_own_mount_not_the_root(self) -> None:
        # Nothing elects an application: a lone app derives its mount from its
        # code like every other, so the site root stays unclaimed.
        class OneAppConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().applications().application(
                    code="only", app_class=ShopApp
                )

        server = AsgiServer(config=OneAppConfig)
        assert set(server.applications) == {"only"}
        assert server.root_application is None
        assert isinstance(server.application_at("only"), ShopApp)

    def test_lone_app_claims_the_root_by_declaring_an_empty_mount(self) -> None:
        # The compatibility mechanism: one app served at unchanged URLs.
        class RootAppConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().applications().application(
                    code="only", mount="", app_class=ShopApp
                )

        server = AsgiServer(config=RootAppConfig)
        assert isinstance(server.root_application, ShopApp)
        assert server.root_application.code == "only"


class TestDefaultRedirect:
    async def test_root_redirects_to_the_default_when_nobody_claims_it(self) -> None:
        class MountsOnlyConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications(default="shop")
                apps.application(code="shop", app_class=ShopApp)
                apps.application(code="api", app_class=ApiApp)

        server = AsgiServer(config=MountsOnlyConfig)
        assert server.root_application is None
        assert server.default_application is server.applications["shop"]
        status, headers = await http_status_headers(server, "/")
        assert status == 307
        assert dict(headers)[b"location"] == b"/shop/"

    def test_a_default_naming_no_application_is_a_boot_error(self) -> None:
        class GhostDefaultConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().applications(default="ghost").application(
                    code="shop", app_class=ShopApp
                )

        with pytest.raises(ValueError, match="ghost"):
            AsgiServer(config=GhostDefaultConfig)


def storage_site_config(base_path: Path) -> type[AsgiConfigBuilder]:
    """A site recipe with an ``idstore`` mount and the key the credential stores need."""

    class StorageSiteConfig(AsgiConfigBuilder):
        def setup(self, data: Any) -> None:
            """The mount path travels through the datastore, not a closure."""
            data["base_path"] = str(base_path)

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=8000)
            cfg.storage(
                app=StorageManager, storage_key=Fernet.generate_key().decode()
            ).local(name="idstore", base_path=self.data["base_path"])
            cfg.applications(default="shop").application(
                code="shop", mount="", app_class=ShopApp
            )
            self.identity_section(cfg)

        def identity_section(self, cfg: Any) -> None:
            """Both identity stores on ``idstore``."""
            auth = cfg.authentication()
            auth.users(mount="idstore", prefix="users")
            auth.tokens(mount="idstore", prefix="api_keys")

    return StorageSiteConfig


class TestStorageSection:
    """``storage`` → genro-storage's own ``list[dict]`` plus the section key."""

    def test_the_section_flattens_to_genro_storage_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENRO_TEST_STORAGE_KEY", "k1,k2")

        class StorageConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                self.storage_section(root.configuration())

            def storage_section(self, cfg: Any) -> None:
                s = cfg.storage(
                    app=StorageManager,
                    storage_key=EnvResolver("GENRO_TEST_STORAGE_KEY"),
                )
                s.local(name="site", base_path=".")
                s.s3(name="uploads", bucket="shop-media", default_encrypted="shopspa")

        mounts, storage_key = ConfigurationHandler(StorageConfig).storage_config()
        assert storage_key == "k1,k2"
        assert mounts == [
            {"name": "site", "protocol": "local", "base_path": "."},
            {
                "name": "uploads",
                "protocol": "s3",
                "bucket": "shop-media",
                "default_encrypted": "shopspa",
            },
        ]

    def test_no_storage_section_leaves_the_default_manager(self) -> None:
        assert ConfigurationHandler(TwoAppConfig).storage_config() is None


class TestIdentitySection:
    """``authentication`` → the identity kwargs ``AuthMixin`` peels."""

    def test_no_identity_configured_leaves_the_stores_unwired(self) -> None:
        server = AsgiServer(config=TwoAppConfig)
        assert server.user_store is None
        assert server.api_key_store is None

    def test_the_declared_stores_reach_the_server(self, tmp_path: Path) -> None:
        server = AsgiServer(config=storage_site_config(tmp_path))
        assert server.user_store is not None
        assert server.api_key_store is not None
        assert server.user_store.load_all() == []   # the server seeds nobody

    def test_the_bootstrap_password_is_no_longer_a_word(self) -> None:
        """#91: the server creates no user, so the recipe has nothing to say."""

        class BootstrapConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().authentication().admin_password("anything")

        with pytest.raises(AttributeError, match="admin_password"):
            ConfigurationHandler(BootstrapConfig)

    def test_a_second_users_element_is_rejected_by_the_grammar(self) -> None:
        class DoubledConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                auth = root.configuration().authentication()
                auth.users(mount="one")
                auth.users(mount="two")

        with pytest.raises(ValueError):
            ConfigurationHandler(DoubledConfig)


class TestTasksConfig:
    """The ``tasks()`` child of ``server`` lifts to the ``tasks=`` kwarg."""

    def test_tasks_disabled_via_recipe(self) -> None:
        class TasksOffConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000).tasks(enabled=False)
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=TasksOffConfig)
        assert server.tasks_enabled is False
        with pytest.raises(RuntimeError, match="disabled"):
            server.tasks

    def test_tuning_reaches_scheduler_and_store(self) -> None:
        class TunedConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000).tasks(tick_seconds=5, mount="site")
                cfg.applications().application(code="shop", mount="", app_class=ShopApp)

        server = AsgiServer(config=TunedConfig)
        assert server.tasks_enabled is True                  # enabled defaults on
        assert server.tasks.scheduler.tick_seconds == 5.0
        assert server.tasks.task_store.mount == "site"       # explicit override

    def test_direct_dict_kwarg(self) -> None:
        server = AsgiServer(applications=[(ShopApp, {"mount": ""})],
                            tasks={"enabled": True, "tick_seconds": 3})
        assert server.tasks.scheduler.tick_seconds == 3.0
        assert server.tasks_config == {"tick_seconds": 3}    # enabled peeled away

    def test_a_child_under_tasks_is_rejected_by_the_grammar(self) -> None:
        class StrayChildConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000).tasks().middleware()

        with pytest.raises(ValueError, match="parent"):
            ConfigurationHandler(StrayChildConfig)


class ParametrizedShop(ShopApp):
    """An app whose grammar is the inherited minimal one (``parameters``)."""

    code = "shop"


class TestMountedAppGrammar:
    """``application(app_class=...)`` mounts ``app_class.grammar`` for the subtree."""

    def test_the_apps_own_subtree_is_read_through_the_handler(self) -> None:
        class ParamConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                cfg = root.configuration()
                cfg.server(host="127.0.0.1", port=8000)
                shop = cfg.applications(default="shop").application(
                    code="shop", mount="", app_class=ParametrizedShop
                )
                shop.parameters(theme="dark", max_items=10)

        server = AsgiServer(config=ParamConfig)
        assert server.config("applications.shop.parameters.theme") == "dark"
        assert server.config("applications.shop.parameters.max_items") == 10

    def test_the_envelope_attributes_are_the_apps_constructor_kwargs(self) -> None:
        class KwargConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().applications().application(
                    code="outlet", mount="outlet", app_class=ShopApp
                )

        server = AsgiServer(config=KwargConfig)
        assert server.applications["outlet"].mount == "outlet"

    def test_an_undeclared_child_of_the_mounted_grammar_raises(self) -> None:
        class StrayConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                shop = root.configuration().applications().application(
                    code="shop", mount="", app_class=ShopApp
                )
                shop.catalog(title="x")

        with pytest.raises(AttributeError):
            ConfigurationHandler(StrayConfig)


class TestReadStack:
    """The four layers, on this dialect."""

    def test_written_value_wins(self) -> None:
        assert ConfigurationHandler(TwoAppConfig)("server.port") == 9100

    def test_signature_default_is_resolved_at_read_time(self) -> None:
        class ProviderConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().authentication().credentials().jwt(
                    name="main", secret="s3cret"
                )

        handler = ConfigurationHandler(ProviderConfig)
        assert handler("authentication.credentials.jwt_0.algorithm") == "HS256"

    def test_call_site_default_applies_to_an_unwritten_value(self) -> None:
        handler = ConfigurationHandler(TwoAppConfig)
        assert handler("server.max_threads", default=7) == 7

    def test_a_missing_path_is_a_noisy_key_error(self) -> None:
        handler = ConfigurationHandler(TwoAppConfig)
        with pytest.raises(KeyError, match="server.tls"):
            handler("server.tls")


def write_defaults_recipe(
    base_dir: Path, filename: str = "config.py", mount_path: str = "/srv/deployment"
) -> Path:
    """A recipe file in *base_dir* deviating from the package defaults.

    ``mount_path`` needs to be a directory that EXISTS only where the recipe
    reaches a real ``StorageManager`` — genro-storage's local backend validates
    the anchor at ``configure()`` time, never at recipe time.
    """
    path = base_dir / filename
    path.write_text(
        "from typing import Any\n"
        "\n"
        "from kajenn.config import BaseConfiguration\n"
        "\n"
        "\n"
        "class DeploymentConfiguration(BaseConfiguration):\n"
        "    def server_section(self, cfg: Any) -> None:\n"
        "        cfg.server(host='10.0.0.1', port=9999)\n"
        "\n"
        "    def storage_mounts(self, section: Any) -> None:\n"
        f"        section.local(name='site', base_path={mount_path!r})\n",
        encoding="utf-8",
    )
    return path


class TestParentRecipes:
    """``BaseConfiguration`` + the declared defaults layer + the site recipe.

    ``DefaultConfig.parents_for()`` is what ``AsgiServer`` hands the handler: the
    package defaults lowest, the recipe's own defaults source over them, the site
    recipe last and winning.
    """

    def test_a_site_inherits_the_default_mounts_and_adds_its_key(
        self, tmp_path: Path
    ) -> None:
        class KeyOnlyConfig(BaseConfiguration):
            storage_key = "k1"

        parents = DefaultConfig(tmp_path).parents_for(KeyOnlyConfig)
        mounts, storage_key = ConfigurationHandler(KeyOnlyConfig, parents=parents).storage_config()
        assert storage_key == "k1"
        assert mounts == [
            {**DEFAULT_SITE_MOUNT, "base_path": str(Path.cwd())},
            {**DEFAULT_HOME_MOUNT, "base_path": str(Path.cwd())},
        ]

    def test_only_the_package_defaults_are_layered_without_a_defaults_recipe(
        self, tmp_path: Path
    ) -> None:
        assert DefaultConfig(tmp_path).parents_for(BaseConfiguration) == [BaseConfiguration]

    def test_the_conventional_recipe_joins_the_chain_when_its_file_exists(
        self, tmp_path: Path
    ) -> None:
        declared = write_defaults_recipe(tmp_path)
        assert DefaultConfig(tmp_path).parents_for(BaseConfiguration) == [
            BaseConfiguration,
            declared,
        ]

    def test_the_defaults_layer_overrides_the_base_and_loses_to_the_site(
        self, tmp_path: Path
    ) -> None:
        write_defaults_recipe(tmp_path)

        class SiteConfig(AsgiConfigBuilder):
            """Says one thing only: the layers under it supply everything else."""

            def main(self, root: Any) -> None:
                root.configuration().server(host="127.0.0.1")

        parents = DefaultConfig(tmp_path).parents_for(SiteConfig)
        handler = ConfigurationHandler(SiteConfig, parents=parents)
        assert handler("server.host") == "127.0.0.1"      # the site wins
        assert handler("server.port") == 9999             # the defaults layer holds
        mounts, _ = handler.storage_config()              # over the package default
        # The deployment layer declares ``site:`` only, so ``home:`` is the one
        # the package defaults wrote under it.
        assert mounts == [
            {**DEFAULT_SITE_MOUNT, "base_path": "/srv/deployment"},
            {**DEFAULT_HOME_MOUNT, "base_path": str(Path.cwd())},
        ]

    def test_a_key_only_section_without_parents_yields_no_mount(self) -> None:
        """The guard: a storage section with no mount child is not a crash."""

        class KeyOnlyConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().storage(app=StorageManager, storage_key="k1")

        mounts, storage_key = ConfigurationHandler(KeyOnlyConfig).storage_config()
        assert mounts == []
        assert storage_key == "k1"


class TestDeclaredDefaultConfig:
    """``default_config`` on the recipe: which defaults source, declared by the recipe.

    Unset (or ``True``) takes the conventional ``<base_dir>/config.py`` when it is
    there, ``False`` takes nothing, a path takes that file and must find it.
    """

    def test_unset_takes_the_conventional_file(self, tmp_path: Path) -> None:
        declared = write_defaults_recipe(tmp_path)

        class SiteConfig(BaseConfiguration):
            pass

        assert SiteConfig.default_config is None
        assert DefaultConfig(tmp_path).parents_for(SiteConfig) == [BaseConfiguration, declared]

    def test_true_reads_the_conventional_file_like_an_unset_attribute(
        self, tmp_path: Path
    ) -> None:
        declared = write_defaults_recipe(tmp_path)

        class SiteConfig(BaseConfiguration):
            default_config = True

        assert DefaultConfig(tmp_path).parents_for(SiteConfig) == [BaseConfiguration, declared]

    def test_false_refuses_the_layer_even_when_the_file_is_there(self, tmp_path: Path) -> None:
        write_defaults_recipe(tmp_path)

        class SiteConfig(BaseConfiguration):
            default_config = False

        assert DefaultConfig(tmp_path).parents_for(SiteConfig) == [BaseConfiguration]

    def test_an_explicit_path_is_layered_from_wherever_it_lives(self, tmp_path: Path) -> None:
        elsewhere = write_defaults_recipe(tmp_path, filename="shared_defaults.py")

        class SiteConfig(BaseConfiguration):
            default_config = str(elsewhere)

        parents = DefaultConfig(tmp_path).parents_for(SiteConfig)
        assert parents == [BaseConfiguration, elsewhere]
        assert ConfigurationHandler(SiteConfig, parents=parents)("server.port") == 9999

    def test_an_explicit_path_that_does_not_exist_is_a_config_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.py"

        class SiteConfig(BaseConfiguration):
            default_config = missing

        with pytest.raises(ConfigError, match="does not exist"):
            DefaultConfig(tmp_path).parents_for(SiteConfig)

    def test_a_config_py_source_declares_its_own_default_config(self, tmp_path: Path) -> None:
        """The attribute is read off the recipe class a path source defines."""
        write_defaults_recipe(tmp_path)
        site = tmp_path / "site.py"
        site.write_text(
            "from kajenn.config import BaseConfiguration\n"
            "\n"
            "\n"
            "class SiteConfiguration(BaseConfiguration):\n"
            "    default_config = False\n",
            encoding="utf-8",
        )
        assert DefaultConfig(tmp_path).parents_for(site) == [BaseConfiguration]

    def test_a_config_py_source_must_define_exactly_one_recipe(self, tmp_path: Path) -> None:
        site = tmp_path / "site.py"
        site.write_text("value = 1\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="exactly one ConfigBuilder subclass"):
            DefaultConfig(tmp_path).parents_for(site)


class TestHomeResolution:
    """``base_dir``: the explicit argument, then ``KAJENN_HOME``, then ``~``."""

    def test_the_env_var_is_the_default_base_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOME_ENV, str(tmp_path))
        assert DefaultConfig().base_dir == tmp_path
        assert DefaultConfig().path == tmp_path / "config.py"

    def test_the_explicit_argument_wins_over_the_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOME_ENV, str(tmp_path / "from_env"))
        assert DefaultConfig(tmp_path / "explicit").base_dir == tmp_path / "explicit"

    def test_the_home_directory_is_the_last_resort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HOME_ENV, raising=False)
        assert DefaultConfig().base_dir == Path.home() / ".kajenn"

    def test_the_cli_registry_follows_the_same_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOME_ENV, str(tmp_path))
        assert SitesRegistry().base_dir == tmp_path
        assert SitesRegistry().sites_dir == tmp_path / "sites"


class TestServerLayersTheDeclaredDefaults:
    """The production wiring: ``AsgiServer(config=...)`` layers what the recipe declares."""

    def test_the_server_reads_the_conventional_defaults_recipe(
        self, kajenn_home: Path
    ) -> None:
        write_defaults_recipe(kajenn_home, mount_path=str(kajenn_home))

        class SiteConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().server(host="127.0.0.1")

        server = AsgiServer(config=SiteConfig)
        assert server.config is not None
        assert server.config("server.host") == "127.0.0.1"      # the site wins
        assert server.config("server.port") == 9999             # from the defaults layer

    def test_a_recipe_declining_the_layer_sees_only_the_package_defaults(
        self, kajenn_home: Path
    ) -> None:
        write_defaults_recipe(kajenn_home)

        class SiteConfig(BaseConfiguration):
            default_config = False

        server = AsgiServer(config=SiteConfig)
        assert server.config is not None
        with pytest.raises(KeyError, match="server.port"):
            server.config("server.port")
