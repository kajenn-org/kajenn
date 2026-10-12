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

"""The specification and the guides describe the behaviour the audit corrections landed."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_the_specification_ratifies_credential_validity_across_processes() -> None:
    # wf:contract: SPECIFICATION.md carries exactly one block titled with "credential validity
    # wf:contract: and revocation across processes", naming valid_until, forget_credentials,
    # wf:contract: request_timeout and FrameStream.
    spec = read("SPECIFICATION.md")
    assert spec.count("credential validity and revocation across processes") == 1
    block = spec[spec.index("credential validity and revocation across processes"):]
    for word in ("valid_until", "forget_credentials", "request_timeout", "FrameStream"):
        assert word in block


def test_the_streaming_guide_says_the_external_seam_streams() -> None:
    # wf:contract: docs/guides/streaming.md no longer says the external seam delivers "no
    # wf:contract: endless SSE"; it names more_body as what crosses the seam chunk by chunk.
    guide = read("docs/guides/streaming.md")
    assert "no endless SSE" not in guide
    assert "more_body" in guide


def test_the_protocol_table_matches_the_code() -> None:
    # wf:contract: docs/design/kbus-protocol.md maps "a stream aborted before its first
    # wf:contract: message" to 503, not 502, and no longer describes max_calls as requests
    # wf:contract: "in flight".
    protocol = read("docs/design/kbus-protocol.md")
    assert "in flight" not in protocol
    row = next(line for line in protocol.splitlines() if "aborted before its first message" in line)
    assert "503" in row and "502" not in row
