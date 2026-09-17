import asyncio
from decimal import Decimal

import pytest
from genro_tytx import from_tytx, to_tytx

from kajenn.asgi_endpoint import BufferedAsgiEndpoint


async def test_complete_request_then_disconnect_after_response_completion():
    observed = []

    async def application(scope, receive, send):
        observed.append(await receive())
        disconnect = asyncio.create_task(receive())
        await asyncio.sleep(0)
        assert not disconnect.done()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})
        observed.append(await disconnect)

    answer = await BufferedAsgiEndpoint(application).serve({"type": "http"}, b"request")

    assert observed == [
        {"type": "http.request", "body": b"request", "more_body": False},
        {"type": "http.disconnect"},
    ]
    assert answer == {"status": 200, "headers": [], "body": b"done"}


async def test_raw_body_and_duplicate_response_headers_are_unchanged():
    async def application(scope, receive, send):
        request = await receive()
        assert request["body"] == b"\x00request\xff"
        await send(
            {
                "type": "http.response.start",
                "status": 201,
                "headers": [(b"set-cookie", b"a=1"), (b"set-cookie", b"b=2")],
            }
        )
        await send({"type": "http.response.body", "body": b"\x00answer\xff"})

    answer = await BufferedAsgiEndpoint(application).serve(
        {"type": "http", "method": "POST"}, b"\x00request\xff"
    )
    assert answer == {
        "status": 201,
        "headers": [["set-cookie", "a=1"], ["set-cookie", "b=2"]],
        "body": b"\x00answer\xff",
    }


async def test_finite_chunks_are_allowed_when_streaming_rejection_is_disabled():
    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"one", "more_body": True})
        await send({"type": "http.response.body", "body": b"two", "more_body": False})

    answer = await BufferedAsgiEndpoint(application, reject_streaming=False).serve({}, b"")
    assert answer["body"] == b"onetwo"


async def test_streaming_sse_and_trailers_are_rejected():
    async def streaming(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"one", "more_body": True})

    async def sse(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"Content-Type", b"text/event-stream; charset=utf-8")],
            }
        )

    async def trailers(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [], "trailers": True})

    with pytest.raises(ValueError, match="streaming"):
        await BufferedAsgiEndpoint(streaming).serve({}, b"")
    with pytest.raises(ValueError, match="event"):
        await BufferedAsgiEndpoint(sse).serve({}, b"")
    with pytest.raises(ValueError, match="trailers"):
        await BufferedAsgiEndpoint(trailers).serve({}, b"")
    with pytest.raises(ValueError, match="trailers"):
        await BufferedAsgiEndpoint(streaming).serve(
            {"extensions": {"http.response.trailers": {}}}, b""
        )


@pytest.mark.parametrize("status", [True, 99, 600, "200"])
async def test_invalid_status_is_rejected(status):
    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})

    with pytest.raises(ValueError, match="status"):
        await BufferedAsgiEndpoint(application).serve({}, b"")


async def test_start_body_order_and_completion_are_enforced():
    async def no_response(scope, receive, send):
        return None

    async def no_finish(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def body_first(scope, receive, send):
        await send({"type": "http.response.body", "body": b"bad"})

    async def start_twice(scope, receive, send):
        start = {"type": "http.response.start", "status": 200, "headers": []}
        await send(start)
        await send(start)

    async def complete_twice(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        await send({"type": "http.response.body", "body": b""})

    for application, error, match in [
        (no_response, RuntimeError, "start"),
        (no_finish, RuntimeError, "complete"),
        (body_first, ValueError, "before"),
        (start_twice, ValueError, "started"),
        (complete_twice, ValueError, "completed"),
    ]:
        with pytest.raises(error, match=match):
            await BufferedAsgiEndpoint(application).serve({}, b"")


async def test_request_and_cumulative_response_limits_apply_before_collection():
    called = False

    async def application(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"123", "more_body": True})
        await send({"type": "http.response.body", "body": b"4"})

    endpoint = BufferedAsgiEndpoint(application, max_body_size=3, reject_streaming=False)
    with pytest.raises(ValueError, match="request"):
        await endpoint.serve({}, b"1234")
    assert not called
    with pytest.raises(ValueError, match="response"):
        await endpoint.serve({}, b"")


async def test_invalid_headers_and_body_are_rejected():
    async def bad_headers(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [("x", b"y")]})

    async def bad_body(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": "text"})

    with pytest.raises(TypeError, match="headers"):
        await BufferedAsgiEndpoint(bad_headers).serve({}, b"")
    with pytest.raises(TypeError, match="body"):
        await BufferedAsgiEndpoint(bad_body).serve({}, b"")


@pytest.mark.parametrize("transport", ["xml", "msgpack"])
async def test_wsk_converts_application_transport_to_json_and_repairs_headers(transport):
    value = {"amount": Decimal("12.50")}
    encoded = to_tytx(value, transport)
    source_body = encoded if isinstance(encoded, bytes) else encoded.encode("utf-8")

    async def application(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", f"application/vnd.tytx+{transport}".encode()),
                    (b"content-length", str(len(source_body)).encode()),
                    (b"x-result", b"kept"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": source_body})

    answer = await BufferedAsgiEndpoint(application).serve({"method": "WSK"}, b"")
    assert from_tytx(answer["body"].decode(), "json") == value
    assert answer["headers"] == [
        ["x-result", "kept"],
        ["content-type", "application/json"],
        ["content-length", str(len(answer["body"]))],
    ]


async def test_ordinary_http_does_not_adapt_media_body():
    source_body = to_tytx({"answer": 42}, "xml").encode()

    async def application(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/vnd.tytx+xml")],
            }
        )
        await send({"type": "http.response.body", "body": source_body})

    answer = await BufferedAsgiEndpoint(application).serve({"method": "GET"}, b"")
    assert answer["body"] == source_body
    assert answer["headers"] == [["content-type", "application/vnd.tytx+xml"]]
