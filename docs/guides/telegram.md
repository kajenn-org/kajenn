# Telegram bots

**Version:** 0.3 · **Last updated:** 2026-10-08 · **Status:** 🔴 DA REVISIONARE

`TelegramBotApplication` hosts independently configured `RoutingClass` bot
instances. Each instance uses a BotFather token. A receiving deployment owns a
webhook at `/<application mount>/<bot code>`; a send-only deployment has no webhook.
Multiple instances can use the same class.
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

With `webhook_url`, the app receives updates as well as sending messages.
The URL is the externally reachable HTTPS URL of the application's mount,
including any reverse-proxy prefix. The single persistence route applies to
every bot. It is an internal route reference, not a URL fetched over HTTP.

## Send locally while receiving centrally

Use the same application class on the local server, **omitting `webhook_url`**:

```python
app = cfg.applications().application(
    code="telegram", app_class=TelegramBotApplication,
)
app.telegram(persistence_route="registry/bots")
```

Register the central bot's token in the local application's registry, using the
same bot class if desired. The local application can then send directly:

```python
telegram = server.applications["telegram"]
await telegram.send_message("alpha", mario_chat_id, "You have a new PR")
```

Mario must already have started a private chat with that bot. The local service
needs the actual bot token and Mario's Telegram chat ID. The central server is
not involved in the outgoing request; replies are delivered to its webhook.

Registration, startup, `activate_bot` and shutdown on a send-only application
never call `setWebhook`, `deleteWebhook` or polling methods. Inbound HTTP returns
404 and task-based command delivery is disabled. No task manager or public HTTPS
endpoint is needed locally. Omission or `None` selects send-only mode; an empty
or invalid URL is an error, never an implicit mode change.

Mode belongs to the deployment configuration, not to the persisted bot record.
Removing the URL does not delete an existing Telegram webhook: keep the central
receiver running when adding a local sender. Tokens grant bot-wide access, so
provision them only to trusted services. Use a separate local registry namespace
or backend for the local installation.

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

The API validates the class-owned grammar, checks the token with `getMe` and saves
the registration. A receiving app then calls `setWebhook` with a generated secret;
a send-only app does not. Duplicate codes or tokens within the same application
are rejected. Separate receiving and sending installations can use the same token.
`name` and `icon` are local metadata: they do not change
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
Commands addressed to another bot are ignored. Other text can continue a conversation.
Routing's auth plugin remains active: Telegram webhook verification authenticates
the delivery only. There is no sender-to-avatar resolver in this small example,
so protected commands cannot be called by Telegram senders.

## Optional administrator admission

Bot grammars can inherit the common instance grammar and add their own elements:

```python
from kajenn.applications.telegram import TelegramBotInstanceGrammar

class MyBotGrammar(TelegramBotInstanceGrammar):
    # Add bot-specific @element methods here.
    pass
```

The example's `DemoBotGrammar` already inherits it. Enable admission per instance:

```python
await telegram.register_bot(
    code="team", bot_class=DemoBot, token=token_from_secret_store,
    config={
        "settings": {"dataset": "team"},
        "access": {
            "approval_required": True,
            "admins": [123456, 654321],
            "approval_policy": "first",
        },
    },
)
```

`admins` contains positive Telegram **user IDs**, not usernames. Each admin must
have opened a private chat with this bot so the bot can deliver the request.
Admission is disabled by default. With admission enabled, an unapproved user's
first private message (usually `/start`) creates a durable request; commands are
withheld until approval. Requests from a group direct the user to the private
chat. Repeated messages reuse the existing request. Admins are admitted implicitly.

With `first`, the first valid decision **processed** closes the request. With
`all`, every admin must approve; any first rejection closes it. Each admin has one
immutable vote. A rejected request stays rejected; another `/start` does not reset
it. Administration of existing membership is outside this example.

The bot sends each admin Approve/Reject buttons. A callback must come from the
configured admin and match their stored chat/message. Decisions persist before
outgoing changes. On conclusion every admin copy is edited to show
`Approved by <name> (<id>)` or `Rejected by <name> (<id>)`, with an empty keyboard.
The requester receives the outcome. Copies are retained as a visible record.

A failed notification or edit is logged and stays pending for startup recovery;
repeated requests retry missing admin notifications, and another valid callback
retries outstanding resolution updates. It cannot change a concluded decision,
even if an old keyboard remains visible. There is no background retry timer.
Admission is separate from router authorization: protected routes remain closed.

