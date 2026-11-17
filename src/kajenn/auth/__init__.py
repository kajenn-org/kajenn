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

"""Auth capability package: the server side of authentication.

``AuthMixin`` composes the capability onto a server: it asks the
authentication application mounted with code ``_auth`` (``kajenn.auth_app``)
for the identity of a presented credential and of a session, and caches the
answers. The identity is resolved by the execution point
(``RoutedApplication.execute``), which awaits ``server.authenticate(scope)``.
The package holds no store, no credential and no login surface.
"""

from __future__ import annotations

from .mixin import AuthMixin

__all__ = ["AuthMixin"]
