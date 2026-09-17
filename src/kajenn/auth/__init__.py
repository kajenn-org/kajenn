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

"""Auth capability package: the credential core and the mixin.

``AuthCore`` (basic/bearer/jwt verification) and ``AuthMixin`` (the §5.5
identity precedence over sessions). ``AuthMiddleware`` — the chain entry point
armed by the mixin — lives in ``middleware/authentication.py``. The core
authenticates by header credentials and by the session avatar, and never asks
for a user and a password: the login surface — the self-describing
``AuthMethod``/``PasswordMethod``, ``OidcMethod`` and the ``safe_next_path``
open-redirect guard — belongs to the ``kajenn_server_app`` package.
"""

from __future__ import annotations

from .api_key_store import ApiKeyStore, FileApiKeyStore
from .core import AuthCore
from .mixin import AuthMixin
from .user_store import FileUserStore, UserStore

__all__ = [
    "ApiKeyStore",
    "AuthCore",
    "AuthMixin",
    "FileApiKeyStore",
    "FileUserStore",
    "UserStore",
]
