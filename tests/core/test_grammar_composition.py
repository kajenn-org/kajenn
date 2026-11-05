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

"""An application's grammar is composed from every grammar declared along its MRO.

A mixin declares its own ``grammar``; an application class using the mixin
accepts the mixin's words in the recipe beside its own, with no grammar class
written by hand.
"""

from __future__ import annotations

from typing import Any

from genro_builders.builder import element
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn.server_app import ServerApplication


class GreetingMixinGrammar:
    """The words ``GreetingMixin`` brings into a recipe."""

    @element(sub_tags="", node_label="greeting")
    def greeting(self, text: str = None) -> None:
        """The greeting the application answers."""


class GreetingMixin:
    """A mixin with a grammar of its own."""

    grammar = GreetingMixinGrammar


class Site(GreetingMixin, RoutedApplication):
    """A routed application using the mixin, with no grammar of its own."""

    @route()
    def hello(self) -> dict[str, Any]:
        return {"greeting": self.config("greeting.text", default=None)}


class GreetingServer(GreetingMixin, ServerApplication):
    """The server application with the mixin: its own words and the mixin's."""


class SiteRecipe(AsgiConfigBuilder):
    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications()
        site = apps.application(code="site", app_class=Site)
        site.greeting(text="hello")
        site.parameters(title="Shop")


class ServerRecipe(AsgiConfigBuilder):
    def main(self, root: Any) -> None:
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=0)
        apps = cfg.applications()
        server_app = apps.application(code="_server", app_class=GreetingServer)
        server_app.greeting(text="welcome")
        server_app.login(max_attempts=4, backoff=9)


def test_a_mixin_word_reaches_the_application() -> None:
    # wf:contract: an application class whose mixin declares grammar = GreetingMixinGrammar
    # wf:contract: accepts greeting(text=...) under its application element, and reads it
    # wf:contract: back as self.config("greeting.text").
    server = AsgiServer(config=SiteRecipe)
    assert server.applications["site"].config("greeting.text") == "hello"


def test_the_inherited_words_survive_the_composition() -> None:
    # wf:contract: the words of ApplicationGrammar (parameters) stay available on an
    # wf:contract: application whose mixin brings a grammar.
    server = AsgiServer(config=SiteRecipe)
    assert server.applications["site"].config("parameters.title") == "Shop"


def test_a_richer_grammar_and_a_mixin_grammar_compose() -> None:
    # wf:contract: a ServerApplication subclass using the mixin accepts both the mixin's
    # wf:contract: greeting and ServerApplicationGrammar's login in the same recipe.
    server = AsgiServer(config=ServerRecipe)
    app = server.applications["_server"]
    assert type(app) is GreetingServer
    assert app.config("greeting.text") == "welcome"
    assert app.login_policy == {"max_attempts": 4, "backoff": 9}


def test_a_class_without_mixins_keeps_its_grammar_words() -> None:
    # wf:contract: ServerApplication alone still accepts login(...) after the composition.
    class PlainRecipe(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=0)
            cfg.applications().application(code="_server").login(max_attempts=2, backoff=1)

    app = AsgiServer(config=PlainRecipe).applications["_server"]
    assert type(app) is ServerApplication
    assert app.login_policy == {"max_attempts": 2, "backoff": 1}
