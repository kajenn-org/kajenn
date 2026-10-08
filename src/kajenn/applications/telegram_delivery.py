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

"""Telegram outbound requests, native polls and task-backed reminders.

Requests are serialized per token within this application. Configured spacing
and bounded retries apply to connection establishment errors, HTTP 429 and 5xx.
429 honors retry_after. Read/write failures have an uncertain outcome and are
never retried automatically. A retried 5xx can still duplicate a Telegram send;
this is not an exactly-once transport. Credentials never enter raised errors.

Poll records use get_poll/save_poll on the application's persistence route.
Answers track update IDs per voter to reject stale deliveries and preserve vote
withdrawals. Optional bot routes receive event and poll snapshots through the
anonymous router; poll participation does not grant application access.

One-shot reminders use kajenn.tasks schedules with a task name unique to the
application code. Their records contain payloads, never bot tokens. They resume
from overdue schedules on startup and skip closed conversations. A sending state
left by interruption becomes uncertain and requires an operator's decision;
it is never resent silently. Scheduler records/logs follow the task-store's
storage policy, independently of the bot credential provider.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import mimetypes
import secrets
import time
from datetime import datetime
from typing import Any

import httpx

from ..exceptions import HTTPForbidden, HTTPNotFound, HTTPUnauthorized
from .telegram_conversations import _TelegramAPIError

MEDIA_METHODS = {
    "document": "sendDocument",
    "photo": "sendPhoto",
    "video": "sendVideo",
    "audio": "sendAudio",
    "voice": "sendVoice",
    "animation": "sendAnimation",
}


class _Delivery:
    """Telegram-specific delivery services belonging to one application."""

    def __init__(self, application: Any) -> None:
        self.application = application
        self.token_locks: dict[str, asyncio.Lock] = {}
        self.last_request: dict[str, float] = {}
        self.cooldowns: dict[str, float] = {}
        self.poll_lock = asyncio.Lock()
        self.reminder_lock = asyncio.Lock()

    async def wait(self, delay: float) -> None:
        await asyncio.sleep(delay)

    async def request(
        self,
        token: str,
        method: str,
        *,
        upload: tuple[str, bytes, str] | None = None,
        upload_field: str | None = None,
        **payload: Any,
    ) -> Any:
        attempts = self.application.config("telegram.retry_attempts", default=3)
        retry_delay = self.application.config("telegram.retry_delay", default=1.0)
        interval = self.application.config("telegram.send_interval", default=0.0)
        if type(attempts) is not int or not 1 <= attempts <= 10:
            raise ValueError("retry_attempts must be between 1 and 10")
        if not all(
            isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
            for v in (retry_delay, interval)
        ):
            raise ValueError("delivery delays must be finite and nonnegative")
        async with self.token_locks.setdefault(token, asyncio.Lock()):
            uncertain = False
            for attempt in range(attempts):
                remaining = (
                    max(interval + self.last_request.get(token, 0), self.cooldowns.get(token, 0))
                    - time.monotonic()
                )
                if remaining > 0:
                    await self.wait(remaining)
                self.cooldowns.pop(token, None)
                error = None
                delay = retry_delay * (2**attempt)
                try:
                    url = f"https://api.telegram.org/bot{token}/{method}"
                    if upload is None:
                        response = await self.application.client.post(url, json=payload)
                    else:
                        if upload_field is None:
                            raise ValueError("upload_field is required for an upload")
                        data = {
                            key: value if isinstance(value, str) else json.dumps(value)
                            for key, value in payload.items()
                        }
                        response = await self.application.client.post(
                            url, data=data, files={upload_field: upload}
                        )
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                    error = f"Telegram {method} transport failed"
                except httpx.HTTPError:
                    raise _TelegramAPIError(
                        f"Telegram {method} transport failed (outcome uncertain)",
                        outcome_uncertain=True,
                    ) from None
                finally:
                    self.last_request[token] = time.monotonic()
                if error is None:
                    try:
                        result = response.json()
                    except ValueError:
                        result = {}
                    if not isinstance(result, dict):
                        result = {}
                    status = response.status_code
                    if status == 200 and result.get("ok") and "result" in result:
                        return result["result"]
                    if (
                        status == 400
                        and method == "editMessageText"
                        and str(result.get("description", "")).startswith(
                            "Bad Request: message is not modified"
                        )
                    ):
                        return True
                    error = f"Telegram {method} failed (HTTP {status})"
                    if status == 429:
                        delay = max(delay, result.get("parameters", {}).get("retry_after", delay))
                        self.cooldowns[token] = time.monotonic() + delay
                    elif status < 500:
                        raise _TelegramAPIError(error, outcome_uncertain=uncertain)
                    else:
                        uncertain = True
                if attempt + 1 == attempts:
                    raise _TelegramAPIError(error, outcome_uncertain=uncertain)
                await self.wait(delay)
                self.cooldowns.pop(token, None)

    def get_text_parts(self, text: str) -> list[str]:
        if not text:
            raise ValueError("Telegram text cannot be empty")
        parts: list[str] = []
        chars: list[str] = []
        units = 0
        for char in text:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > 4096:
                parts.append("".join(chars))
                chars, units = [], 0
            chars.append(char)
            units += size
        parts.append("".join(chars))
        return parts

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
        if kind not in MEDIA_METHODS:
            raise ValueError("unsupported Telegram media kind")
        if len(caption.encode("utf-16-le")) // 2 > 1024:
            raise ValueError("media caption exceeds 1024 UTF-16 units")
        payload = {"chat_id": chat_id, "caption": caption}
        upload = None
        if isinstance(media, bytes):
            if not filename or not media:
                raise ValueError("uploads require a filename and nonempty bytes")
            maximum = 10_000_000 if kind == "photo" else 50_000_000
            if len(media) > maximum:
                raise ValueError("media exceeds the Telegram upload limit")
            upload = (
                filename,
                media,
                mimetypes.guess_type(filename)[0] or "application/octet-stream",
            )
        elif isinstance(media, str) and media:
            payload[kind] = media
        else:
            raise ValueError("media must be a file_id, URL or bytes")
        return await self.request(
            self.application.registrations[bot_code]["token"],
            MEDIA_METHODS[kind],
            upload=upload,
            upload_field=kind,
            **payload,
        )

    async def get_poll(self, bot_code: str, poll_id: str) -> dict[str, Any]:
        record: dict[str, Any] | None = await self.application._persist(
            "get_poll", {"bot_code": bot_code, "poll_id": poll_id}
        )
        if record is None:
            raise LookupError("poll not found")
        return record

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
        if not 1 <= len(question) <= 300 or not 1 <= len(options) <= 12:
            raise ValueError("polls require a question of 1-300 characters and 1-12 options")
        if any(not isinstance(o, str) or not 1 <= len(o) <= 100 for o in options):
            raise ValueError("poll options must contain 1-100 characters")
        if route:
            self.application.get_bot(bot_code).route.node(
                route, errors=self.application.ROUTER_ERRORS
            )
        async with self.poll_lock:
            sent = await self.application._telegram(
                self.application.registrations[bot_code]["token"],
                "sendPoll",
                chat_id=chat_id,
                question=question,
                options=[{"text": o} for o in options],
                is_anonymous=is_anonymous,
                allows_multiple_answers=allows_multiple_answers,
            )
            await self.application._persist(
                "save_poll",
                {
                    "bot_code": bot_code,
                    "poll_id": sent["poll"]["id"],
                    "chat_id": chat_id,
                    "message_id": sent["message_id"],
                    "poll": sent["poll"],
                    "answers": {},
                    "update_id": -1,
                    "route": route,
                },
            )
            return sent

    async def stop_poll(self, bot_code: str, poll_id: str) -> Any:
        async with self.poll_lock:
            record = await self.get_poll(bot_code, poll_id)
            if record["poll"].get("is_closed"):
                return record["poll"]
            poll = await self.application._telegram(
                self.application.registrations[bot_code]["token"],
                "stopPoll",
                chat_id=record["chat_id"],
                message_id=record["message_id"],
            )
            record["poll"] = poll
            await self.application._persist("save_poll", record)
            return poll

    async def receive_poll(self, bot_code: str, update: dict[str, Any]) -> None:
        answer = update.get("poll_answer")
        poll_id = answer["poll_id"] if answer is not None else update["poll"]["id"]
        async with self.poll_lock:
            try:
                record = await self.get_poll(bot_code, poll_id)
            except LookupError:
                return
            if answer is not None:
                voter = answer.get("user") or answer.get("voter_chat")
                key = f"{'user' if 'user' in answer else 'chat'}:{voter['id']}"
                previous = record["answers"].get(key, {})
                if previous.get("update_id", -1) >= update["update_id"]:
                    return
                record["answers"][key] = dict(answer, update_id=update["update_id"])
            else:
                if record["update_id"] >= update["update_id"]:
                    return
                closed = record["poll"].get("is_closed", False)
                record["poll"] = dict(
                    update["poll"], is_closed=closed or update["poll"].get("is_closed", False)
                )
                record["update_id"] = update["update_id"]
            await self.application._persist("save_poll", record)
        if record["route"]:
            try:
                node = self.application.get_bot(bot_code).route.node(
                    record["route"], errors=self.application.ROUTER_ERRORS
                )
                await self.application._call(
                    node, event=copy.deepcopy(update), poll=copy.deepcopy(record)
                )
            except (HTTPNotFound, HTTPUnauthorized, HTTPForbidden):
                return

    @property
    def reminder_task_name(self) -> str:
        digest = hashlib.sha256(self.application.code.encode()).hexdigest()[:16]
        return f"telegram_{digest}_reminder"

    @property
    def manager(self) -> Any:
        manager = getattr(self.application._require_server(), "tasks", None)
        if manager is None:
            raise RuntimeError("Telegram reminders require kajenn.tasks")
        return manager

    async def get_reminder(self, code: str) -> dict[str, Any]:
        record: dict[str, Any] | None = await self.application._require_server().run_sync(
            lambda: self.manager.task_store.get(code)
        )
        if record is None or record["task_name"] != self.reminder_task_name:
            raise LookupError("reminder not found in this application")
        return record

    async def save_reminder(self, record: dict[str, Any]) -> None:
        await self.application._require_server().run_sync(
            lambda: self.manager.task_store.save(record)
        )

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
        self.application.get_bot(bot_code)
        self.get_text_parts(text)
        if when.tzinfo is None or when.utcoffset() is None or when.timestamp() <= time.time():
            raise ValueError("when must be a future timezone-aware datetime")
        if conversation_id is not None:
            record = await self.application.get_conversation(bot_code, conversation_id)
            if record["state"] != "open" or not any(
                p["user_id"] == user_id and p["chat_id"] == chat_id for p in record["participants"]
            ):
                raise ValueError("reminder requires an open conversation and matching participant")
            if len(self.get_text_parts(text)) > 1:
                raise ValueError("conversation reminders must fit one message")
        code = f"{self.reminder_task_name}_{secrets.token_hex(12)}"
        await self.save_reminder(
            {
                "code": code,
                "task_name": self.reminder_task_name,
                "target_kind": "task",
                "kwargs": {"code": code},
                "kind": "at",
                "spec": [when.isoformat()],
                "enabled": True,
                "next_run_ts": when.timestamp(),
                "delivery_state": "pending",
                "bot_code": bot_code,
                "chat_id": chat_id,
                "text": text,
                "conversation_id": conversation_id,
                "user_id": user_id,
            }
        )
        return code

    async def cancel_reminder(self, code: str) -> bool:
        async with self.reminder_lock:
            record = await self.get_reminder(code)
            if record["delivery_state"] != "pending":
                return False
            record.update(enabled=False, delivery_state="cancelled")
            await self.save_reminder(record)
            return True

    async def deliver_reminder(self, code: str) -> None:
        await self.application.ready.wait()
        async with self.reminder_lock:
            record = await self.get_reminder(code)
            if record["delivery_state"] == "sending":
                record.update(delivery_state="uncertain", enabled=False)
                await self.save_reminder(record)
                return
            if not record["enabled"] or record["delivery_state"] != "pending":
                return
            record["delivery_state"] = "sending"
            await self.save_reminder(record)
            try:
                if record["conversation_id"]:
                    async with self.application.conversations.lock:
                        conversation = await self.application.conversations.get_record(
                            record["bot_code"], record["conversation_id"]
                        )
                        await self.application.conversations.expire_record(conversation)
                        if conversation["state"] != "open":
                            record["delivery_state"] = "skipped"
                        else:
                            await self.application.conversations.send_record_message(
                                conversation,
                                record["user_id"],
                                record["text"],
                                chat_id=record["chat_id"],
                            )
                else:
                    await self.application.send_text(
                        record["bot_code"], record["chat_id"], record["text"]
                    )
            except Exception as exc:
                record.update(
                    delivery_state="uncertain"
                    if getattr(exc, "outcome_uncertain", False)
                    else "failed",
                    enabled=False,
                    delivery_error=f"{type(exc).__name__}: {exc}",
                )
                await self.save_reminder(record)
                raise
            if record["delivery_state"] == "sending":
                record["delivery_state"] = "sent"
            record["enabled"] = False
            await self.save_reminder(record)
