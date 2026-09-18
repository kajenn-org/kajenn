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

## Run a chunked response and an event stream

Prerequisite: [request handlers](requests.md). Save this complete example as
`streams.py` and run `python streams.py`:

```python
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.streaming import StreamingResponse
from kajenn.sse import SseStream


class Streams(RoutedApplication):
    mount = ""

    async def chunks(self):
        yield b"one"
        yield b"two"

    async def events(self):
        yield {"data": "a"}
        yield {"data": "b"}

    @route()
    async def download(self):
        return StreamingResponse(self.chunks(), media_type="text/plain")

    @route()
    async def updates(self):
        return SseStream(self.events(), retry_ms=5000).response()


if __name__ == "__main__":
    AsgiServer(applications=[Streams]).serve(host="127.0.0.1", port=8000)
```

In another terminal:

```bash
curl -N http://127.0.0.1:8000/download
curl -N http://127.0.0.1:8000/updates
```

The download returns `onetwo`; the event stream includes `data: a` and `data: b`
frames separated by blank lines, plus a 5000-millisecond reconnection hint.
These finite examples finish themselves. Stop the server with Ctrl-C.

The `-N` option disables curl's buffering. Network packets need not align with
generator yields. A browser's EventSource understands SSE framing; a plain byte
stream does not add that framing. Return `SseStream.response()`, not the stream
object itself.

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

```{admonition} In revisione
:class: warning

The finite HTTP examples are verified. A reproducible walkthrough of an endless
stream, client disconnect and bounded shutdown remains to be completed; see the
lifecycle guide for the implemented shutdown contract.
```
