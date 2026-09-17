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

"""The server application in a recipe: declared like any other (D-SA-10).

Cut out of ``tests/core/test_config.py`` when the server application became a
package of its own: the core suite must load no module of
``kajenn_server_app``, and this recipe names its class.
"""

from __future__ import annotations

from typing import Any

import pytest
from genro_bag.resolvers import EnvResolver

from kajenn import AsgiConfigBuilder, AsgiServer, ConfigurationHandler
from kajenn_server_app import ServerApplication

from tests.core.test_config import ApiApp, ShopApp, TwoAppConfig


OIDC_SECRET_ENV_VAR = "GENRO_TEST_OIDC_SECRET"


class LoginSurfaceConfig(TwoAppConfig):
    """The two-app site plus the server application, with its own login surface."""

    def applications_section(self, cfg: Any) -> None:
        """The two apps of the base recipe, then ``_server`` and its own words."""
        apps = cfg.applications(default="shop")
        apps.application(code="shop", mount="", app_class=ShopApp)
        apps.application(code="api", app_class=ApiApp)
        server_app = apps.application(code="_server", app_class=ServerApplication)
        server_app.login(max_attempts=3, backoff=10)
        oidc = server_app.oidc()
        oidc.provider(
            code="corp",
            issuer="https://idp.example.com",
            client_id="corp-client",
            client_secret=EnvResolver(OIDC_SECRET_ENV_VAR),
            scopes="openid profile",
            identity_claim="preferred_username",
            tags=["staff"],
        )
        oidc.provider(
            code="public",
            issuer="https://accounts.example.org",
            client_id="pub-client",
        )


class TestLoginSurface:
    """D-SA-10 and D-SA-11: the login surface is the app's own, in its own words.

    ``authentication.login``, ``authentication.oidc`` and its ``provider``
    children left the site dialect, and with them the handler's
    ``server_app_kwargs``/``oidc_providers`` and the ``server_app`` server
    kwarg. The same three words are declared by this package, under the
    ``application`` element of the app that owns them: they stay elements, so
    ``client_secret`` goes through the read stack and the secret never sits in
    the recipe.
    """

    def test_the_login_surface_reaches_the_server_app(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OIDC_SECRET_ENV_VAR, "oidc-s3cret")
        app = AsgiServer(config=LoginSurfaceConfig).applications["_server"]
        assert app.login_policy == {"max_attempts": 3, "backoff": 10}
        assert set(app.oidc_providers) == {"corp", "public"}
        corp = app.oidc_providers["corp"]
        assert corp["client_secret"] == "oidc-s3cret"
        assert corp["scopes"] == "openid profile"
        assert corp["identity_claim"] == "preferred_username"
        assert corp["tags"] == ["staff"]

    def test_provider_defaults_apply_per_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OIDC_SECRET_ENV_VAR, "oidc-s3cret")
        app = AsgiServer(config=LoginSurfaceConfig).applications["_server"]
        assert app.oidc_providers["public"] == {
            "issuer": "https://accounts.example.org",
            "client_id": "pub-client",
            "scopes": "openid email profile",
            "identity_claim": "email",
            "tags": [],
        }

    def test_each_declared_provider_is_a_registered_auth_method(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OIDC_SECRET_ENV_VAR, "oidc-s3cret")
        app = AsgiServer(config=LoginSurfaceConfig).applications["_server"]
        assert set(app.auth_section.methods) == {"password", "oidc:corp", "oidc:public"}

    def test_an_undeclared_login_surface_leaves_the_bare_app(self) -> None:
        app = ServerApplication()
        assert app.login_policy == {}
        assert app.oidc_providers == {}

    def test_a_provider_without_a_code_is_rejected_by_the_collection(self) -> None:
        class NoCodeConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                server_app = apps.application(code="_server", app_class=ServerApplication)
                server_app.oidc().provider(issuer="https://idp.example.com", client_id="x")

        with pytest.raises(ValueError, match="code"):
            ConfigurationHandler(NoCodeConfig)

    def test_a_duplicate_provider_code_is_rejected_by_the_collection(self) -> None:
        class DoubledCodeConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                apps = root.configuration().applications()
                server_app = apps.application(code="_server", app_class=ServerApplication)
                oidc = server_app.oidc()
                oidc.provider(code="corp", issuer="https://a.example.com", client_id="a")
                oidc.provider(code="corp", issuer="https://b.example.com", client_id="b")

        with pytest.raises(ValueError, match="corp"):
            ConfigurationHandler(DoubledCodeConfig)

    def test_a_recipe_writing_the_old_words_is_refused(self) -> None:
        class OldWordsConfig(AsgiConfigBuilder):
            def main(self, root: Any) -> None:
                root.configuration().authentication().login(max_attempts=3)

        with pytest.raises(AttributeError, match="login"):
            ConfigurationHandler(OldWordsConfig)
