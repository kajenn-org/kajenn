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

"""The three names the hidden-path rule is written in (issue #88).

A path whose first segment starts with ``HIDDEN_SEGMENT_PREFIX`` is hidden or
of service: nothing of the site lives there. The one exception is the segment
``WELL_KNOWN_SEGMENT``, reserved by RFC 8615 for discovery documents, which an
application serves by declaring them as children of its ``WELL_KNOWN_ROOT``
branch — a routing class attached like any other reserved branch:

    self.route.add_branches({"name": WELL_KNOWN_ROOT, "instance": Discovery()})

The server reads those children at mount time; ``/.well-known/<name>/<rest>``
then resolves on that application as ``_well_known/<name>/<rest>``.
"""

from __future__ import annotations

__all__ = ["HIDDEN_SEGMENT_PREFIX", "WELL_KNOWN_ROOT", "WELL_KNOWN_SEGMENT"]

HIDDEN_SEGMENT_PREFIX = "."
"""What a first path segment starts with to be hidden."""

WELL_KNOWN_SEGMENT = ".well-known"
"""The one hidden first segment a site may answer on (RFC 8615)."""

WELL_KNOWN_ROOT = "_well_known"
"""The branch an application declares its discovery documents under."""
