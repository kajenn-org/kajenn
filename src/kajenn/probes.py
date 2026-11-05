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

"""The health probes every server answers: liveness and readiness.

Two kinds, the ones an orchestrator (Kubernetes, a load balancer, a container
runtime) asks a process about:

- **liveness** — "is the process alive": 200 ``ok`` as long as the server can
  answer at all, while it is leaving included. A failing liveness probe gets
  the process restarted, so it never depends on anything but the process.
- **readiness** — "may traffic be sent here": 200 ``ready`` while the server
  is ``RUNNING`` and every application says it is ``ready``; otherwise 503
  ``not ready``, one line per reason.

The paths are ``DEFAULT_PROBES``: ``/healthz`` and ``/livez`` for liveness,
``/readyz`` for readiness. The server answers them in its own entry point,
ahead of the middleware chain and of the refusal of a server that is leaving:
no session, no cookie, no authentication, no request registered. The
``probes`` kwarg (and the ``server.probes`` recipe word) renames them or turns
them off.
"""

from __future__ import annotations

from typing import Any

from .well_known import WELL_KNOWN_PATTERN

__all__ = ["DEFAULT_PROBES", "LIVENESS", "PROBE_KINDS", "READINESS", "probe_paths"]

LIVENESS = "liveness"
"""The probe kind that answers whether the process is alive."""

READINESS = "readiness"
"""The probe kind that answers whether the server takes traffic."""

PROBE_KINDS = (LIVENESS, READINESS)
"""The probe kinds, in the order they are read."""

DEFAULT_PROBES: dict[str, tuple[str, ...]] = {
    LIVENESS: ("/healthz", "/livez"),
    READINESS: ("/readyz",),
}
"""The probe paths of a server that declares none, by kind."""


def probe_paths(declared: Any) -> dict[str, str]:
    """The probe index the server keeps: public path → kind.

    ``declared`` is what the ``probes`` kwarg received: ``None`` or ``True``
    for ``DEFAULT_PROBES``, ``False`` for no probe at all, or a dict
    ``{kind: path | [paths] | None | False}`` where a kind left out keeps its
    default and ``None``/``False``/an empty list turns that kind off. A
    string may hold several paths separated by commas (the recipe spelling).

    Raises:
        ValueError: an unknown kind, a path not starting with ``/``, a
            well-known path, or the same path given twice.
    """
    if declared is False:
        return {}
    if declared is None or declared is True:
        declared = {}
    if not isinstance(declared, dict):
        raise ValueError(f"probes: expected a dict, True or False, got {declared!r}")
    unknown = sorted(set(declared) - set(PROBE_KINDS))
    if unknown:
        raise ValueError(f"probes: unknown kind(s) {', '.join(unknown)}")
    index: dict[str, str] = {}
    for kind in PROBE_KINDS:
        value = declared.get(kind, DEFAULT_PROBES[kind])
        for path in _as_paths(value):
            if not path.startswith("/"):
                raise ValueError(f"probes: {kind} path {path!r} does not start with '/'")
            if WELL_KNOWN_PATTERN.match(path):
                raise ValueError(f"probes: {kind} path {path!r} is a well-known path")
            if path in index:
                raise ValueError(f"probes: path {path!r} given twice")
            index[path] = kind
    return index


def _as_paths(value: Any) -> list[str]:
    """A kind's value as a list of paths: off (``None``/``False``) is empty."""
    if value is None or value is False:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]
