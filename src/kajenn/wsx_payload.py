# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""Explicit serialized browser data and conversion at an application endpoint.

A routing process may copy SerializedWsxPayload.text but never calls decode.
JSON replies remain byte-for-byte TYTX text. XML/msgpack conversion belongs
at the application that produced the response, where its codecs are registered.
"""

from typing import Any, Literal

from genro_tytx import from_tytx, to_tytx



class SerializedWsxPayload:
    """An already serialized JSON TYTX value; None represents absent data."""

    def __init__(self, text: str | None) -> None:
        if text is not None and not isinstance(text, str):
            raise ValueError("serialized WSX data must be a string or absent")
        self.text = text

    def decode(self) -> Any:
        """Hydrate at an explicit consumer, never while routing."""
        return from_tytx(self.text, "json") if self.text is not None else None


class WsxResponseEncoder:
    """Adapt the producing application's HTTP answer to the browser codec."""

    def encode(self, body: bytes, content_type: str) -> SerializedWsxPayload:
        if not body:
            return SerializedWsxPayload(None)
        formats: tuple[Literal["json", "xml", "msgpack"], ...] = ("json", "xml", "msgpack")
        transport = next((name for name in formats if name in content_type), None)
        if transport == "json":
            return SerializedWsxPayload(body.decode("utf-8"))
        if transport is not None:
            value = from_tytx(body if transport == "msgpack" else body.decode("utf-8"), transport)
        else:
            try:
                value = body.decode("utf-8")
            except UnicodeDecodeError:
                value = body
        encoded = to_tytx(value, "json")
        if isinstance(encoded, bytes):
            encoded = encoded.decode("utf-8")
        return SerializedWsxPayload(encoded)
