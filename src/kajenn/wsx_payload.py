# Copyright 2026 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0.

"""The payload of a WSX message, kept serialized while it is routed.

``SerializedWsxPayload`` holds one TYTX json string, or ``None`` for absent
data. Whoever routes a message copies that string; only the consumer that
wants the value calls ``decode``. ``WsxResponseEncoder`` turns an
application's HTTP answer into that string: a json answer travels
byte-for-byte, an xml or msgpack answer is converted where its codecs are
registered — the application that produced it — so no codec is needed further
along.
"""

from typing import Any, Literal

from genro_tytx import from_tytx, to_tytx


class SerializedWsxPayload:
    """An already serialized TYTX json value; ``None`` is absent data."""

    def __init__(self, text: str | None) -> None:
        """Hold ``text``.

        Raises:
            ValueError: ``text`` is neither a string nor ``None``.
        """
        if text is not None and not isinstance(text, str):
            raise ValueError("serialized WSX data must be a string or absent")
        self.text = text

    def decode(self) -> Any:
        """The hydrated value, or ``None`` when there is no text.

        Called by the consumer that wants the value, never while routing.
        """
        return from_tytx(self.text, "json") if self.text is not None else None


class WsxResponseEncoder:
    """Adapt the producing application's HTTP answer to the browser codec."""

    def encode(self, body: bytes, content_type: str) -> SerializedWsxPayload:
        """One HTTP answer as the TYTX json string a browser reads.

        Args:
            body: the answer's bytes; empty gives an absent payload.
            content_type: the media type the answer declared.

        Returns:
            The payload. A json content-type passes the bytes through as text;
            xml and msgpack are hydrated and re-serialized to json; anything
            else is taken as utf-8 text, or as the bytes when it does not
            decode, and serialized to json.
        """
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
