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

"""Telegram application: registered RoutingClass bots, outbound messages and webhooks.

``webhook_url`` selects reception on this deployment. With an HTTPS mount URL,
the application owns the bots' webhooks and executes incoming commands. Without
it, this application only sends: registration, restoration and activation never
set or delete a webhook, inbound HTTP returns 404, and task delivery is disabled.
A sender can share the central bot's token while replies go to the central
webhook. Send-only deployments need registration persistence but no task manager
or receipt operations.

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

Message commands, conversation text and inline callbacks are staged before ACK.
Conversation state and admission decisions use the same persistence route, with
atomic revision checks; see telegram_conversations for its persistence contract.
Bot grammars may inherit TelegramBotInstanceGrammar to opt into admin admission.
Outbound delivery supports bounded retries, text splitting, media, announcements
and native polls. Reminders reuse kajenn.tasks. Polling and application identity
provisioning are outside this contract.
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
from datetime import datetime

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
from .telegram_conversations import _Conversations, _TelegramAPIError
from .telegram_delivery import _Delivery

__all__ = ["TelegramBotApplication", "TelegramBotGrammar", "TelegramBotInstanceGrammar"]

BOT_CODE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}")
UPDATE_RETENTION_SECONDS = 48 * 60 * 60
COMMAND = re.compile(r"/([a-z0-9_]{1,32})(?:@([a-zA-Z0-9_]+))?(?:\s+(.*))?", re.DOTALL)


class TelegramBotGrammar(ApplicationGrammar):
    """The Telegram application's registry and optional public webhook location."""

    @element(sub_tags="", node_label="telegram")
    def telegram(
        self,
        persistence_route: str | BagResolver,
        webhook_url: str | BagResolver | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        send_interval: float = 0.0,
    ) -> None:
        """One persistence route; omit webhook_url for a send-only application."""


