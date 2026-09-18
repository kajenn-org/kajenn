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

"""Configuration package: the ``asgiconfig`` dialect and the server's read door.

A ``config.py`` defines a ``ServerConfiguration`` (subclass of
``AsgiConfigBuilder``) whose recipe declares the site sections;
``AsgiServer(config=...)`` builds its own ``ConfigurationHandler`` over that
source and reads every value through it.

The recipe is never alone: the handler layers ``BaseConfiguration`` (the
package's shipped defaults) and the defaults source the recipe itself declares
through its ``default_config`` attribute underneath it — see ``DefaultConfig``.

The configuration ALWAYS exists: a server composed in code takes the
ready-made ``DefaultConfiguration`` (named in ``CONFIGURATION_TEMPLATES``) and
writes its constructor kwargs into a ``ShortcutConfiguration`` on top of it — see
``templates``.
"""

from .builder import AsgiConfigBuilder, BaseConfiguration
from .default_config import HOME_ENV, DefaultConfig
from .elements import AsgiServerGrammar
from .handler import ConfigError, ConfigurationHandler
from .templates import (
    CONFIGURATION_TEMPLATES,
    DEFAULT_TEMPLATE,
    DefaultConfiguration,
    ShortcutConfiguration,
)

__all__ = [
    "CONFIGURATION_TEMPLATES",
    "DEFAULT_TEMPLATE",
    "HOME_ENV",
    "AsgiConfigBuilder",
    "AsgiServerGrammar",
    "BaseConfiguration",
    "ConfigError",
    "ConfigurationHandler",
    "DefaultConfig",
    "DefaultConfiguration",
    "ShortcutConfiguration",
]