## Concurrent conversations with multiple participants

Create conversations on the central receiver, whose registry owns their state.
A local service with a separate registry can send ordinary notifications directly;
it must ask the central application to create a conversation requiring correlation.

```python
conversation = await telegram.create_conversation(
    "team",
    participants=[
        {"user_id": mario_id, "chat_id": mario_id, "role": "requester"},
        {"user_id": reviewer_id, "chat_id": reviewer_id, "role": "reviewer"},
    ],
    route="conversation",
    context={"pr": 39},
)
await telegram.send_conversation_message(
    "team", conversation["id"], mario_id, "Please review PR 39",
    buttons={"Accept": "accept", "Reject": "reject"},
)
```

Every conversation has independent context, participants, outgoing message
references, a revision and state. A user can participate in several conversations.
A participant is an explicit user/chat pair; `role` is application metadata and
never a router authorization role. Messages go only to the selected recipient,
not to everyone in the conversation. Specify `chat_id` when a user participates
through more than one chat.

The bot route receives `text`, `sender` (Telegram user data), `conversation`
(a snapshot), and `action` (the button action, or an empty string for a text reply).
It returns text to the same participant or `None`. `DemoBot.conversation` provides
a small PR example. Command routes retain their existing `text` signature.
Handlers that close a conversation should return `None` and send any final text
before closing it.

Replies match the stored message, chat and participant. A callback additionally
matches an action offered by that message. Text without a reply reference selects
a conversation only when exactly one open conversation matches. When several
match, the bot asks the user to reply to the relevant message. No text is broadcast
or silently associated with the most recent request.

Use `get_conversation` to read a fresh snapshot. Use
`update_conversation_context(bot_code, id, context, revision=snapshot["revision"])`
to replace its context; stale revisions fail rather than overwrite newer work.
`close_conversation(bot_code, id)` concludes it; `state="cancelled"` cancels it.
An optional Unix timestamp `expires_at` marks the conversation expired on its next
read or interaction. Expiry does not schedule a reminder. Closed, cancelled and
expired conversations no longer dispatch responses or callbacks. Admission
records cannot be closed or have their context changed through these general APIs.

## Persistence route contract

The configured route accepts these keyword arguments:

| Argument | Meaning |
|---|---|
| `operation` | Registration, receipt or conversation operation described below |
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

Send-only registration and plain sending use only `list` and `save`; they neither
read nor prune update receipts. Conversation APIs additionally require the
conversation operations below.

Conversation storage also uses the application-wide route:

| Operation | Payload | Result |
|---|---|---|
| `list_conversations` | `{"bot_code": "..."}` | All conversation and admission records for this bot |
| `get_conversation` | `{"bot_code": "...", "id": "..."}` | One record, or `None` |
| `save_conversation` | Full record including `revision` | Saved record with incremented revision |

A save **must atomically compare the supplied revision** against stored state,
write only on a match, increment the revision and return the resulting record.
Creation supplies revision zero. Conflicts must raise; they must not silently
replace newer state. Namespace records by application, bot and conversation ID.
The example serializes access within one process and encrypts conversation files
under `conversations/<bot>/<id>.json`. A database adapter can implement the same
contract with a conditional update or transaction. Use one receiving process per
registry with the filesystem example; its lock is not a cross-process lock.

Conversation and admission records persist until the hosting provider removes
them. Removing approved admission records removes that user's remembered access;
removing message references loses correlation for outstanding replies. Define
retention in the hosting application rather than purging active records blindly.
Telegram sends/edits and persistence writes are separate operations: a crash
between them can leave duplicate notifications or an untracked sent message.

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

For the local installation use `examples/telegram_bot/local_config.py` instead.
It reuses the same bot class and encrypted registry example, but omits the webhook
URL even if `KAJENN_TELEGRAM_WEBHOOK_URL` exists in the environment. Configure the
same bot token and a local storage encryption key. Run it from a separate checkout
or configure separate storage mounts so both installations keep their own registry.

```bash
kajenn serve examples/telegram_bot/local_config.py
```

The local example registers senders; it sends no notification automatically.
Call `send_message` from the local application's job or handler when an event occurs.

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
unregistration, application account linking, media and polls are outside this
example. Admission grants access to this bot; it does not create application users,
tokens or router permissions. Failures remain visible in the task spool.
