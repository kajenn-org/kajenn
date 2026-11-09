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

"""SpaApplicationBase: the application an SPA application extends."""

from __future__ import annotations

from kajenn.routed_application import RoutedApplication

__all__ = ["SpaApplicationBase"]


class SpaApplicationBase(RoutedApplication):
    """The application an SPA application extends, in one process or on a pool.

    It adds nothing to ``RoutedApplication`` yet. What an SPA application in
    one process and kajenn-orchestra's ``SpaApplication`` have in common moves
    here one piece at a time.
    """
