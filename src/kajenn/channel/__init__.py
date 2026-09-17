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

"""Channel subpackage: the frame protocol and the child side (◆D10).

The minimal package knows how to BE a child; the orchestration package knows
how to HAVE children — the hub imports this protocol from below, never the
reverse.
"""

from .client import ChannelClient
from .frame import MAX_FRAME_SIZE, REGISTER_METHOD, REGISTER_PATH, Frame, FrameStream
from .hub import (
    CALL_METHOD,
    EVENT_METHOD,
    REPLY_METHOD,
    ChannelCallError,
    ChannelHub,
    ChannelMember,
)
from .local import LocalChannel, LocalFrameStream

__all__ = [
    "CALL_METHOD",
    "EVENT_METHOD",
    "MAX_FRAME_SIZE",
    "REGISTER_METHOD",
    "REGISTER_PATH",
    "REPLY_METHOD",
    "ChannelCallError",
    "ChannelClient",
    "ChannelHub",
    "ChannelMember",
    "Frame",
    "FrameStream",
    "LocalChannel",
    "LocalFrameStream",
]
