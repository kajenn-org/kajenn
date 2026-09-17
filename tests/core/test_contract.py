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

"""Contract tests for the cooperative base classes (SPECIFICATION.md §4, D16).

The cooperative chain: base + two mixin-style layers + concrete class, each
layer peeling its own kwargs; leftovers raise ``TypeError`` naming them.
The ownership channel: ``server`` assigned exactly once at attach/mount.
The base answers: ``authenticate``/``session`` return ``None``.
"""

import asyncio

import pytest

from kajenn import BaseApplication, BaseServer


class AlphaMixin:
    """Mixin layer: peels ``alpha`` and forwards the rest."""

    def __init__(self, **kwargs):
        self.alpha = kwargs.pop("alpha")
        super().__init__(**kwargs)


class BetaMixin:
    """Mixin layer: peels ``beta`` and forwards the rest."""

    def __init__(self, **kwargs):
        self.beta = kwargs.pop("beta")
        super().__init__(**kwargs)


class ConcreteApp(AlphaMixin, BetaMixin, BaseApplication):
    """Cooperative chain: base + two mixin-style layers + concrete class."""


class TestCooperativeChain:
    def test_each_layer_receives_its_kwargs(self):
        app = ConcreteApp(alpha=1, beta=2, code="demo")
        assert app.alpha == 1
        assert app.beta == 2
        assert app.code == "demo"

    def test_leftover_kwarg_raises_naming_it(self):
        with pytest.raises(TypeError, match="bogus"):
            ConcreteApp(alpha=1, beta=2, bogus=3)

    def test_server_leftover_kwarg_raises_naming_it(self):
        with pytest.raises(TypeError, match="bogus"):
            BaseServer(applications=[BaseApplication(mount="")], bogus=3)


class TestOwnershipChannel:
    def test_server_is_none_until_attached(self):
        assert BaseApplication().server is None

    def test_registration_assigns_server(self):
        root = BaseApplication(mount="")
        server = BaseServer(applications=[root])
        assert root.server is server

    def test_registration_indexes_by_code(self):
        api = BaseApplication(code="api")
        server = BaseServer(applications=[BaseApplication(mount=""), api])
        assert api.server is server
        assert server.applications["api"] is api

    def test_second_assignment_raises(self):
        root = BaseApplication(mount="")
        BaseServer(applications=[root])
        with pytest.raises(RuntimeError):
            BaseServer(applications=[root])

    def test_serving_the_same_app_on_a_second_server_raises(self):
        api = BaseApplication(code="api")
        BaseServer(applications=[api])
        with pytest.raises(RuntimeError):
            BaseServer(applications=[api])


class TestApplicationIdentity:
    def test_code_defaults_to_the_class_name_lowercased(self):
        assert BaseApplication().code == "baseapplication"

    def test_mount_defaults_to_the_code(self):
        app = BaseApplication(code="api")
        assert app.mount == "api"
        assert BaseServer(applications=[app]).application_at("api") is app

    def test_an_empty_mount_is_the_root_not_a_missing_value(self):
        # ``mount=""`` IS the site root: reading it as "unset" would silently
        # move the app to ``/<code>``.
        root = BaseApplication(code="shop", mount="")
        server = BaseServer(applications=[root])
        assert root.mount == ""
        assert server.root_application is root
        assert server.application_at("") is root
        assert server.application_at("shop") is None

    def test_class_attributes_are_the_defaults(self):
        class Fixed(BaseApplication):
            code = "fixed"
            mount = "elsewhere"

        assert (Fixed().code, Fixed().mount) == ("fixed", "elsewhere")
        overridden = Fixed(code="other", mount="")
        assert (overridden.code, overridden.mount) == ("other", "")


class TestServerContract:
    def test_duplicate_code_raises(self):
        with pytest.raises(ValueError, match="api"):
            BaseServer(applications=[BaseApplication(code="api"), BaseApplication(code="api")])

    def test_duplicate_mount_raises(self):
        with pytest.raises(ValueError, match="'api'"):
            BaseServer(
                applications=[
                    BaseApplication(code="one", mount="api"),
                    BaseApplication(code="two", mount="api"),
                ]
            )

    def test_a_server_of_mounts_only_has_no_root_application(self):
        server = BaseServer(applications=[BaseApplication(code="api")])
        assert server.root_application is None
        assert server.default_application is None

    def test_a_default_naming_no_served_application_raises(self):
        with pytest.raises(ValueError, match="ghost"):
            BaseServer(applications=[BaseApplication(code="api")], default="ghost")

    def test_the_default_is_read_back_as_the_application(self):
        api = BaseApplication(code="api")
        server = BaseServer(applications=[api], default="api")
        assert server.default_application is api

    def test_authenticate_answers_nobody(self):
        server = BaseServer(applications=[BaseApplication(mount="")])
        assert server.authenticate(None) is None

    def test_session_answers_none(self):
        server = BaseServer(applications=[BaseApplication(mount="")])
        assert server.session(None) is None


class TestAppContract:
    def test_lifecycle_hooks_exist_and_default_to_noop(self):
        app = BaseApplication()
        assert app.on_startup() is None
        assert app.on_shutdown() is None

    def test_base_asgi_call_is_not_implemented(self):
        app = BaseApplication()
        with pytest.raises(NotImplementedError):
            asyncio.run(app({}, None, None))
