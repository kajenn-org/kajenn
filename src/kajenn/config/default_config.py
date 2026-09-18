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

"""DefaultConfig — the defaults layer a recipe declares for itself.

Three layers reach the server's read door, lowest first:

1. ``BaseConfiguration`` — the package's shipped defaults;
2. the recipe's ``default_config`` source — the deployment's own layer;
3. the site's own recipe — always last, always winning.

The middle layer is what a sysadmin owns: a mount that only exists on this
host, a key material source, a listener — set once, inherited by every site
deployed there, and overridable by any of them.

WHICH source that is, the RECIPE declares (``default_config`` on
``AsgiConfigBuilder``); this class only resolves the declaration:

- unset / ``None`` / ``True`` → the conventional ``<base_dir>/config.py``,
  layered only when the file exists;
- ``False`` → no defaults layer at all: the site sits straight on the package
  defaults;
- a path → THAT file, and a missing one is a ``ConfigError`` — an explicit
  choice the runtime cannot honour is a configuration mistake, never a silent
  skip.

``base_dir`` resolves with precedence: the explicit argument → the env var
``KAJENN_HOME`` → ``~/.kajenn``. The CLI's ``SitesRegistry`` takes its own
``base_dir`` from this class, so ONE variable relocates everything kajenn keeps
outside a deployment — containers and test runs included.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from genro_builders.builder import BuilderBase
from genro_builders.contrib.config import ConfigBuilder

from .builder import BaseConfiguration
from .handler import ConfigError

__all__ = ["HOME_ENV", "DefaultConfig"]

HOME_ENV = "KAJENN_HOME"
"""Env var relocating everything kajenn keeps outside a deployment."""

RecipeSource = str | Path | type | BuilderBase


class DefaultConfig:
    """The defaults layer a recipe declares, and the parent chain it forms."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or os.environ.get(HOME_ENV) or Path.home() / ".kajenn")

    @property
    def path(self) -> Path:
        """The conventional defaults recipe of this ``base_dir`` — it need not exist."""
        return self.base_dir / "config.py"

    def parents_for(self, source: RecipeSource) -> list[type | Path]:
        """The parent recipes of *source*'s handler, lowest layer first.

        Always the package defaults, then the layer *source* declares — the three
        ``default_config`` forms are in the module docstring.
        """
        parents: list[type | Path] = [BaseConfiguration]
        declared = self.declared_by(source)
        if declared is False:
            return parents
        if declared is None or declared is True:
            if self.path.is_file():
                parents.append(self.path)
            return parents
        path = Path(declared).expanduser()
        if not path.is_file():
            raise ConfigError(f"default_config names a file that does not exist: {path}")
        parents.append(path)
        return parents

    def declared_by(self, source: RecipeSource) -> bool | str | Path | None:
        """The ``default_config`` value *source* declares.

        A recipe class or instance answers directly; a ``config.py`` path is
        imported to reach its class first.
        """
        if isinstance(source, (str, Path)):
            source = self.recipe_class(source)
        return getattr(source, "default_config", None)

    def recipe_class(self, path: str | Path) -> type:
        """Import a ``config.py`` and return the single recipe class it defines.

        Mirrors the loader contract of
        ``genro_builders.contrib.config.handler.ConfigHandler._load_recipe_class``:
        the module is executed from its file location, never registered in
        ``sys.modules``, and must define exactly ONE ``ConfigBuilder`` subclass
        (an imported shared base recipe does not count).
        """
        path = Path(path).resolve()
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ConfigError(f"cannot import config module from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found = {
            obj
            for obj in vars(module).values()
            if isinstance(obj, type)
            and issubclass(obj, ConfigBuilder)
            and obj is not ConfigBuilder
            and obj.__module__ == module.__name__
        }
        if len(found) != 1:
            names = sorted(cls.__name__ for cls in found) or "none"
            raise ConfigError(
                f"{path} must define exactly one ConfigBuilder subclass, found: {names}"
            )
        return found.pop()
