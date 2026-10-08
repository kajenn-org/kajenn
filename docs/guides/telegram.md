# Telegram bots

**Version:** 0.1 · **Last updated:** 2026-10-07 · **Status:** 🔴 DA REVISIONARE

`TelegramBotApplication` hosts independently configured `RoutingClass` bot
instances. Each instance uses a BotFather token and its own webhook at
`/<application mount>/<bot code>`. Multiple instances can use the same class.
There is no polling transport and no provider-neutral bot base class.

## Configure the application

```python
app = cfg.applications().application(
    code="telegram", app_class=TelegramBotApplication,
)
app.telegram(
    persistence_route="registry/bots",
    webhook_url="https://example.com/telegram",
)
```

The URL is the externally reachable HTTPS URL of the application's mount,
including any reverse-proxy prefix. The single persistence route applies to
every bot. It is an internal route reference, not a URL fetched over HTTP.

## Register instances

From trusted application code, after the Telegram application's startup:

```python
await telegram.register_bot(
    code="alpha",
    bot_class=DemoBot,
    name="Alpha assistant",
    icon="alpha.png",
    token=token_from_secret_store,
    config={"settings": {"greeting": "Hello", "dataset": "alpha"}},
)
```

The API validates the class-owned grammar, checks the token with `getMe`, saves
the registration and calls `setWebhook` with a generated secret. Duplicate codes
or tokens are rejected. `name` and `icon` are local metadata: they do not change
the Telegram profile. Registration is a Python API, not an unauthenticated
management endpoint. A bot class must be importable at module level on restart.

## Define a bot

See `examples/telegram_bot/__init__.py`: `DemoBot` is a `RoutingClass` with
`/hello` and `/echo` commands. Its constructor receives `application`, `code`
and `config`, a callable read door backed by the class's `grammar`.

The registration's `config` maps grammar element names to their attribute
dictionaries. This first version accepts a flat set of elements. Grammar
defaults apply through `config("settings.greeting")`; unknown elements and
attributes are refused. Each registration creates a new router instance.

Command handlers accept `text`, the text after the command, and return a string
or `None`. Sync handlers run in the server pool; async handlers run on its loop.
Commands addressed to another bot are ignored, as are non-command messages.
Routing's auth plugin remains active: Telegram webhook verification authenticates
the delivery only. There is no sender-to-avatar resolver in this small example,
so protected commands cannot be called by Telegram senders.

## Persistence route contract

The configured route accepts these keyword arguments:

| Argument | Meaning |
|---|---|
| `operation` | `list`, `save`, `get_receipt`, `save_receipt`, or `prune_receipts` |
| `application` | Telegram application's code, the registry namespace |
| `record` | Operation payload; `None` for `list` |

`list` returns a list of records. `save` durably upserts one record before
returning. Records include code, importable class reference, token, generated
webhook secret, username, local metadata and grammar configuration. The provider
must protect credentials at rest and keep the route inaccessible to public
requests. It can use a database or the application's storage.

Update receipts are separate from bot registrations:

| Operation | Payload | Result |
|---|---|---|
| `get_receipt` | `{"task_id": "..."}` | Expiry timestamp, or `None` |
| `save_receipt` | `{"task_id": "...", "expires_at": timestamp}` | Durable upsert |
| `prune_receipts` | `{"now": timestamp}` | Remove receipts expiring at or before `now` |

Timestamps are Unix seconds. All operations share the same application namespace
and the same `persistence_route`. Filesystem receipts live in a separate directory;
a database provider can use an indexed table with expiry-based cleanup.

The example's `DemoRegistry` writes encrypted JSON through `server.storage` and
returns 404 on HTTP requests. It requires `GENRO_STORAGE_KEY`; encryption failures
do not fall back to plaintext. The Telegram application restores registrations
and renews webhooks on startup. A persistence failure prevents activation; a
webhook setup failure after saving can be retried with
`await telegram.activate_bot("alpha")`, preserving the saved token and secret.
Startup also retries activation. Startup errors retain their underlying cause;
HTTP transport errors remain sanitized because their URLs contain credentials.

## Run the example

From the repository root, configure `GENRO_STORAGE_KEY` with a stable Fernet key,
`KAJENN_TELEGRAM_WEBHOOK_URL` with the public HTTPS mount URL, and
`KAJENN_TELEGRAM_ALPHA_TOKEN` with the token created in BotFather. Optionally set
`KAJENN_TELEGRAM_BETA_TOKEN` to a **different** bot's token for a second instance.
Keep the encryption key for subsequent restarts.

```bash
kajenn serve examples/telegram_bot/config.py
```

Forward the public HTTPS URL to the server. Open the bot in Telegram and send
`/hello` or `/echo some text`. Alpha replies with its `alpha` dataset label;
beta uses `beta`. The dataset here is just a configurable label, not a database.

## Delivery boundaries

Verified commands are written to `kajenn.tasks` before the webhook returns 200.
The handler and Telegram reply run independently in the task manager. Duplicate
update IDs are scoped to the application and bot. Separate durable receipts suppress
duplicates for **48 hours from acceptance**, even if a completed task is purged or
the application restarts. Duplicates do not extend the expiry. The window exceeds
[Telegram's 24-hour update retention](https://core.telegram.org/bots/api#getting-updates).
Expired receipts are removed on startup and before each command is accepted;
an idle application therefore cleans them at its next startup or command.

Completed task history can be purged without losing deduplication during that
window. A replay after the receipt expires and the task is purged can execute
again. The task is stored before the receipt: if receipt persistence fails, the
webhook returns an error and the retry reuses the existing task. Task storage and
receipt storage are separate writes, not a distributed transaction; keep the
existing task until a failed receipt write has recovered.

This is a single-process prototype using the task spool's existing lifecycle.
It does not promise exactly-once replies or automatic recovery of tasks interrupted
by a crash. Outbound messages are plain text, 1–4096 characters; oversized replies
fail the task. Automatic retries, splitting, rate-limit queues, typing indicators,
unregistration, account linking, approval flows, media, polls and conversations
are outside this first example. Failures remain visible in the task spool.
