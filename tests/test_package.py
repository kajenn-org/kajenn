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

"""Contract: the distribution installs, exposes its version, and the core never imports the server app."""

import sys

import kajenn


def test_version_is_exposed():
    assert kajenn.__version__ == "0.0.0"


def test_core_does_not_import_server_app():
    assert "kajenn_server_app" not in sys.modules
