# Opaque transport benchmark

Measured on 2026-09-08 on macOS Darwin 25.6.0, arm64, Python 3.14.6. The repository base was `2465fcc47dadb1761ec1c09466c847715725daa9` on `feat/opaque-transport-issue72`, with the uncommitted issue 72 implementation in the working tree.

Run from the repository root:

```console
PYTHONPATH=$PWD/src:$PWD .venv/bin/python examples/remote_openapi/benchmark.py
```

The default run starts a fresh endpoint subprocess for each transport, warms each payload size with 10 calls, then performs 300 sequential 1 KiB calls and 30 sequential 1 MiB calls. The endpoint decodes the HTTP request record, builds an HTTP response record with the same body, and returns it. Calls use one persistent connection and `max_calls=1`, so the measurements describe sequential request/reply overhead and throughput rather than concurrency scaling.

Results from one complete 8.1-second measured run:

| Transport | Body | Calls | Median | p95 | Calls/s | Body roundtrip MiB/s | Wire MiB/s | Client CPU | Endpoint CPU total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| UDS | 1 KiB | 300 | 0.186 ms | 0.438 ms | 4,304 | 8.41 | 10.72 | 0.028 s | 0.168 s |
| UDS | 1 MiB | 30 | 7.260 ms | 7.964 ms | 134 | 268.19 | 268.26 | 0.105 s | included above |
| TCP loopback | 1 KiB | 300 | 0.169 ms | 0.236 ms | 5,406 | 10.56 | 13.46 | 0.020 s | 0.050 s |
| TCP loopback | 1 MiB | 30 | 1.043 ms | 1.744 ms | 730 | 1,460.95 | 1,461.34 | 0.020 s | included above |

The endpoint CPU value is `process_time()` across warm-up and both measured cases in that endpoint subprocess. It is repeated only conceptually across the two rows and cannot be attributed to either payload size. Client CPU is measured independently for each measured case. Wall-clock latency includes frame and HTTP-record encoding/decoding, socket transport, scheduling, and the separate endpoint process.

## Bytes on the wire

| Body | Opaque request frame | Opaque request + reply | Base64 JSON request reference | Opaque request reduction |
|---|---:|---:|---:|---:|
| 1 KiB | 1,398 B | 2,611 B | 1,702 B | 304 B (17.9%) |
| 1 MiB | 1,048,949 B | 2,097,713 B | 1,398,438 B | 349,489 B (25.0%) |

The opaque byte counts are exact `len(Frame.encode())` values. They include the fixed frame header, frame JSON info, the HTTP-record header and metadata, and the raw body. There is no body base64.

The reference is deliberately narrow: it serializes the same request routing and HTTP fields into one compact JSON object, encodes the body with standard base64, and adds a four-byte length prefix. It is a serialization size and encode-CPU reference, not a measured old-server or old-orchestration baseline. Encoding that reference 300 times for 1 KiB took 0.0027 client CPU seconds; 30 encodes of 1 MiB took 0.115 seconds for the UDS run and 0.102 seconds for the TCP run.

## Interpretation and limits

This run establishes that both transports move the two selected payload sizes through a real process boundary and that raw bodies avoid the base64 expansion. It does not establish a stable ranking between UDS and TCP. The surprising loopback TCP result for 1 MiB shows why one short run on one machine should not be used for that conclusion; scheduler state, socket buffering, memory copies, and warm cache state were not controlled.

The benchmark uses sequential echo calls, buffered bodies, one client and one endpoint, and no application work beyond record decoding and encoding. It does not measure concurrent calls, remote hosts, TLS, authentication, ASGI routing, WSGI execution, browser WSX, streaming, memory high-water marks, or failure recovery. Repeat runs and a dedicated idle machine are required before using these numbers for capacity planning or regression thresholds.

Parameters can be changed without editing the script:

```console
PYTHONPATH=$PWD/src:$PWD .venv/bin/python examples/remote_openapi/benchmark.py \
  --warmup 20 --small-iterations 1000 --large-iterations 100
```
