# Streaming & SSE

## What it does

Sends a response body incrementally instead of all at once. `StreamingResponse`
streams arbitrary chunks; `SseStream` streams Server-Sent Events with the correct
SSE framing and headers.

## When to use it

When the body is large, generated lazily, or open-ended: a file too big to buffer,
a progress feed, live updates pushed to the browser. Use `StreamingResponse` for
raw chunks and `SseStream` for an event stream a browser's `EventSource` can
consume.

## Setup

Both types import from submodules — they are **not** re-exported from the package
top level:

```python
from kajenn.streaming import StreamingResponse
from kajenn.sse import SseStream
```

## Chunked streaming

Return a `StreamingResponse` fed by an async generator that yields bytes:

```python
from kajenn.streaming import StreamingResponse


async def chunks():
    yield b"one"
    yield b"two"


resp = StreamingResponse(chunks(), media_type="text/plain")
```

Each yielded chunk is sent as it is produced, so the client starts receiving
before the generator finishes.

## Server-Sent Events

Wrap an async source of events in an `SseStream` and turn it into a response:

```python
from kajenn.sse import SseStream


async def source(*evts):
    for e in evts:
        yield e


stream = SseStream(source({"data": "a"}, {"data": "b"}), retry_ms=5000)
resp = stream.response()   # a StreamingResponse with the SSE headers set
```

- The source yields event dicts (`{"data": ...}`).
- `retry_ms` sets the client's reconnection hint.
- `.response()` returns a `StreamingResponse` already carrying the SSE headers, so
  you return it like any other response.

## How to verify it

For chunked output:

```console
$ curl -N http://127.0.0.1:8000/<your-route>
onetwo
```

For SSE, `curl -N` shows the event frames as they arrive:

```console
$ curl -N http://127.0.0.1:8000/<your-sse-route>
data: a

data: b
```

The `-N` flag disables curl's buffering so you see chunks as they come.

## Gotchas

- Import from the submodules: `from kajenn.streaming import
  StreamingResponse` and `from kajenn.sse import SseStream`. Neither is
  available as `from kajenn import ...`.
- `SseStream` produces a `StreamingResponse` via `.response()` — that is what you
  return; do not return the `SseStream` itself.
- The MCP push stream (`GET /mcp`) is built on this same SSE machinery; it depends
  on the task backbone being armed (see [tasks](tasks.md) and [MCP](mcp.md)).

## Where buffering still applies

The examples above describe a `StreamingResponse` returned directly by a core
`RoutedApplication`. The request body has already been fully read before that
handler runs; streaming the response does not stream an upload.

An application hosted in another process through `RemoteApplication` buffers the
complete request and the complete response: `BufferedAsgiEndpoint` refuses a
chunked or event-stream answer rather than buffering it, so that seam delivers
no incremental chunks and no endless SSE. [WSX](websockets.md) likewise requires
a finite response and rejects one with `more_body=True`.

The server's default `shutdown_timeout_seconds=5.0` limits uvicorn's wait for
open streams during shutdown before cancellation. Application shutdown hooks
then run; this is not a five-second bound on the entire shutdown sequence.
A stream that should not wait to be cancelled reads its source through
`await server.get_until_leaving(queue)`, which answers `None` as soon as the
server starts leaving, and ends on it. See [Lifecycle](lifecycle.md).
