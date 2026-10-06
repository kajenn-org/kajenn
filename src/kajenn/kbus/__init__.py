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

``Frame``/``FrameStream`` are the wire. ``KajennBusClient`` is the child end
over a socket, ``LocalKajennBus`` the in-process one, ``KajennBusHub`` the parent
end that binds the socket and keeps the rubric of registered members. The far
end of the KajennBus may live in another process; nothing here reaches up to
whoever spawns it.
"""

from .client import KajennBusClient
from .frame import MAX_FRAME_SIZE, REGISTER_METHOD, REGISTER_PATH, Frame, FrameStream
from .hub import (
    CALL_METHOD,
    EVENT_METHOD,
    REPLY_METHOD,
    KajennBusCallError,
    KajennBusHub,
    KajennBusMember,
)
from .local import LocalKajennBus, LocalFrameStream

__all__ = [
    "CALL_METHOD",
    "EVENT_METHOD",
    "MAX_FRAME_SIZE",
    "REGISTER_METHOD",
    "REGISTER_PATH",
    "REPLY_METHOD",
    "KajennBusCallError",
    "KajennBusClient",
    "KajennBusHub",
    "KajennBusMember",
    "Frame",
    "FrameStream",
    "LocalKajennBus",
    "LocalFrameStream",
]
