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

Save this complete local example as `jobs.py`:

```python
from uuid import uuid4
from kajenn import AsgiServer, RoutedApplication
from kajenn.tasks import new_descriptor
from genro_routes import route


class Jobs(RoutedApplication):
    mount = ""

    @route()
    def sum_sync(self, a: int = 0, b: int = 0) -> dict:
        return {"result": a + b}

    @route()
    def submit(self, a: int = 2, b: int = 3) -> dict:
        task_id = uuid4().hex
        descriptor = new_descriptor(task_id, owner="demo", mount="", node_path="sum_sync")
        self.server.tasks.spool.create(descriptor, {"a": a, "b": b})
        return {"task_id": task_id}

    @route()
    def result(self, task_id: str) -> dict:
        spool = self.server.tasks.spool
        return {"task": spool.get(task_id), "result": spool.read_result(task_id)}


if __name__ == "__main__":
    server = AsgiServer(applications=[Jobs], tasks={"tick_seconds": 1})
    server.serve(host="127.0.0.1", port=8000)
```

Run `python jobs.py` from an empty writable demo directory. In another terminal:

```bash
curl -X POST 'http://127.0.0.1:8000/submit?a=2&b=3'
curl 'http://127.0.0.1:8000/result?task_id=PASTE_TASK_ID'
```

Use the returned task_id. Poll result until the descriptor says `terminated`;
the result is then `{"result":5}`. A pending job can initially have a null result.
Stop with Ctrl-C. The local demo exposes public submission/result routes; a real
service must authenticate submission and enforce ownership on result retrieval.


`tasks` accepts:

- `True` (default) / `False` — enable or disable.
- a dict `{"enabled": ..., "tick_seconds": ..., "mount": ...}` for finer control.

Once armed, `server.tasks` (lazily provisioned) exposes the backbone:
`.spool`, `.executor`, `.hub`, `.scheduler`, `.task_store`, `.worker_id`.

## Fire-and-forget

The submit handler above creates a descriptor and hands it to the spool while
the server is running. The following is the equivalent **handler fragment**, not
code to append after the blocking `serve()` call:

```python
from uuid import uuid4
from kajenn.tasks import new_descriptor

task_id = uuid4().hex
d = new_descriptor(task_id, owner="alice", mount="", node_path="sum_sync")
self.server.tasks.spool.create(d, {"a": 2, "b": 3})
```

- `new_descriptor(...)` builds the task descriptor: who owns it (`owner`), which
  mount it targets (`mount`), and which route to run (`node_path`).
- `spool.create(descriptor, params)` enqueues it with its call parameters.

The `tasks` symbols (`TaskManager`, `new_descriptor`, and the rest) import from
`kajenn.tasks`, not the top level.

## Scheduling

Insert this method inside the Jobs class, before starting the server, to
register a scheduled task:

```python
@route(task="cleanup", task_every="1s")
def cleanup(self) -> dict:
    return {"cleaned": True}
```

- `task="cleanup"` — the task code.
- `task_every="1s"` — run on an interval. Use `task_cron=...` instead for a cron
  expression.

The example sets `tick_seconds` to 1 so this short interval can be observed.
The default scheduler tick is 30 seconds: an interval marks when work becomes
due, not a guarantee of exact execution time. After a few seconds, inspect
`tasks/logs/cleanup.jsonl` in the demo directory for an `"outcome": "ok"` record.
Stop the demo with Ctrl-C.

The scheduler drives tasks from its own tick loop; from async code you can
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

The submit/result calls above verify execution, rather than only the existence
of the task manager. The assertions below are for code that already has the
server object; they are not commands to run in a separate interpreter.

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
