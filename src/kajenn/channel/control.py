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
"""Explicit JSON codec for values consumed by channel control endpoints."""

from __future__ import annotations
import json
from typing import Any

__all__ = ["ControlPayload"]


class ControlPayload:
    """Serialize control values without teaching Frame about those values."""

    def reject_constant(self, value: str) -> None:
        raise ValueError(f"non-finite JSON number {value!r}")

    def object_pairs(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def encode(self, value: Any) -> bytes:
        try:
            return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(f"invalid control payload: {exc}") from exc

    def decode(self, payload: bytes) -> Any:
        try:
            return json.loads(
                payload,
                object_pairs_hook=self.object_pairs,
                parse_constant=self.reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ValueError(f"invalid control payload: {exc}") from exc
