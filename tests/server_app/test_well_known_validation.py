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

"""The boot-time checks of the ``security_txt`` and ``robots`` words.

A missing ``expires`` and an empty ``user_agent`` stop the server; an
``expires`` already past is served with a WARNING, and one written without a
time zone is read as UTC.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from kajenn import AsgiServer
from kajenn.config import AsgiConfigBuilder


def recipe(server_words) -> type:
    class Recipe(AsgiConfigBuilder):
        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.server(host="127.0.0.1", port=0)
            server_words(cfg.applications().application(code="_server"))

    return Recipe


def test_a_missing_expires_stops_the_boot() -> None:
    def words(node: Any) -> None:
        node.security_txt(contact="mailto:security@example.com")

    with pytest.raises(ValueError, match="expires"):
        AsgiServer(config=recipe(words))


async def test_a_past_expires_without_time_zone_is_served_with_a_warning(
    http_request, response_status, response_body, caplog
) -> None:
    def words(node: Any) -> None:
        node.security_txt(contact="mailto:security@example.com", expires="2020-01-01T00:00:00")

    with caplog.at_level(logging.WARNING):
        server = AsgiServer(config=recipe(words))
    assert any(record.levelno == logging.WARNING and "2020-01-01T00:00:00" in record.getMessage()
               for record in caplog.records)
    sent = await http_request(server, "/.well-known/security.txt")
    assert response_status(sent) == 200
    assert b"Expires: 2020-01-01T00:00:00\n" in response_body(sent)


def test_an_empty_user_agent_stops_the_boot() -> None:
    def words(node: Any) -> None:
        node.robots().agent(user_agent="", disallow="/admin/")

    with pytest.raises(ValueError, match="user_agent"):
        AsgiServer(config=recipe(words))
