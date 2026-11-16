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

It holds the management application (`ServerApplication` in `server_app`)
and the sections it attaches (`TasksSection`, `MonitorSection` and the others
in `server_sections`). Authentication is not here: it belongs to the
application mounted at ``/_auth`` (`kajenn.auth_app`).

It is a package of its own, beside `kajenn` and shipped with it, and imports
the core by absolute path. The server mounts the application under the code
``_server`` when the configuration does not name it; the configuration only
customises it.
"""

from .server_app import ServerApplication
from .server_sections import MonitorSection, TasksSection

__all__ = [
    "MonitorSection",
    "ServerApplication",
    "TasksSection",
]
