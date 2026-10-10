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

"""The server application — the system surface a server exposes under ``/_server``.

Everything that asks a human for a user and a password lives here: the
application (`ServerApplication` in `server_app`), the login methods
(`AuthMethod`, `PasswordMethod` in `auth_method`, `OidcMethod` in
`oidc_method`) and the sections it attaches (`AuthSection`, `UsersSection`,
`TokensSection`, `TasksSection`, `MonitorSection` in `server_sections`).

It is a package of its own, beside `kajenn` and shipped with it:
``import kajenn`` loads none of this, and this side imports the core by
absolute path. The core authenticates by header credentials and by the session
avatar (`kajenn.auth.core`, `kajenn.auth.mixin`) and knows nothing of
this package. Nothing here is mounted automatically: the application is
declared in the configuration like any other, with ``app_class`` from this
package and code ``_server``.
"""

from .auth_method import AuthMethod, PasswordMethod, safe_next_path
from .oidc_method import OidcMethod
from .server_app import ServerApplication
from .server_sections import (
    AuthSection,
    MonitorSection,
    TasksSection,
    TokensSection,
    UsersSection,
)

__all__ = [
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
]
