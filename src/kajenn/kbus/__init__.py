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

"""KajennBus subpackage: the frame protocol and both ends that speak it (◆D10).

``Frame``/``FrameStream`` are the wire. ``KBusClient`` is the child end over a
socket, ``LocalKBus`` the in-process one, ``KBusHub`` the parent end that binds
the socket and keeps the rubric of registered members. Every live link is one
``KBusConnector``, symmetric: both ends call and serve. The far
end of the KajennBus may live in another process; nothing here reaches up to
whoever spawns it.
"""

from .client import KBusCallError, KBusClient
from .connector import KBusCallCancelled, KBusCallFailed, KBusConnector
from .frame import (
    CALL_METHOD,
    EVENT_METHOD,
    MAX_FRAME_SIZE,
    REGISTER_METHOD,
    REGISTER_PATH,
    REPLY_METHOD,
    Frame,
    FrameStream,
    FrameStreamProtocol,
)
from .hub import KBusHub, KBusMember
from .local import LocalFrameStream, LocalKBus

__all__ = [
    "CALL_METHOD",
    "EVENT_METHOD",
    "MAX_FRAME_SIZE",
    "REGISTER_METHOD",
    "REGISTER_PATH",
    "REPLY_METHOD",
    "KBusCallCancelled",
    "KBusCallError",
    "KBusCallFailed",
    "KBusClient",
    "KBusConnector",
    "KBusHub",
    "KBusMember",
    "Frame",
    "FrameStream",
    "FrameStreamProtocol",
    "LocalFrameStream",
    "LocalKBus",
]
