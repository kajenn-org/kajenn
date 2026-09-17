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

"""Throwaway ASGI app — a Phase 0 test fixture, deliberately NOT part of src/.

A minimal ``BaseApplication`` subclass with hand-rolled path dispatch and three
routes — one sync, one async, one that raises — used to exercise the base
server's demux (D3), the empty websocket (D7), and, in later phases, the pool
and the request registry. The real application classes arrive in later macros;
this one lives in ``tests/`` on purpose.
"""

from __future__ import annotations

from kajenn import BaseApplication
from kajenn.types import Receive, Scope, Send


class ThrowawayApp(BaseApplication):
    """Test app echoing ``name:path``, with sync/async/raising routes.

    Constructor kwarg peeled here (cooperative chain): ``name`` — identifies
    which app served a request (primary vs. a mount) in assertions.
    """

    def __init__(self, **kwargs: object) -> None:
        self.name: str = kwargs.pop("name", "primary")
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        if path == "/boom":
            raise RuntimeError("boom")
        if path == "/sync":
            body = await self.server.run_sync(self.sync_route)
        elif path == "/sync-current":
            body = await self.server.run_sync(self.sync_current_route)
        elif path == "/async":
            body = await self.async_route()
        else:
            body = f"{self.name}:{path}"
        await self.respond(send, 200, body)

    def sync_route(self) -> str:
        """Sync handler: dispatched onto the server's thread pool via run_sync."""
        return f"sync:{self.name}"

    def sync_current_route(self) -> str:
        """Sync handler observing the registry's current request off the loop."""
        item = self.server.requests.current
        return f"current:{item.path if item is not None else None}"

    async def async_route(self) -> str:
        """Async handler: stays on the event loop."""
        return f"async:{self.name}"

    async def respond(self, send: Send, status: int, text: str) -> None:
        """Send a minimal ``text/plain`` ASGI response."""
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": text.encode()})
