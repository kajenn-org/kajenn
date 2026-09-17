# WebSockets: WSX and raw hosting

> **Status:** Draft; implementation checked against the development source on 2026-09-08.

The core supports two ways to serve a WebSocket: its WSX message protocol, and
an application's `serve_websocket(scope, receive, send)` raw seam. The
`websockets` backend required by uvicorn is a package dependency.

## At a glance

```{figure} ../_static/diagrams/websocket-flow.svg
:figclass: flow-diagram
:alt: Both WebSocket modes share the server-state gate. Raw applications own their protocol; WSX supplies its own handshake and routes messages.

The raw seam and WSX have different protocol owners. Neither should be confused with the ordinary HTTP middleware path.
```

## Sending a WSX request

A WSX frame is text: `WSX://` followed by JSON. Its `data` field is a **TYTX
string**, not a nested JSON object. Use `WsxEnvelope` to encode and decode:

```python
from kajenn.wsx import WsxEnvelope

message = WsxEnvelope(id="request-1", path="/greet", data={"name": "Ada"})
text = message.encode()
assert WsxEnvelope(text).data == {"name": "Ada"}
```

Against the getting-started server, a Python client can make that call.
`AsgiServer` already supplies pydantic parameter validation. Run the client in
a separate process:

```python
import asyncio
from websockets.asyncio.client import connect
from kajenn.wsx import WsxEnvelope


async def main():
    async with connect("ws://127.0.0.1:8000/") as socket:
        await socket.send(WsxEnvelope(id="request-1", path="/greet",
                                      data={"name": "Ada"}).encode())
        reply = WsxEnvelope(await socket.recv())
        assert reply.id == "request-1"
        assert reply.status == 200
        assert reply.data == {"hello": "Ada"}


asyncio.run(main())
```

Every message goes through the server's mount demux and then the application as
a synthetic HTTP scope with method `WSK`, even if its envelope named a method.
A message with an `id` is registered and answered with that id and a status.
An ordinary message without an id is an event and gets no reply. The reserved
`/_wsx/ping` is handled inline and answered outside the concurrency limit.
Non-WSX text and binary frames are logged and dropped.

## Handshake and limits

The handshake path selects a home application. An unknown home or a missing
cookie demanded by `handshake_cookie` is accepted and then closed with code 1008.
A hostile Origin is refused before accept. With no allow-list, a browser's
Origin must match the handshake Host; clients without an Origin header pass.
Configure browser origins explicitly when necessary:

```python
# Inside AsgiConfigBuilder.main:
root.configuration().server().websocket(
    origins="https://app.example.com,https://admin.example.com",
    max_concurrent=16,
)
```

Constructor form: `AsgiServer(websocket={"origins": ["https://app.example.com"],
"max_concurrent": 16})`. The default limit is 16 executing messages per
connection. It does not bound the number of pending tasks or the total memory
queued on that connection. Closing a connection waits up to five seconds for
its message tasks and cancels the remainder.

Identity is authenticated once at the handshake; the existing session is read
without creating one. The identity and session are carried into each message.
The HTTP middleware chain does not run on the handshake or per message.
WSX responses must be finite and buffered: an ASGI response chunk with
`more_body=True` is rejected and the call becomes a 500 response.

## Raw WebSockets

An application defining `serve_websocket` receives the mount-relative scope,
receive and send directly. It owns accept/close, Origin and identity validation,
its protocol, and cleanup; the WSX gate and registry are not used. The server's
not-running admission gate still applies before delegation. See the adapter in
[Mounting applications](applications.md).

## SPA page channels and push

A `SpaApplication` handshake requires its `spa_connection_id` cookie. A page
first calls `/<mount>/_wsx/openchannel` (omit the mount for a root app), carrying
`page_id` in the envelope. The application validates ownership and records the
channel; only a 200 response binds the page to that socket. A page RPC before
this step is refused with 409. Page requests follow ordinary worker placement;
a sequential page's requests serialize on that page's queue.

`await server.send_message(page_id, path, data)` writes an event to a bound page
and returns a boolean: `True` means written to the socket, not executed by the
browser. Worker code can call `worker.send_message(...)`, which forwards through
the commander. Datachanges and database events are not automatically converted
to push notifications by this API. The worker side is documented in
`kajenn-orchestra`.
