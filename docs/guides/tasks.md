# Background Tasks

> **Status:** Draft; implementation checked against the development source on 2026-09-08.

## What it does

Runs work outside the request/response cycle: a **spool** of queued jobs, an
**executor** that runs them, and a **scheduler** for interval and cron jobs. Tasks
are managed over HTTP under `/_server/tasks`.

## When to use it

When an operation should not block the caller (send an email, crunch a report) or
must run on a schedule (nightly cleanup, periodic sync). The backbone is enabled by default on `AsgiServer`; drive it from code or
authorized management requests.

## Setup

Tasks are a mixin capability of `AsgiServer`, enabled by default and configured
with the `tasks` kwarg:

```python
from kajenn import AsgiServer, RoutedApplication
from genro_routes import route


class App(RoutedApplication):
    mount = ""

    @route()
    def sum_sync(self, a: int = 0, b: int = 0) -> dict:
        return {"result": a + b}


server = AsgiServer(applications=[App], tasks=True)
server.serve(host="127.0.0.1", port=8000)
```

`tasks` accepts:

- `True` (default) / `False` — enable or disable.
- a dict `{"enabled": ..., "tick_seconds": ..., "mount": ...}` for finer control.

Once armed, `server.tasks` (lazily provisioned) exposes the backbone:
`.spool`, `.executor`, `.hub`, `.scheduler`, `.task_store`, `.worker_id`.

## Fire-and-forget

Queue a one-off job by creating a descriptor and handing it to the spool:

```python
from uuid import uuid4
from kajenn.tasks import new_descriptor

task_id = uuid4().hex
d = new_descriptor(task_id, owner="alice", mount="", node_path="sum_sync")
server.tasks.spool.create(d, {"a": 2, "b": 3})
```

- `new_descriptor(...)` builds the task descriptor: who owns it (`owner`), which
  mount it targets (`mount`), and which route to run (`node_path`).
- `spool.create(descriptor, params)` enqueues it with its call parameters.

The `tasks` symbols (`TaskManager`, `new_descriptor`, and the rest) import from
`kajenn.tasks`, not the top level.

## Scheduling

Register a route as a scheduled task directly on the decorator:

```python
@route(task="cleanup", task_every="1s")
def cleanup(self) -> dict:
    return {"cleaned": True}
```

- `task="cleanup"` — the task code.
- `task_every="1s"` — run on an interval. Use `task_cron=...` instead for a cron
  expression.

The scheduler drives them; from async code you can `await scheduler.tick()` to advance it
and `scheduler.run_now(code)` to trigger a scheduled task immediately.

## Managing tasks over HTTP

The `_server` app exposes the task backbone under `/_server/tasks/...`. These
endpoints require `SUPERADMIN` (and the server management gate); anonymous curl
requests receive 401. Authenticate with an appropriately authorized credential:


- schedule side: `list`, `create`, `enable`, `disable`, `run_now`, `logs`.
- spool side: `spool_list`, `progress`, `cancel`, `result`.

## How to verify it

```console
$ curl http://127.0.0.1:8000/_server/tasks/list
```

Queue a fire-and-forget job from code (as above), then poll its progress and
result:

```console
$ curl http://127.0.0.1:8000/_server/tasks/spool_list
$ curl "http://127.0.0.1:8000/_server/tasks/progress?..."
$ curl "http://127.0.0.1:8000/_server/tasks/result?..."
```

## Gotchas

- The task symbols come from `kajenn.tasks` — `from kajenn.tasks import
  new_descriptor`, not from the package top level.
- `server.tasks` is lazy and enabled by default. Access with `tasks=False` raises
  `RuntimeError`; it does not silently return an inactive manager.
- Interval vs cron is `task_every=...` **or** `task_cron=...` on `@route`, not
  both.
- The MCP push stream (`GET /mcp`) depends on the task backbone — it is `405`
  without it. See the [MCP guide](mcp.md).
