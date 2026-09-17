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

"""ASGI type aliases for kajenn.

Aliases follow the ASGI spec (MutableMapping, not TypedDict, for
extensibility): ``Scope`` — connection metadata (type, method, path,
headers, ...); ``Message`` — messages between app and server (the ``type``
key identifies the message); ``Receive``/``Send`` — the two async channel
callables; ``ASGIApp`` — the standard application signature.
"""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

__all__ = ["ASGIApp", "Message", "Receive", "Scope", "Send"]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
