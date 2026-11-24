# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""SPA visit metadata originates in the server process that owns the session."""

from __future__ import annotations

from secrets import token_urlsafe
from typing import TYPE_CHECKING, Any, TypedDict, cast

from genro_tytx import to_tytx

from .exceptions import HTTPForbidden
from .middleware.base import headers_dict
from .middleware.session import SessionMiddleware
from .wsx import WsxEnvelope
from .wsx_payload import SerializedWsxPayload


if TYPE_CHECKING:
    from .asgi_server import AsgiServer
    from .server import BaseServer
    from .types import Scope


class SpaVisit(TypedDict):
    cid: str
    identity: str | None
    login: bool
    ttl: int


async def prepare_visit(server: BaseServer, code: str, scope: Scope) -> SpaVisit | None:
    """Return the CID and session login for this app. Headers never supply either."""
    websocket = scope.get("method") == "WSK"
    if websocket:
        layer = server.get_middleware(SessionMiddleware)
        session = layer.get_session(scope) if layer is not None else None
        scope["session"] = session
        # The socket can outlive a login, logout or permission change.
        scope["auth"] = await server.authenticate(scope)
    else:
        session = scope.get("session")
    if session is None:
        return None
    key = f"kajenn_spa.{code}"
    cid = session.data[key]
    if not cid:
        if websocket:
            return None
        cid = token_urlsafe(24)
        session.data[key] = cid
        session.mark_dirty()
    root = session.avatar()
    credential = bool(headers_dict(scope).get("authorization"))
    if root is not None:
        avatar = scope.get("auth")
        if credential or avatar is None or avatar.identity != root.identity:
            # A stamped scope or a one-call credential does not vouch for the login.
            session_scope = {
                **scope,
                "headers": [
                    (k, v) for k, v in scope.get("headers", ()) if k.lower() != b"authorization"
                ],
            }
            avatar = await server.authenticate(session_scope)
        if avatar is None or avatar.identity != root.identity:
            raise HTTPForbidden("SPA session is no longer authenticated")
    return {
        "cid": cid,
        "identity": root.identity if root else None,
        "login": not credential,
        "ttl": session.meta["ttl"],
    }


async def deliver_page_message(
    server: BaseServer, code: str, cid: str, user: str, page_id: str, path: str, payload: str | None
) -> bool:
    """Deliver through the front only while the bound socket still owns this visit."""
    socket = server.websockets.get_page_socket(page_id, application=code)
    if socket is None or not socket.connected:
        return False
    layer = server.get_middleware(SessionMiddleware)
    session = layer.get_session(socket.scope) if layer else None
    if session is None or session.data[f"kajenn_spa.{code}"] != cid:
        return False
    avatar = session.avatar()
    if avatar is not None:
        auth_scope = {
            **socket.scope,
            "session": session,
            "kajenn.channel": "wsx",
            "headers": [
                (k, v) for k, v in socket.scope.get("headers", ()) if k.lower() != b"authorization"
            ],
        }
        avatar = await server.authenticate(auth_scope)
        if avatar is None:
            return False
    if user != (avatar.identity if avatar else f"guest_{cid}"):
        return False
    if (
        not socket.connected
        or server.websockets.get_page_socket(page_id, application=code) is not socket
    ):
        return False
    await socket.send_text(
        WsxEnvelope(
            method="WSK", path=path, page_id=page_id, serialized_data=SerializedWsxPayload(payload)
        ).encode()
    )
    return True


async def send_page_message(
    server: BaseServer, code: str, cid: str, user: str, page_id: str, path: str, data: Any
) -> bool:
    """Deliver in the process that owns the browser socket."""
    payload = to_tytx(data, "json") if data is not None else None
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if getattr(server, "kbus_role", None):
        return await cast("AsgiServer", server).forward_spa_message(cid, user, page_id, path, payload)
    return await deliver_page_message(server, code, cid, user, page_id, path, payload)
