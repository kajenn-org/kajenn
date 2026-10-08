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

"""A webhook bot with an application-owned encrypted filesystem registry."""

import json

from genro_bag.resolvers import EnvResolver
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.applications import TelegramBotApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from kajenn.response import Response

from examples.telegram_bot import DemoBot


class DemoRegistry(RoutedApplication):
    """Persistence provider called internally; no HTTP routes are exposed."""

    @route()
    def bots(self, operation: str, application: str, record: dict | None = None):
        directory = self.server.storage.node(f"site:telegram_registry/{application}")
        if operation == "list":
            if not directory.is_dir():
                return []
            return [
                json.loads(node.read_text()) for node in directory.children() if node.ext == "json"
            ]
        if operation == "save":
            directory.child(f"{record['code']}.json").write_text(
                json.dumps(record),
                encrypted=True,
            )
            return None
        receipts = directory.child("receipts")
        if operation == "prune_receipts":
            if receipts.is_dir():
                for node in receipts.children():
                    if node.ext == "json" and json.loads(node.read_text()) <= record["now"]:
                        node.delete()
            return None
        if operation == "get_receipt":
            node = receipts.child(f"{record['task_id']}.json")
            return json.loads(node.read_text()) if node.exists() else None
        if operation == "save_receipt":
            receipts.child(f"{record['task_id']}.json").write_text(
                json.dumps(record["expires_at"]),
                encrypted=True,
            )
            return None
        raise ValueError(f"unknown registry operation: {operation}")

    async def __call__(self, scope, receive, send):
        await Response("Not Found", status_code=404)(scope, receive, send)

    async def on_startup(self):
        telegram = self.server.applications["telegram"]
        for code in ("alpha", "beta"):
            token = self.config(f"parameters.{code}_token", default=None)
            if token and code not in telegram.bots:
                await telegram.register_bot(
                    code=code,
                    bot_class=DemoBot,
                    token=token,
                    name=f"Demo {code}",
                    config={"settings": {"dataset": code}},
                )


class TelegramDemoConfiguration(CONFIGURATION_TEMPLATES["default"]):
    """Load persisted bots, then optionally register alpha and beta from the environment."""

    storage_key = EnvResolver("GENRO_STORAGE_KEY")

    def applications_section(self, cfg):
        apps = cfg.applications()
        app = apps.application(code="telegram", app_class=TelegramBotApplication)
        app.telegram(
            persistence_route="registry/bots",
            webhook_url=EnvResolver("KAJENN_TELEGRAM_WEBHOOK_URL"),
        )
        registry = apps.application(code="registry", app_class=DemoRegistry)
        registry.parameters(
            alpha_token=EnvResolver("KAJENN_TELEGRAM_ALPHA_TOKEN", default=None),
            beta_token=EnvResolver("KAJENN_TELEGRAM_BETA_TOKEN", default=None),
        )
