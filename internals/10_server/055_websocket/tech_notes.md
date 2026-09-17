# WebSocket — technical notes

**Version**: 0.1 · **Last Updated**: 2026-09-08 · **Status**: historical evidence

## Historical delivery measures and order

The following records describe the original phase branches, not a current
coverage measurement.

**Historical measure, recorded at the phase branch head.** The whole suite is 1946 passed at
97% coverage; the websocket's own tests are **170**, across
`tests/core/test_websocket_facade.py` (31), `tests/core/test_wsx_envelope.py` (29),
`tests/core/test_websocket_registry.py` (12), `tests/core/test_wsx_connection.py` (37),
`tests/core/test_websocket_server_send.py` (8), `tests/core/test_websocket_raw_seam.py`
(6), `tests/spa/orchestration/test_orchestration_websocket_e2e.py` (16) and
`tests/spa/orchestration/test_orchestration_asgi_seam.py` (31). The three modules
the motor lives in — `websocket.py`, `wsx.py`, `kajenn_orchestra/environ.py` — are covered
100% by those alone.

## The order of the work

| Phase | What lands | |
|---|---|---|
| 0 | this folder, Q1 resolved, the namings — no code | **DONE** |
| 1 | the `WebSocket` facade, `WsxEnvelope`, `WebSocketDisconnect` | **DONE** |
| 2 | `WsxConnection`, `WebSocketRegistry`, `on_websocket`, the config element | **DONE** |
| 3 | the server speaks first, proven on a test application | **DONE** |
| 4a | `asgi_app`, `AsgiSeam`, `hosted_app_seam`, `WsgiSeam` as the adapter, `run_sync` | **DONE** |
| 4 | the SPA: message → CALL, `openchannel`, the per-page queue, the push | **DONE** |
| 5 | `serve_websocket`, the admitted raw seam | **DONE** |
| 6 | documents | **DONE** |
| 7 | release 0.43.0 | historical release step; this audit is on the later develop baseline |

Tests first in every phase, one commit per phase, the suite green. The working
plan of the phases is local to the machine this work runs on and is not
committed; what it decides lands here.
