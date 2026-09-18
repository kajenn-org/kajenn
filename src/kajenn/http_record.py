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

"""A bounded, versioned record carrying HTTP metadata and opaque body bytes.

One record is ``b"HTTP"``, a version byte, the length of the metadata and then
the metadata as ascii JSON, followed by the body bytes. The body is never
interpreted; the metadata is checked to be a JSON tree of finite numbers,
string keys and no duplicate key, so what one peer wrote is what the other
reads. ``encode_request``/``decode_request`` carry an ASGI http scope reduced
to its transportable fields; ``encode_response``/``decode_response`` carry a
status, text headers and a body.
"""

import json
import math
import struct
from collections.abc import Mapping
from typing import Any

from .transport_limits import DEFAULT_MAX_FRAME_SIZE, HttpBodyTooLarge, http_max_body_size


class HttpRecord:
    """Bounded, versioned encoding for an HTTP scope and its opaque body."""

    MAGIC = b"HTTP"
    VERSION = 1
    DEFAULT_MAX_BODY_SIZE = DEFAULT_MAX_FRAME_SIZE
    _HEADER = struct.Struct(">4sBI")
    _REQUEST_FIELDS = frozenset(
        {
            "method",
            "path",
            "raw_path",
            "root_path",
            "query_string",
            "headers",
            "scheme",
            "server",
            "client",
            "http_version",
        }
    )

    def __init__(self, max_body_size: int | None = None) -> None:
        """Build a codec bounded at ``max_body_size`` bytes of body.

        Args:
            max_body_size: the ceiling in bytes; ``None`` reads the
                process-wide policy.

        Raises:
            TypeError: ``max_body_size`` is not an integer.
            ValueError: ``max_body_size`` is negative.
        """
        max_body_size = http_max_body_size() if max_body_size is None else max_body_size
        if isinstance(max_body_size, bool) or not isinstance(max_body_size, int):
            raise TypeError("max_body_size must be an integer")
        if max_body_size < 0:
            raise ValueError("max_body_size must not be negative")
        self.max_body_size = max_body_size

    def encode(self, metadata: dict[str, Any], body: bytes) -> bytes:
        """One record: the header, the metadata as ascii JSON, then the body.

        Raises:
            TypeError: ``body`` is not bytes or ``metadata`` is not a dict.
            ValueError: the metadata is not a JSON tree of finite numbers and
                string keys, or it nests deeper than the decoder can read.
            HttpBodyTooLarge: the body passes ``max_body_size``.
        """
        self._check_body(body)
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a dictionary")
        try:
            self._check_json_value(metadata, "metadata")
        except RecursionError as exc:
            raise ValueError("HTTP metadata nesting exceeds decoder capacity") from exc
        try:
            encoded_metadata = json.dumps(
                metadata,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("metadata is not valid JSON") from exc
        return self._HEADER.pack(self.MAGIC, self.VERSION, len(encoded_metadata)) + encoded_metadata + body

    def decode(self, payload: bytes) -> tuple[dict[str, Any], bytes]:
        """The metadata and the body of one record.

        Raises:
            TypeError: ``payload`` is not bytes.
            ValueError: the header is truncated or carries another magic or
                version, the metadata is truncated, is not a JSON object, or
                carries a duplicate key or a non-finite number.
            HttpBodyTooLarge: the body passes ``max_body_size``.
        """
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) < self._HEADER.size:
            raise ValueError("truncated HTTP record header")
        magic, version, metadata_size = self._HEADER.unpack_from(payload)
        if magic != self.MAGIC:
            raise ValueError("invalid HTTP record magic")
        if version != self.VERSION:
            raise ValueError(f"unsupported HTTP record version: {version}")
        metadata_end = self._HEADER.size + metadata_size
        if metadata_end > len(payload):
            raise ValueError("truncated HTTP record metadata")
        body_size = len(payload) - metadata_end
        if body_size > self.max_body_size:
            raise HttpBodyTooLarge("HTTP body exceeds configured limit")
        encoded_metadata = payload[self._HEADER.size : metadata_end]
        try:
            metadata = json.loads(
                encoded_metadata,
                object_pairs_hook=self._object_without_duplicates,
                parse_constant=self._reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ValueError("invalid HTTP metadata JSON") from exc
        if not isinstance(metadata, dict):
            raise ValueError("HTTP metadata must be a JSON object")
        try:
            self._check_json_value(metadata, "metadata")
        except RecursionError as exc:
            raise ValueError("HTTP metadata nesting exceeds decoder capacity") from exc
        return metadata, payload[metadata_end:]

    def encode_request(self, scope: Mapping[str, Any], body: bytes) -> bytes:
        """One request record: the transportable scope fields and the body.

        Only the fields of ``_REQUEST_FIELDS`` travel; bytes fields become
        latin-1 text and ``server``/``client`` a host/port pair.

        Raises:
            TypeError: ``scope`` is not a mapping, or a field it carries is of
                the wrong type.
            ValueError: the scope declares a type other than ``http``, or a
                field holds a value the wire cannot carry.
        """
        if not isinstance(scope, Mapping):
            raise TypeError("scope must be a mapping")
        if "type" in scope and scope["type"] != "http":
            raise ValueError("request scope type must be http")
        selected = {key: scope[key] for key in self._REQUEST_FIELDS if key in scope}
        normalized = self._encode_scope(selected)
        return self.encode({"record_type": "request", "scope": normalized}, body)

    def decode_request(self, payload: bytes) -> tuple[dict[str, Any], bytes]:
        """The ASGI http scope and the body of one request record.

        The scope comes back with ``type`` set to ``http``.

        Raises:
            ValueError: the envelope is not a request envelope, its scope is
                not an object, or it names a field outside ``_REQUEST_FIELDS``.
        """
        metadata, body = self.decode(payload)
        if set(metadata) != {"record_type", "scope"} or metadata.get("record_type") != "request":
            raise ValueError("invalid request metadata envelope")
        encoded_scope = metadata.get("scope")
        if not isinstance(encoded_scope, dict):
            raise ValueError("request scope metadata must be an object")
        if not set(encoded_scope).issubset(self._REQUEST_FIELDS):
            raise ValueError("request metadata contains unknown fields")
        scope = self._decode_scope(encoded_scope)
        scope["type"] = "http"
        return scope, body

    def encode_response(self, response: dict[str, Any]) -> bytes:
        """One response record from ``{"status", "headers", "body"}``.

        Raises:
            TypeError: ``response`` is not a dict, or a header name or value
                is not a string.
            ValueError: the dict carries other keys than those three, the
                status is not an integer from 100 to 599, or a header is not a
                pair.
        """
        if not isinstance(response, dict):
            raise TypeError("response must be a dictionary")
        if set(response) != {"status", "headers", "body"}:
            raise ValueError("response must contain only status, headers and body")
        status = response["status"]
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError("response status must be an integer from 100 to 599")
        headers = self._encode_text_headers(response["headers"])
        body = response["body"]
        return self.encode(
            {"record_type": "response", "status": status, "headers": headers},
            body,
        )

    def decode_response(self, payload: bytes) -> dict[str, Any]:
        """One response record as ``{"status", "headers", "body"}``.

        Raises:
            ValueError: the envelope is not a response envelope, the status is
                not an integer from 100 to 599, or a header is not a string
                pair.
        """
        metadata, body = self.decode(payload)
        if set(metadata) != {"record_type", "status", "headers"} or metadata.get("record_type") != "response":
            raise ValueError("invalid response metadata envelope")
        status = metadata.get("status")
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError("response status must be an integer from 100 to 599")
        headers = self._decode_text_headers(metadata.get("headers"))
        return {"status": status, "headers": headers, "body": body}

    def _check_body(self, body: bytes) -> None:
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        if len(body) > self.max_body_size:
            raise HttpBodyTooLarge("HTTP body exceeds configured limit")

    def _check_json_value(self, value: Any, path: str) -> None:
        if value is None or isinstance(value, (str, bool)):
            return
        if isinstance(value, int):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"{path} contains a non-finite number")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._check_json_value(item, f"{path}[{index}]")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"{path} contains a non-string key")
                self._check_json_value(item, f"{path}.{key}")
            return
        raise ValueError(f"{path} contains unsupported type {type(value).__name__}")

    def _object_without_duplicates(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def _reject_constant(self, value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    def _encode_scope(self, scope: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in scope.items():
            if key in {"raw_path", "query_string"}:
                result[key] = self._bytes_to_text(value, key)
            elif key == "headers":
                result[key] = self._encode_byte_headers(value)
            elif key in {"server", "client"}:
                result[key] = self._encode_address(value, key)
            else:
                if not isinstance(value, str):
                    raise TypeError(f"scope {key} must be a string")
                result[key] = value
        return result

    def _decode_scope(self, scope: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in scope.items():
            if key in {"raw_path", "query_string"}:
                result[key] = self._text_to_bytes(value, key)
            elif key == "headers":
                result[key] = self._decode_byte_headers(value)
            elif key in {"server", "client"}:
                result[key] = self._decode_address(value, key)
            else:
                if not isinstance(value, str):
                    raise ValueError(f"scope {key} must be a string")
                result[key] = value
        return result

    def _encode_byte_headers(self, headers: Any) -> list[list[str]]:
        if not isinstance(headers, (list, tuple)):
            raise TypeError("scope headers must be a sequence")
        result: list[list[str]] = []
        for pair in headers:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("each scope header must be a pair")
            result.append(
                [self._bytes_to_text(pair[0], "header name"), self._bytes_to_text(pair[1], "header value")]
            )
        return result

    def _decode_byte_headers(self, headers: Any) -> list[tuple[bytes, bytes]]:
        if not isinstance(headers, list):
            raise ValueError("scope headers must be an array")
        result: list[tuple[bytes, bytes]] = []
        for pair in headers:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("each scope header must be a pair")
            result.append(
                (self._text_to_bytes(pair[0], "header name"), self._text_to_bytes(pair[1], "header value"))
            )
        return result

    def _encode_text_headers(self, headers: Any) -> list[list[str]]:
        if not isinstance(headers, (list, tuple)):
            raise TypeError("response headers must be a sequence")
        result: list[list[str]] = []
        for pair in headers:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("each response header must be a pair")
            name, value = pair
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("response header names and values must be strings")
            result.append([name, value])
        return result

    def _decode_text_headers(self, headers: Any) -> list[list[str]]:
        if not isinstance(headers, list):
            raise ValueError("response headers must be an array")
        result: list[list[str]] = []
        for pair in headers:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not isinstance(pair[1], str)
            ):
                raise ValueError("each response header must be a string pair")
            result.append([pair[0], pair[1]])
        return result

    def _encode_address(self, address: Any, field: str) -> list[Any] | None:
        if address is None:
            return None
        if not isinstance(address, (list, tuple)) or len(address) != 2:
            raise ValueError(f"scope {field} must be a host/port pair or None")
        host, port = address
        if not isinstance(host, str):
            raise TypeError(f"scope {field} host must be a string")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError(f"scope {field} port must be an integer from 0 to 65535")
        return [host, port]

    def _decode_address(self, address: Any, field: str) -> tuple[str, int] | None:
        if address is None:
            return None
        if not isinstance(address, list) or len(address) != 2:
            raise ValueError(f"scope {field} must be a host/port pair or null")
        host, port = address
        if not isinstance(host, str):
            raise ValueError(f"scope {field} host must be a string")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError(f"scope {field} port must be an integer from 0 to 65535")
        return host, port

    def _bytes_to_text(self, value: Any, field: str) -> str:
        if not isinstance(value, bytes):
            raise TypeError(f"{field} must be bytes")
        return value.decode("latin-1")

    def _text_to_bytes(self, value: Any, field: str) -> bytes:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        try:
            return value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field} is not a Latin-1 byte representation") from exc
