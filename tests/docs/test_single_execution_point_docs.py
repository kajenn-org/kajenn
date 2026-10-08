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

"""The specification and the guides state the single execution point."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_the_specification_ratifies_the_execution_point() -> None:
    # wf:contract: SPECIFICATION.md carries exactly one "Ratified 2026-10-08" block naming
    # wf:contract: RoutedApplication.execute, server.channels and the retired AuthMiddleware.
    spec = read("SPECIFICATION.md")
    assert spec.count("Ratified 2026-10-08") == 1
    block = spec[spec.index("Ratified 2026-10-08"):]
    for word in ("execute", "channels", "AuthMiddleware", "kajenn.channel"):
        assert word in block


def test_the_authentication_guide_documents_the_flow() -> None:
    # wf:contract: docs/guides/authentication.md documents server.channels(), the two
    # wf:contract: _server routes, cache_ttl and the async server.authenticate.
    guide = read("docs/guides/authentication.md")
    for word in (
        "channels()",
        "/_server/auth/authenticate",
        "/_server/auth/forget_credential",
        "cache_ttl",
        "async",
    ):
        assert word in guide
    assert "AuthMiddleware" not in guide


def test_the_bus_protocol_says_who_reads_auth() -> None:
    # wf:contract: kbus-protocol.md says `auth` is read by the execution point and
    # wf:contract: `credential` is reserved and still unread.
    protocol = read("docs/design/kbus-protocol.md")
    assert "execution point" in protocol
    assert "credential" in protocol
