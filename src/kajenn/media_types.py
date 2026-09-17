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

"""The map from a TYTX transport name to its media type.

``TRANSPORT_MIME`` goes from the transport (``json``/``xml``/``msgpack``) to the
media type that names it on the wire. ``Response`` reads it to stamp the
content-type of a TYTX reply; ``Request`` needs no map — it resolves the inbound
transport by substring on the content-type.
"""

from __future__ import annotations

TRANSPORT_MIME: dict[str, str] = {
    "json": "application/vnd.tytx+json",
    "xml": "application/vnd.tytx+xml",
    "msgpack": "application/vnd.tytx+msgpack",
}
__all__ = ["TRANSPORT_MIME"]
