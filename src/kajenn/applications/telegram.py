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

"""Telegram application: registered RoutingClass bots, webhook ingress and replies.

One application owns an application-wide persistence route. That trusted route
accepts ``operation``, ``application=<code>`` and ``record=<dict>``. ``list``
returns registration dictionaries and ``save`` durably upserts a registration.
``get_receipt``/``save_receipt``/``prune_receipts`` keep expiring update receipts
separate from registrations and the task spool. The provider must protect the
token and webhook secret at rest.

Each bot class declares ``grammar`` and accepts ``application``, ``code`` and a
callable ``config`` read door. Registration config maps element names to their
attributes, validated by that grammar. Only importable module-level classes are
persisted. Name/icon are local registry metadata, not Telegram profile changes.

POST /<application mount>/<bot code> verifies Telegram's secret, then stages a
command in kajenn.tasks before acknowledging it. Task IDs include the application,
bot and update IDs. Receipts deduplicate for 48 hours even after spool cleanup;
expired receipts are pruned on startup and ingress. The task resolves the
bot's command route with anonymous auth filters: provider credentials authenticate
delivery, never the sender's application identity. Protected commands stay closed.
Handlers receive ``text`` (the command tail) and return text or None.

This transport processes message commands only. Polling, identity onboarding,
dialogs, media and automatic outbound retries are outside this contract.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import importlib
import json
import re
import secrets
import time
from typing import Any

import httpx
from genro_bag import BagResolver
from genro_builders.builder import BuilderBase, element
from genro_builders.contrib.config import ConfigHandler
from genro_routes import RoutingClass, route

from ..application import ApplicationGrammar
from ..exceptions import HTTPForbidden, HTTPNotFound, HTTPUnauthorized
from ..lifespan import FatalBootError
from ..request import Request
from ..response import Response
from ..routed_application import RoutedApplication
from ..server import BaseServer
from ..tasks import TaskManager, new_descriptor
from ..types import Receive, Scope, Send

__all__ = ["TelegramBotApplication", "TelegramBotGrammar"]

BOT_CODE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}")
UPDATE_RETENTION_SECONDS = 48 * 60 * 60
COMMAND = re.compile(r"/([a-z0-9_]{1,32})(?:@([a-zA-Z0-9_]+))?(?:\s+(.*))?", re.DOTALL)


class TelegramBotGrammar(ApplicationGrammar):
    """The Telegram application's registry and public webhook location."""

    @element(sub_tags="", node_label="telegram")
    def telegram(
        self, persistence_route: str | BagResolver, webhook_url: str | BagResolver
    ) -> None:
        """One persistence route and the public HTTPS URL of this app's mount."""


class _BotConfiguration(BuilderBase):
    """Mount one bot class's grammar and build its declared config elements."""

    def __init__(self, bot_class: type, values: dict[str, Any]) -> None:
        self.bot_class = bot_class
        self.values = values
        super().__init__()

    @element(node_label="bot", _meta={"subbuilder": "bot_class:grammar"})
    def bot(self, bot_class: type) -> None:
        """Root whose children belong to the bot class's grammar."""

    def main(self, root: Any) -> None:
        node = root.bot(bot_class=self.bot_class)
        for name, attrs in self.values.items():
            getattr(node, name)(**attrs)


