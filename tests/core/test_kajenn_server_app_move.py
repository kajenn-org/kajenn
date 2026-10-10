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

"""The management application lives in the core: ``kajenn.server_app``.

The package ``kajenn_server_app`` is gone, no alias: the import path is the
one change a recipe sees. Three import orders must work in a fresh interpreter,
because the core now imports the package to mount it.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys

import pytest

EXPORTS = (
    "AuthMethod",
    "AuthSection",
    "MonitorSection",
    "OidcMethod",
    "PasswordMethod",
    "ServerApplication",
    "TasksSection",
    "TokensSection",
    "UsersSection",
    "safe_next_path",
)


def test_the_package_is_a_subpackage_of_the_core() -> None:
    # wf:contract: kajenn.server_app imports and exports every name the old package did.
    module = importlib.import_module("kajenn.server_app")
    for name in EXPORTS:
        assert hasattr(module, name), name
    assert module.ServerApplication.code == "_server"


def test_the_old_package_is_gone() -> None:
    # wf:contract: kajenn_server_app is not importable: no alias, no shim.
    assert importlib.util.find_spec("kajenn_server_app") is None


def test_the_sections_keep_their_module_path() -> None:
    # wf:contract: the sections stay under server_sections inside the moved package.
    for section in ("auth_section", "tokens_section", "tasks_section", "monitor_section", "users_section"):
        importlib.import_module(f"kajenn.server_app.server_sections.{section}")


@pytest.mark.parametrize("first", ["kajenn", "kajenn.server_app", "kajenn.config"])
def test_every_import_order_works_in_a_fresh_interpreter(first: str) -> None:
    # wf:contract: importing kajenn, kajenn.server_app or kajenn.config first in a fresh
    # wf:contract: interpreter succeeds: no circular import between the core and the
    # wf:contract: management package.
    code = f"import {first}; import kajenn; import kajenn.server_app; import kajenn.config"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
