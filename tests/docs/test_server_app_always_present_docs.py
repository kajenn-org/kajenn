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

"""The specification and the guides state that ``_server`` is always present."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_the_specification_ratifies_the_always_present_management_application() -> None:
    # wf:contract: SPECIFICATION.md carries exactly one block titled with "the management
    # wf:contract: application is always present", naming D33, D-SA-10, kajenn.server_app and
    # wf:contract: RemoteApplication.
    spec = read("SPECIFICATION.md")
    assert spec.count("the management application is always present") == 1
    block = spec[spec.index("the management application is always present"):]
    for word in ("D33", "D-SA-10", "kajenn.server_app", "RemoteApplication"):
        assert word in block


def test_the_guides_name_the_new_package_only() -> None:
    # wf:contract: no guide, README or specification names kajenn_server_app any more.
    for relative in (
        "README.md",
        "SPECIFICATION.md",
        "docs/guides/authentication.md",
        "docs/guides/management.md",
        "docs/guides/configuration.md",
        "docs/getting-started.md",
        "docs/concepts.md",
    ):
        assert "kajenn_server_app" not in read(relative), relative


def test_the_configuration_guide_says_customise_not_declare() -> None:
    # wf:contract: docs/guides/configuration.md documents application(code="_server", ...) as
    # wf:contract: the customisation of an application that is always there.
    guide = read("docs/guides/configuration.md")
    assert 'code="_server"' in guide
    assert "always" in guide