class TelegramBotApplication(RoutedApplication):
    """Own independently configured bot instances behind verified webhooks.

    ``register_bot`` is a trusted in-process API, not a public HTTP route.
    ``client`` optionally supplies an httpx client (owned by the caller).
    Constructor registry/URL options override the application's grammar values.
    """

    grammar = TelegramBotGrammar

    def __init__(
        self,
        *,
        persistence_route: str | None = None,
        webhook_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        self._persistence_route = persistence_route
        self._webhook_url = webhook_url
        self._client = client
        self._owns_client = client is None
        self._bots: dict[str, RoutingClass] = {}
        self._registrations: dict[str, dict[str, Any]] = {}
        self._registry_lock = asyncio.Lock()
        self._ingress_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        super().__init__(**kwargs)

    @property
    def persistence_route(self) -> str:
        return self._persistence_route or self.config("telegram.persistence_route")

    @property
    def webhook_url(self) -> str:
        url = self._webhook_url or self.config("telegram.webhook_url")
        return self._validate_webhook_url(url)

    def _validate_webhook_url(self, url: str) -> str:
        """Require an absolute HTTPS mount URL before registration side effects."""
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or not parsed.host or parsed.query or parsed.fragment:
            raise ValueError("webhook_url must be an HTTPS URL without query or fragment")
        return url.rstrip("/")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    @property
    def bots(self) -> dict[str, RoutingClass]:
        return self._bots

    @property
    def registrations(self) -> dict[str, dict[str, Any]]:
        return self._registrations

    @property
    def registry_lock(self) -> asyncio.Lock:
        return self._registry_lock

    @property
    def ingress_lock(self) -> asyncio.Lock:
        return self._ingress_lock

    @property
    def ready(self) -> asyncio.Event:
        return self._ready

    def get_bot(self, code: str) -> RoutingClass:
        return self.bots[code]

    def get_bot_registration(self, code: str) -> dict[str, Any]:
        """Return a copy of the trusted registration, including its credentials."""
        return copy.deepcopy(self.registrations[code])

    def _require_server(self) -> BaseServer:
        server = self.server
        if server is None:
            raise RuntimeError("Telegram bot operations require an owning server")
        return server

    async def _call(self, node: Any, **kwargs: Any) -> Any:
        if asyncio.iscoroutinefunction(node):
            return await node(**kwargs)
        return await self._require_server().run_sync(lambda: node(**kwargs))

    async def _persist(self, operation: str, record: dict[str, Any] | None = None) -> Any:
        mount, separator, path = self.persistence_route.strip("/").partition("/")
        if not separator or not path:
            raise ValueError("persistence_route must be '<mount>/<route>'")
        app = self._require_server().application_at(mount)
        if app is None:
            raise LookupError(f"persistence application not found: {mount}")
        if not isinstance(app, RoutedApplication):
            raise TypeError("persistence application must be a RoutedApplication")
        node = app.route.node(path)
        return await self._call(node, operation=operation, application=self.code, record=record)

    def _build_bot(self, record: dict[str, Any]) -> RoutingClass:
        module, name = record["bot_class"].split(":", 1)
        bot_class = getattr(importlib.import_module(module), name)
        if not issubclass(bot_class, RoutingClass):
            raise TypeError("bot_class must inherit RoutingClass")
        builder = _BotConfiguration(bot_class, record["config"])
        config = ConfigHandler(builder)
        errors = builder.validate_source()
        if errors:
            raise ValueError(f"invalid bot configuration: {errors}")
        bot: RoutingClass = bot_class(application=self, code=record["code"], config=config)
        bot.route.plug("auth")
        getattr(self._require_server(), "arm_router")(bot.route)
        return bot

    async def _telegram(self, token: str, method: str, **payload: Any) -> Any:
        """Call Telegram without including credential-bearing URLs in errors."""
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json=payload,
            )
        except httpx.HTTPError:
            raise RuntimeError(f"Telegram {method} transport failed") from None
        if response.status_code != 200:
            raise RuntimeError(f"Telegram {method} failed (HTTP {response.status_code})")
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed")
        return data["result"]

    async def _activate(self, record: dict[str, Any], bot: RoutingClass, url: str) -> None:
        code = record["code"]
        # Expose the verified endpoint before Telegram can deliver its first update.
        self.bots[code] = bot
        self.registrations[code] = record
        await self._telegram(
            record["token"],
            "setWebhook",
            url=f"{url}/{code}",
            secret_token=record["webhook_secret"],
            allowed_updates=["message"],
        )

    async def activate_bot(self, code: str) -> RoutingClass:
        """Retry webhook activation for a saved bot, preserving its credentials."""
        async with self.registry_lock:
            bot = self.get_bot(code)
            await self._activate(self.registrations[code], bot, self.webhook_url)
            self.ready.set()
            return bot

    async def register_bot(
        self,
        *,
        code: str,
        bot_class: type[RoutingClass],
        token: str,
        name: str = "",
        icon: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> RoutingClass:
        """Validate, durably register, then activate a bot created with BotFather.

        Failed persistence leaves the bot inactive. Failed webhook activation
        leaves its registration available for ``activate_bot`` or startup. Duplicate
        codes and tokens are rejected; one Telegram token has only one webhook.
        """
        async with self.registry_lock:
            if not BOT_CODE.fullmatch(code):
                raise ValueError("invalid bot code")
            if code in self.registrations:
                raise ValueError(f"bot already registered: {code}")
            if any(r["token"] == token for r in self.registrations.values()):
                raise ValueError("bot token already registered")
            if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
                raise ValueError("invalid bot token format")
            if "." in bot_class.__qualname__:
                raise ValueError("bot_class must be a module-level class")
            record = {
                "code": code,
                "bot_class": f"{bot_class.__module__}:{bot_class.__name__}",
                "token": token,
                "name": name or code,
                "icon": icon,
                "config": copy.deepcopy(config or {}),
                "webhook_secret": secrets.token_urlsafe(32),
            }
            json.dumps(record)
            bot = self._build_bot(record)
            url = self.webhook_url
            user = await self._telegram(token, "getMe")
            if not user["is_bot"]:
                raise ValueError("Telegram credentials must identify a bot")
            record["username"] = user["username"]
            await self._persist("save", record)
            await self._activate(record, bot, url)
            self.ready.set()
            return bot

    async def on_startup(self) -> None:
        """Restore the registry and renew each webhook; a broken registry is fatal."""
        try:
            async with self.registry_lock:
                self.ready.clear()
                url = self.webhook_url
                records = await self._persist("list")
                await self._persist("prune_receipts", {"now": time.time()})
                for record in records:
                    await self._activate(record, self._build_bot(record), url)
                self.ready.set()
        except Exception as exc:
            raise FatalBootError("Telegram bot registry startup failed") from exc

    async def on_shutdown(self) -> None:
        """Close only the HTTP client owned by this application."""
        if self._owns_client and self._client is not None:
            await self.client.aclose()
            self._client = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        code = scope["path"].strip("/")
        if code not in self.registrations:
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await Response("Method Not Allowed", status_code=405)(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        secret = headers.get(b"x-telegram-bot-api-secret-token", b"")
        record = self.registrations[code]
        if not hmac.compare_digest(secret, record["webhook_secret"].encode()):
            await Response("Forbidden", status_code=403)(scope, receive, send)
            return
        request = Request(scope, receive, server=self.server, application=self)
        try:
            update = json.loads(await request.read_body())
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                raise ValueError("invalid update")
            message = update.get("message", {})
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            text = message.get("text", "")
            if not isinstance(text, str):
                raise ValueError("invalid text")
            command = COMMAND.fullmatch(text)
            if command:
                chat_id = message["chat"]["id"]
                if type(chat_id) is not int:
                    raise ValueError("invalid chat")
        except (ValueError, KeyError, TypeError):
            await Response("Bad Request", status_code=400)(scope, receive, send)
            return
        if command and (not command[2] or command[2].lower() == record["username"].lower()):
            digest = hashlib.sha256(
                f"{self.code}:{code}:{update['update_id']}".encode()
            ).hexdigest()
            task_id = f"telegram-{digest}"
            await self._stage_update(task_id, code, command[1], command[3] or "", chat_id)
        await Response("OK")(scope, receive, send)

    async def _stage_update(
        self,
        task_id: str,
        code: str,
        command: str,
        text: str,
        chat_id: int,
    ) -> None:
        """Persist the task and receipt before ACK; serialize concurrent deliveries.

        A receipt write failure leaves the task available for the retry to find.
        Receipt expiry is fixed at acceptance and never extended by redeliveries.
        """
        manager: TaskManager = getattr(self._require_server(), "tasks")
        mount = self.mount
        if mount is None:
            raise RuntimeError("Telegram application requires a resolved mount")
        async with self.ingress_lock:
            now = time.time()
            await self._persist("prune_receipts", {"now": now})
            expires_at = await self._persist("get_receipt", {"task_id": task_id})
            if expires_at is not None and expires_at > now:
                return
            if manager.spool.get(task_id) is None:
                descriptor = new_descriptor(
                    task_id,
                    owner=f"telegram:{code}",
                    mount=mount,
                    node_path="deliver_update",
                )
                manager.spool.create(
                    descriptor,
                    {
                        "bot_code": code,
                        "command": command,
                        "text": text,
                        "chat_id": chat_id,
                    },
                )
            await self._persist(
                "save_receipt",
                {
                    "task_id": task_id,
                    "expires_at": now + UPDATE_RETENTION_SECONDS,
                },
            )

    @route()
    async def deliver_update(self, bot_code: str, command: str, text: str, chat_id: int) -> None:
        """Task entry point; resolve only public commands, then deliver their text."""
        await self.ready.wait()
        bot = self.get_bot(bot_code)
        try:
            node = bot.route.node(command, errors=self.ROUTER_ERRORS)
            result = await self._call(node, text=text)
        except (HTTPNotFound, HTTPUnauthorized, HTTPForbidden):
            return
        if result is not None:
            if not isinstance(result, str):
                raise TypeError("Telegram command handlers must return str or None")
            await self.send_message(bot_code, chat_id, result)

    async def send_message(self, bot_code: str, chat_id: int, text: str) -> Any:
        """Send a plain-text message to a known Telegram chat."""
        if not 1 <= len(text) <= 4096:
            raise ValueError("Telegram text must contain between 1 and 4096 characters")
        return await self._telegram(
            self.registrations[bot_code]["token"], "sendMessage", chat_id=chat_id, text=text
        )
