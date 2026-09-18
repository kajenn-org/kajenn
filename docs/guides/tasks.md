# Background Tasks

## What it does

Runs work outside the request/response cycle: a **spool** of queued jobs, an
**executor** that runs them, and a **scheduler** for interval and cron jobs. A
server that declares `ServerApplication` also manages them over HTTP under
`/_server/tasks`.

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

The scheduler drives them from its own tick loop; from async code you can
`await server.tasks.scheduler.tick()` to advance it by hand and
`server.tasks.scheduler.run_now(code)` to queue one immediately. Declaring both
`task_every` and `task_cron` on one route is logged as an error and the route is
skipped.

## Managing tasks over HTTP

`ServerApplication` (from `kajenn_server_app`) exposes the task backbone under
`/_server/tasks/...` — but only on a server that declares it in `applications=`
or on the `applications` section. Every one of these endpoints carries
`auth_rule="SUPERADMIN"`, so an anonymous request receives 401 and an identity
without that tag receives 403. Authenticate with an appropriately authorized
credential:


- schedule side: `list`, `create`, `enable`, `disable`, `run_now`, `logs`.
- spool side: `spool_list`, `progress`, `cancel`, `result`.

## How to verify it

From code, the backbone answers without any HTTP at all:

```python
assert server.tasks.worker_id == "local"
assert server.tasks.spool is not None
```

Over HTTP, with `ServerApplication` declared and a credential carrying the
`SUPERADMIN` tag:

```console
$ curl -u admin:… http://127.0.0.1:8000/_server/tasks/list
$ curl -u admin:… http://127.0.0.1:8000/_server/tasks/spool_list
$ curl -u admin:… "http://127.0.0.1:8000/_server/tasks/progress?task_id=..."
$ curl -u admin:… "http://127.0.0.1:8000/_server/tasks/result?task_id=..."
```

Without the credential those paths answer 401; without the application, 404.

## Gotchas

- The task symbols come from `kajenn.tasks` — `from kajenn.tasks import
  new_descriptor`, not from the package top level.
- `server.tasks` is built on first access and enabled by default. Reading it
  with `tasks=False` raises `RuntimeError`; it does not silently return an
  inactive manager.
- Interval vs cron is `task_every=...` **or** `task_cron=...` on `@route`, not
  both.
- The MCP push stream (`GET /mcp`) depends on the task backbone — it is `405`
  without it. See the [MCP guide](mcp.md).