class TelegramBotInstanceGrammar:
    """Common instance options; bot grammars inherit and add their own elements."""

    @element(sub_tags="", node_label="access")
    def access(
        self,
        approval_required: bool = False,
        admins: list[int] | None = None,
        approval_policy: str = "first",
    ) -> None:
        """Optional admission by configured Telegram users: first decision or all approvals."""


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
    """Own bot instances, sending directly and optionally receiving webhooks.

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
        self._conversations = _Conversations(self)
        self._delivery = _Delivery(self)
        super().__init__(**kwargs)
        self.route.add_entry(
            self.deliver_reminder, metadata={"task": self.delivery.reminder_task_name}
        )

    @property
    def delivery(self) -> _Delivery:
        return self._delivery

    @property
    def persistence_route(self) -> str:
        return self._persistence_route or self.config("telegram.persistence_route")

    @property
    def webhook_url(self) -> str | None:
        """The public HTTPS mount URL, or None when this app only sends."""
        url = self._webhook_url
        if url is None:
            url = self.config("telegram.webhook_url", default=None)
        if url is None:
            return None
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

    @property
    def conversations(self) -> _Conversations:
        return self._conversations

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
        # RouterNode uses the asyncio coroutine marker on Python 3.11.
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
        self.conversations.validate_access(bot)
        bot.route.plug("auth")
        getattr(self._require_server(), "arm_router")(bot.route)
        return bot

    async def _telegram(self, token: str, method: str, **payload: Any) -> Any:
        """Call Telegram with bounded retries and sanitized transport errors."""
        return await self.delivery.request(token, method, **payload)

    async def _activate(self, record: dict[str, Any], bot: RoutingClass, url: str | None) -> None:
        code = record["code"]
        # Expose the verified endpoint before Telegram can deliver its first update.
        self.bots[code] = bot
        self.registrations[code] = record
        if url is None:
            return
        await self._telegram(
            record["token"],
            "setWebhook",
            url=f"{url}/{code}",
            secret_token=record["webhook_secret"],
            allowed_updates=["message", "callback_query", "poll", "poll_answer"],
        )

    async def activate_bot(self, code: str) -> RoutingClass:
        """Activate a saved bot; only receivers configure a webhook."""
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
        codes and tokens within this application are rejected. A separate
        send-only application can use the same token without replacing its webhook.
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
        """Restore bots, renewing webhooks only when reception is configured."""
        try:
            async with self.registry_lock:
                self.ready.clear()
                url = self.webhook_url
                records = await self._persist("list")
                if url is not None:
                    await self._persist("prune_receipts", {"now": time.time()})
                for record in records:
                    await self._activate(record, self._build_bot(record), url)
                    if url is not None:
                        await self.conversations.restore_admissions(record["code"])
                self.ready.set()
        except Exception as exc:
            raise FatalBootError("Telegram bot registry startup failed") from exc

    async def on_shutdown(self) -> None:
        """Close only the HTTP client owned by this application."""
        if self._owns_client and self._client is not None:
            await self.client.aclose()
            self._client = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.webhook_url is None:
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
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
            poll = update.get("poll")
            answer = update.get("poll_answer")
            if poll is not None and (
                not isinstance(poll, dict) or not isinstance(poll.get("id"), str)
            ):
                raise ValueError("invalid poll")
            if answer is not None:
                if not isinstance(answer, dict) or not isinstance(answer.get("poll_id"), str):
                    raise ValueError("invalid poll answer")
                voter = answer.get("user") or answer.get("voter_chat")
                if not isinstance(voter, dict) or type(voter.get("id")) is not int:
                    raise ValueError("invalid poll voter")
                if not isinstance(answer.get("option_ids"), list) or any(
                    type(i) is not int or i < 0 for i in answer["option_ids"]
                ):
                    raise ValueError("invalid poll options")
            message = update.get("message", {})
            query = update.get("callback_query")
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            text = message.get("text", "")
            if not isinstance(text, str):
                raise ValueError("invalid text")
            command = COMMAND.fullmatch(text)
            chat_id = 0
            if text:
                chat_id = message["chat"]["id"]
                if type(chat_id) is not int:
                    raise ValueError("invalid chat")
                sender = message.get("from", {})
                if not isinstance(sender, dict):
                    raise ValueError("invalid sender")
            if query is not None:
                if (
                    not isinstance(query, dict)
                    or not isinstance(query.get("id"), str)
                    or not isinstance(query.get("data", ""), str)
                    or type(query["from"]["id"]) is not int
                ):
                    raise ValueError("invalid callback")
                callback_message = query.get("message")
                if callback_message is not None and (
                    not isinstance(callback_message, dict)
                    or type(callback_message.get("message_id")) is not int
                    or not isinstance(callback_message.get("chat"), dict)
                    or type(callback_message["chat"].get("id")) is not int
                ):
                    raise ValueError("invalid callback message")
        except (ValueError, KeyError, TypeError):
            await Response("Bad Request", status_code=400)(scope, receive, send)
            return
        addressed = (
            not command or not command[2] or command[2].lower() == record["username"].lower()
        )
        if poll is not None or answer is not None or query is not None or (text and addressed):
            digest = hashlib.sha256(
                f"{self.code}:{code}:{update['update_id']}".encode()
            ).hexdigest()
            task_id = f"telegram-{digest}"
            await self._stage_update(
                task_id,
                code,
                command[1] if command else "",
                command[3] or "" if command else text,
                chat_id,
                update,
            )
        await Response("OK")(scope, receive, send)

    async def _stage_update(
        self,
        task_id: str,
        code: str,
        command: str,
        text: str,
        chat_id: int,
        update: dict[str, Any] | None = None,
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
                        "update": update,
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
    async def deliver_update(
        self,
        bot_code: str,
        command: str,
        text: str,
        chat_id: int,
        update: dict[str, Any] | None = None,
    ) -> None:
        """Task entry point; resolve only public commands, then deliver their text."""
        if self.webhook_url is None:
            raise RuntimeError("Telegram webhook reception is disabled")
        await self.ready.wait()
        bot = self.get_bot(bot_code)
        if update is not None and ("poll" in update or "poll_answer" in update):
            await self.delivery.receive_poll(bot_code, update)
            return
        if update is not None and "callback_query" in update:
            await self.conversations.handle_callback(bot_code, update["callback_query"])
            return
        message = (update or {}).get("message", {"chat": {"id": chat_id}})
        if not await self.conversations.admit_sender(bot_code, message):
            return
        if not command:
            await self.conversations.handle_message(bot_code, message)
            return
        try:
            node = bot.route.node(command, errors=self.ROUTER_ERRORS)
            result = await self._call(node, text=text)
        except (HTTPNotFound, HTTPUnauthorized, HTTPForbidden):
            return
        if result is not None:
            if not isinstance(result, str):
                raise TypeError("Telegram command handlers must return str or None")
            await self.send_message(bot_code, chat_id, result)

    async def send_message(
        self, bot_code: str, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> Any:
        """Send a plain-text message to a known Telegram chat."""
        if not 1 <= len(text) <= 4096:
            raise ValueError("Telegram text must contain between 1 and 4096 characters")
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._telegram(self.registrations[bot_code]["token"], "sendMessage", **payload)

    async def create_conversation(
        self,
        bot_code: str,
        *,
        participants: list[dict[str, Any]],
        route: str,
        context: dict[str, Any] | None = None,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        """Persist an independent conversation; participants include user_id and chat_id."""
        async with self.conversations.lock:
            return await self.conversations.create_record(
                bot_code, participants, route, context, expires_at
            )

    async def get_conversation(self, bot_code: str, conversation_id: str) -> dict[str, Any]:
        """Read one conversation through the application persistence route."""
        async with self.conversations.lock:
            record = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            return record

    async def send_conversation_message(
        self,
        bot_code: str,
        conversation_id: str,
        user_id: int,
        text: str,
        *,
        buttons: dict[str, str] | None = None,
        chat_id: int | None = None,
    ) -> Any:
        """Send to one participant; buttons map labels to routed action names."""
        async with self.conversations.lock:
            record = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            if record["state"] != "open":
                raise ValueError("conversation is closed")
            return await self.conversations.send_record_message(
                record, user_id, text, buttons, chat_id
            )

    async def update_conversation_context(
        self, bot_code: str, conversation_id: str, context: dict[str, Any], *, revision: int
    ) -> dict[str, Any]:
        """Replace context only when the caller's snapshot is still current."""
        json.dumps(context)
        async with self.conversations.lock:
            record = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            if record["kind"] != "conversation" or record["state"] != "open":
                raise ValueError("context updates require an open general conversation")
            if record["revision"] != revision:
                raise ValueError("conversation revision conflict")
            record["context"] = copy.deepcopy(context)
            await self.conversations.save_record(record)
            return record

    async def close_conversation(
        self, bot_code: str, conversation_id: str, *, state: str = "closed"
    ) -> None:
        """Conclude or cancel a general conversation; admission decisions use admin callbacks."""
        if state not in ("closed", "cancelled"):
            raise ValueError("state must be closed or cancelled")
        async with self.conversations.lock:
            record = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            if record["kind"] != "conversation":
                raise ValueError("admission requires an administrator decision")
            if record["state"] == "open":
                record["state"] = state
                await self.conversations.save_record(record)

    async def send_text(self, bot_code: str, chat_id: int, text: str) -> list[Any]:
        """Send long plain text in ordered chunks, preserving every character."""
        return [
            await self.send_message(bot_code, chat_id, part)
            for part in self.delivery.get_text_parts(text)
        ]

    async def send_typing(self, bot_code: str, chat_id: int) -> Any:
        """Emit one typing indication; no background refresh loop is started."""
        return await self._telegram(
            self.registrations[bot_code]["token"],
            "sendChatAction",
            chat_id=chat_id,
            action="typing",
        )

    async def send_media(
        self,
        bot_code: str,
        chat_id: int,
        kind: str,
        media: str | bytes,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        """Send a document, photo, video, audio, voice or animation by reference or upload."""
        return await self.delivery.send_media(
            bot_code, chat_id, kind, media, filename=filename, caption=caption
        )

    async def send_document(
        self,
        bot_code: str,
        chat_id: int,
        document: str | bytes,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        """Send a file_id, Telegram-fetchable URL or bytes with a filename."""
        return await self.send_media(
            bot_code, chat_id, "document", document, filename=filename, caption=caption
        )

    async def send_announcement(
        self, bot_code: str, chat_ids: list[int], text: str
    ) -> list[dict[str, Any]]:
        """Send once per distinct destination and report complete, partial or failed delivery."""
        self.get_bot(bot_code)
        parts = self.delivery.get_text_parts(text)
        if any(type(chat_id) is not int for chat_id in chat_ids):
            raise ValueError("announcement destinations must be integer chat IDs")
        results = []
        for chat_id in dict.fromkeys(chat_ids):
            result: dict[str, Any] = {"chat_id": chat_id, "status": "sent", "messages": []}
            try:
                for part in parts:
                    result["messages"].append(await self.send_message(bot_code, chat_id, part))
            except _TelegramAPIError as exc:
                result.update(
                    status="uncertain"
                    if exc.outcome_uncertain
                    else "partial"
                    if result["messages"]
                    else "failed",
                    error=str(exc),
                )
            results.append(result)
        return results

    async def queue_announcement(self, bot_code: str, chat_ids: list[int], text: str) -> str:
        """Stage an announcement in kajenn.tasks; results are kept in the task spool."""
        self.get_bot(bot_code)
        self.delivery.get_text_parts(text)
        if not chat_ids or any(type(c) is not int for c in chat_ids):
            raise ValueError("announcement requires integer chat IDs")
        manager = self.delivery.manager
        mount = self.mount
        if mount is None:
            raise RuntimeError("Telegram application requires a resolved mount")
        task_id = f"telegram-announcement-{secrets.token_hex(12)}"
        descriptor = new_descriptor(
            task_id, owner=f"telegram:{bot_code}", mount=mount, node_path="deliver_announcement"
        )
        params = {"bot_code": bot_code, "chat_ids": chat_ids, "text": text}
        await self._require_server().run_sync(lambda: manager.spool.create(descriptor, params))
        return task_id

    @route()
    async def deliver_announcement(
        self, bot_code: str, chat_ids: list[int], text: str
    ) -> list[dict[str, Any]]:
        """Task entry point for an announcement, available on send-only deployments too."""
        await self.ready.wait()
        return await self.send_announcement(bot_code, chat_ids, text)

    async def send_poll(
        self,
        bot_code: str,
        chat_id: int,
        question: str,
        options: list[str],
        *,
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        route: str | None = None,
    ) -> Any:
        """Send and track a native regular poll; optionally route result events to the bot."""
        return await self.delivery.send_poll(
            bot_code,
            chat_id,
            question,
            options,
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
            route=route,
        )

    async def get_poll(self, bot_code: str, poll_id: str) -> dict[str, Any]:
        """Read persisted poll totals and the latest received answer per voter."""
        return await self.delivery.get_poll(bot_code, poll_id)

    async def stop_poll(self, bot_code: str, poll_id: str) -> Any:
        """Close a tracked native poll and save its final totals."""
        return await self.delivery.stop_poll(bot_code, poll_id)

    async def schedule_reminder(
        self,
        bot_code: str,
        chat_id: int,
        text: str,
        *,
        when: datetime,
        conversation_id: str | None = None,
        user_id: int | None = None,
    ) -> str:
        """Persist a one-shot reminder, optionally bound to an open conversation participant."""
        return await self.delivery.schedule_reminder(
            bot_code, chat_id, text, when=when, conversation_id=conversation_id, user_id=user_id
        )

    async def get_reminder(self, code: str) -> dict[str, Any]:
        """Read this application's reminder schedule and delivery state."""
        return await self.delivery.get_reminder(code)

    async def cancel_reminder(self, code: str) -> bool:
        """Cancel a pending reminder; False means it has already left pending state."""
        return await self.delivery.cancel_reminder(code)

    async def deliver_reminder(self, code: str) -> None:
        """Scheduler entry point, dynamically registered with an application-specific task name."""
        await self.delivery.deliver_reminder(code)
