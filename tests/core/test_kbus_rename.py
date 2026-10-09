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

"""Contract: the internal lane is the KajennBus (package ``kajenn.kbus``); the old
``kajenn.channel`` name is gone with no alias, and the wire bytes are unchanged."""

import importlib

import pytest

import kajenn


def test_kbus_package_exports_the_renamed_names():
    kbus = importlib.import_module("kajenn.kbus")
    for name in (
        "KBusClient",
        "KBusHub",
        "KBusMember",
        "KBusCallError",
        "KBusConnector",
        "LocalKBus",
        "LocalFrameStream",
        "Frame",
        "FrameStream",
        "CALL_METHOD",
        "EVENT_METHOD",
        "REPLY_METHOD",
        "MAX_FRAME_SIZE",
        "REGISTER_METHOD",
        "REGISTER_PATH",
    ):
        assert name in kbus.__all__
        assert hasattr(kbus, name)


def test_old_channel_package_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("kajenn.channel")


def test_old_class_names_are_gone():
    kbus = importlib.import_module("kajenn.kbus")
    for name in ("ChannelClient", "ChannelHub", "ChannelMember", "ChannelCallError", "LocalChannel"):
        assert not hasattr(kbus, name)
        assert not hasattr(kajenn, name)


def test_root_exports_the_kbus_client():
    assert kajenn.KBusClient is importlib.import_module("kajenn.kbus").KBusClient
    assert "KBusClient" in kajenn.__all__


def test_wire_bytes_unchanged():
    frame = importlib.import_module("kajenn.kbus.frame")
    assert frame.KAJENNBUS_MAGIC == b"KJNF"
    assert frame.KAJENNBUS_VERSION == 1
    assert not hasattr(frame, "CHANNEL_MAGIC")
    assert not hasattr(frame, "CHANNEL_VERSION")


def test_control_payload_moved():
    control = importlib.import_module("kajenn.kbus.control")
    assert hasattr(control, "ControlPayload")


