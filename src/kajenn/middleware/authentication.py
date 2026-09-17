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

"""Authentication middleware — publishes the request identity on the scope.

Delegates the whole verdict to ``server.authenticate(scope)`` (the §5.5
precedence living on ``AuthMixin``) and stores the result on ``scope["auth"]``
for downstream handlers. A present-but-invalid credential raises
``HTTPUnauthorized`` from the auth core, caught outermost by ``ErrorMiddleware``
(order 100) and turned into a 401. Armed by ``AuthMixin``; order 450 (INSIDE
``SessionMiddleware`` at 400, so ``scope["session"]`` is already attached and
the §5.5 session fallback is live), default OFF. The chain only carries
``http`` scopes, so no scope filtering happens here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseMiddleware

if TYPE_CHECKING:
    from ..types import Receive, Scope, Send

__all__ = ["AuthMiddleware"]


class AuthMiddleware(BaseMiddleware):
    """Sets ``scope["auth"]`` from the server's identity resolution."""

    middleware_order = 450
    middleware_default = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Resolve the identity via the server and publish it on the scope."""
        scope["auth"] = self.server.authenticate(scope)
        await self.app(scope, receive, send)
