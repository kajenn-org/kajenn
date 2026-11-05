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

"""The hidden-path rule and the well-known pattern.

A path whose first segment starts with ``HIDDEN_SEGMENT_PREFIX`` is hidden or
of service: nothing of the site lives there, and the server answers 404. A
path matching ``WELL_KNOWN_PATTERN`` — ``/.well-known/...`` (RFC 8615),
``/robots.txt``, ``/favicon.ico`` or one of the health probes ``/healthz``,
``/livez``, ``/readyz`` (#105) — is a well-known path: it is answered
only when an application declares it in its class dict ``well_known``
(public path → route), and otherwise answers 404 "unimplemented well-known
URL", whatever the root application is:

    class Site(RoutedApplication):
        well_known = {"/robots.txt": "robots"}

The pattern is fixed, not configurable.
"""

from __future__ import annotations

import re

__all__ = ["HIDDEN_SEGMENT_PREFIX", "WELL_KNOWN_PATTERN", "as_list"]

HIDDEN_SEGMENT_PREFIX = "."
"""What a first path segment starts with to be hidden."""

WELL_KNOWN_PATTERN = re.compile(
    r"^/(\.well-known/.+|robots\.txt|favicon\.ico|healthz|livez|readyz)$"
)
"""The public paths an application may declare in ``well_known``."""


def as_list(value: str | list | None) -> list:
    """A recipe value written as a string or a list of strings, as a list (``None`` → empty).

    The words of the well-known documents (``security_txt``, ``robots``,
    ``ios``, ``android``) take one value or several in the same attribute.
    """
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)
