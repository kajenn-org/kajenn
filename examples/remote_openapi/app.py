"""The application served by ``kajenn.remote_runner`` in this example."""

import asyncio
import json
import os
from typing import Any

from genro_routes import route

from kajenn import HTTPBadRequest, OpenApiApplication


class RemoteDemoApplication(OpenApiApplication):
    """Small diagnostic API proving what survives a remote HTTP boundary."""

    code = "demo"
    mount = "demo"
    openapi_info = {
        "title": "Remote OpenAPI demo",
        "version": "1.0.0",
        "description": "An OpenApiApplication running in a separate process.",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The standalone runner deliberately owns only a BaseServer, so this
        # example carries the signature plugin its OpenAPI and `_request`
        # injection need instead of relying on frontend plugin configuration.
        self.route.plug("pydantic")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("path") == "/echo":
            chunks = []
            while True:
                message = await receive()
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/octet-stream"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await super().__call__(scope, receive, send)

    @route()
    def hello(self) -> dict[str, Any]:
        """Return a greeting and the PID of the process serving the route."""
        return {"hello": "remote", "pid": os.getpid()}

    @route()
    def inspect(self, _request=None, **_query: Any) -> dict[str, Any]:
        """Return raw query and ordered headers, plus parsed query and cookies."""
        scope = _request.scope
        return {
            "query_string": scope.get("query_string", b"").decode("latin-1"),
            "query": _request.query,
            "headers": [
                [name.decode("latin-1"), value.decode("latin-1")]
                for name, value in scope.get("headers", [])
            ],
            "cookies": _request.cookies,
        }

    @route()
    async def delay(self, seconds: float = 0.05) -> dict[str, Any]:
        """Wait for a bounded interval so callers can observe concurrency."""
        await asyncio.sleep(seconds)
        return {"waited": seconds, "pid": os.getpid()}

    @route()
    def fail(self) -> None:
        """Raise an intentional HTTP error."""
        raise HTTPBadRequest("intentional remote error")

    @route(auth_rule="admin")
    def protected(self, _request=None) -> dict[str, Any]:
        """Return only for an authenticated identity carrying the admin tag."""
        avatar = _request.avatar()
        return {"identity": avatar.identity, "tags": list(avatar.tags), "pid": os.getpid()}


def create_application() -> RemoteDemoApplication:
    """Factory used by owned and connect-only runner examples."""
    return RemoteDemoApplication(code="demo", mount="demo")


def describe_response(response: dict[str, Any]) -> str:
    """Convenience for experimenting with returned JSON bodies."""
    return json.dumps(response, indent=2, sort_keys=True)
