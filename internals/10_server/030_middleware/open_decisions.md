# Middleware — open decisions

**Version**: 0.1 · **Last Updated**: 2026-09-17 · **Status**: 🔴 DA REVISIONARE

Source report: `kajenn-meta/verification/10_server/030_middleware.md`.

Rows of that report whose verdict is `DIVERGE` or `SILENT`, with the matching `Divergences` blocks. Both are verbatim from the report.

## Table

| # | Entry in decisions.md | Implementation (code file:line, test) | Docstring (file:line, text) | Handoff/finaldoc (file, date, text) | Transcript (uuid, date, role, text) | Verdict |
|---|---|---|---|---|---|---|
| 1 | §1 "The chain is a capability, and the base has none" — "**Source: D17, SPECIFICATION.md:229; D7, SPECIFICATION.md:100.**" · "arrives as a **mixin composed before the server class**" | `src/genro_asgi/middleware/__init__.py:76` `class MiddlewareMixin:` and `:92` "`self._middleware_chain = build_chain(middleware or {}, self._base_call, self, registry)`" · `tests/core/test_middleware.py::test_plain_base_server_lacks_the_mixin_attrs` "`assert not hasattr(server, "middleware_chain")`" | `src/genro_asgi/middleware/__init__.py:15` "Middleware capability: the chain as a mixin over the base server (D16)." · `:17` "The base server has NO middleware. This mixin adds the chain as a capability, composed BEFORE the server class" · `:25` "A composition WITHOUT the mixin simply lacks the attributes — a different type, not a ghost." | `temp/decisioni_registri_cancello_2026-08-25.md` (2026-08-25) line 64 "(`src/genro_asgi/middleware/__init__.py:107`) sta sopra `BaseServer.__call__`" | `85a1e6be-cabc-4ba5-8347-cd0db2f8fd02.jsonl` (2026-07-24, user) "`MiddlewareMixin` — `middleware/__init__.py:80`; `default_registry()` — `:68`" | DIVERGE |
| 2 | §2 "Order is declared, never arranged" — "**Source: owner, 2026-08-23.** Each layer states **one number**, and the chain sorts itself: lowest outermost." | `src/genro_asgi/middleware/base.py:95` "`middleware_order: int = 500`" · `:157` "`enabled.append((cls.middleware_order, cls, options))`" · `:158` "`enabled.sort(key=lambda item: item[0])`" · `:160` "`for _order, cls, options in reversed(enabled):`" · `tests/core/test_middleware.py::test_chain_invokes_middlewares_in_order` "`assert calls == ["early", "late"]`" with `EarlyMiddleware.middleware_order = 200` and `LateMiddleware.middleware_order = 800` | `src/genro_asgi/middleware/base.py:20` "Subclasses declare ``middleware_order`` (lower = outermost; errors 100, logging 200, security 300, auth 400, business 500-800, transformation 900)" · `:28` "Enabled middlewares are sorted by ``middleware_order`` and wrapped innermost-out, so the lowest order ends up outermost." | SILENT | `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` (2026-08-24, assistant) "ordinati da un intero `middleware_order`, il più basso all'esterno" — unconfirmed proposal | SILENT |
| 3 | §2 three positions carrying an argument: "**errors outermost**" · "**session outside identity**" · "**cross-origin outside session**" | `src/genro_asgi/middleware/errors.py:63` "`middleware_order = 100`" · `src/genro_asgi/middleware/cors.py:46` "`middleware_order = 300`" · `src/genro_asgi/middleware/session.py:58` "`middleware_order = 400`" · `src/genro_asgi/middleware/authentication.py:42` "`middleware_order = 450`"; no test asserts the three relative positions | `src/genro_asgi/middleware/session.py:34` "order 400 (OUTSIDE ``AuthMiddleware`` at 450, so the session is on the scope before the §5.5 fallback runs)" · `src/genro_asgi/middleware/authentication.py:21` "order 450 (INSIDE ``SessionMiddleware`` at 400, so ``scope["session"]`` is already attached and the §5.5 session fallback is live)" | SILENT | `667567e2-53e5-4596-8b71-41ad7d67c4c6.jsonl` (2026-08-08, user) "`middleware_order = 400`, `middleware_default = False` (`session.py:59-60`)" | SILENT |
| 4 | §3 "Raising is how a layer answers" — "**Source: owner, 2026-08-23.** No layer inside the middleware chain builds an error response. It **raises**, and the outermost layer turns the exception into an answer." | `src/genro_asgi/middleware/errors.py:76-85` is the chain's only try/except · but `src/genro_asgi/middleware/cors.py:136` "`await send({"type": "http.response.start", "status": 400, "headers": []})`" — a layer inside the chain builds a 400 itself, without raising · `tests/core/test_middleware_std.py::test_preflight_disallowed_origin_returns_400` "`assert response_status(sent) == 400`" | `src/genro_asgi/middleware/errors.py:15` "Error middleware: the outermost try/except of the chain." · `:17` "maps control-flow exceptions to responses: ``Redirect`` → its status plus the ``Location`` header, ``HTTPException`` → its status with the detail, any other ``Exception`` → a hidden 500" · `src/genro_asgi/middleware/cors.py:111` "Short-circuit an OPTIONS preflight; otherwise wrap ``send`` to add CORS headers." | SILENT | `277be791-7ca7-48fb-b185-e5c81ac7aafd.jsonl` (2026-08-19, assistant) "qualunque eccezione che esce dalla richiesta finisce in `ErrorMiddleware`" — unconfirmed proposal | DIVERGE |
| 5 | §4 "The error body follows the caller" — "**Source: the module's own contract; no ratified decision states it.**" · "An error's body is negotiated on the caller's `Accept`" | `src/genro_asgi/middleware/errors.py:115-125` `_wants_json`: "`if not accept or "text/html" in accept: return False`" and "`return "application/json" in accept or "*/*" in accept`" · `tests/core/test_middleware_std.py::test_json_accept_gets_error_document`, `::test_html_accept_keeps_text_plain`, `::test_no_accept_defaults_text_plain` | `src/genro_asgi/middleware/errors.py:25` "Content negotiation (D4 error-body reconciliation): the error body follows the caller's ``Accept``." · `:118` "A missing ``Accept`` keeps the historical ``text/plain`` default" | `.subtasks/bug_analysis/plan.md` (2026-09-12) line 104 "the dispatcher's `try`, and `ErrorMiddleware` already answers an `HTTPException` correctly." | `277be791-7ca7-48fb-b185-e5c81ac7aafd.jsonl` (2026-08-19, assistant) "risponde **500 con corpo \"Internal Server Error\"** — `{\"error\": \"Internal Server Error\"}` se il chiamante chiede JSON" — unconfirmed proposal | DIVERGE |
| 6 | §5 "A 401 is a question…" — "**Source: D24, SPECIFICATION.md:421; commit `5b567a3`, 2026-08-14.**" · "A **browser navigation** — a GET whose `Accept` asks for HTML — is redirected to the login page, carrying where it was going" | `src/genro_asgi/middleware/errors.py:87-113` `_error_response` has no 401 branch and no login URL; `src/genro_asgi/middleware/errors.py:127` `_forward_headers` only forwards the exception's headers · `tests/core/test_middleware_std.py::test_browser_navigation_keeps_the_bare_401` "`assert response_status(sent) == 401`", "`assert b"location" not in response_headers(sent)`" | `src/genro_asgi/middleware/errors.py:32` "A 401 is answered exactly like any other error: the bare status with the exception's ``WWW-Authenticate`` challenge forwarded onto it (D-SA-4). The core never points a caller at a login page — it does not own one" | `.subtasks/server_application_split/finaldoc.md` (2026-09-12) line 62 "**D-SA-4 — the 401 login redirect leaves the core.** From `middleware/errors.py`: `LOGIN_PAGE_URL`, `_response_for`, `_login_active`, `_challenge_response`, `_is_browser_navigation`, `_original_target`" · `temp/decisioni_serverapp_identita_2026-09-12.md` (2026-09-12) line 40 "**D-SA-4 — Il redirect fisso al login su 401 esce dal core.** Il core risponde 401 nudo con `WWW-Authenticate`." | `983688a7-2cf7-48f0-a4bb-591df1aacf29.jsonl` (2026-08-14, assistant) "una navigazione da browser va a `login_page?next=<dove stavi andando>`, un chiamante programmatico tiene il 401 con `login_url` nel corpo" — unconfirmed proposal | DIVERGE |
| 8 | §6 "An answer that has begun cannot be replaced" — "**Source: the module's own contract.**" | `src/genro_asgi/middleware/errors.py:70-74` `tracking_send` sets `started = True` on `http.response.start`, `:79-83` "`if started: self.logger.exception("error after response started serving %s", scope.get("path", "?")); raise`" · `tests/core/test_middleware_std.py::test_error_after_start_is_reraised_not_double_sent` "`with pytest.raises(RuntimeError, match="after start")`" and "`assert len(starts) == 1`" | `src/genro_asgi/middleware/errors.py:38` "The middleware wraps ``send`` to track whether ``http.response.start`` has already passed downstream: an exception raised AFTER the response started cannot be answered (a second start would corrupt the stream), so it is logged and re-raised" | SILENT | SILENT | SILENT |
| 10 | §7 "**Invariant 4, SPECIFICATION.md:674** requires an origin gate on WebSocket handshakes … Where those live … is [20_spa/030 channel](…)'s to decide, and until it does, this page records that the middleware chain does not reach them" | The gate exists and is not in the chain: `src/genro_asgi/wsx.py:271` `_origin_refusal` and `:249-252` "`refusal = self._origin_refusal()`", "`if refusal is not None: await self.socket.refuse(1008, refusal)`" · `tests/core/test_wsx_connection.py::test_an_unlisted_origin_is_refused_without_an_accept`, `::test_a_listed_origin_passes`, `::test_no_origin_header_passes` | `src/genro_asgi/middleware/__init__.py:110` "What reads this is code that must do for a websocket what a middleware does for HTTP — the handshake asking ``SessionMiddleware`` for the session of a scope the chain never saw." · `src/genro_asgi/wsx.py:272` "Why this Origin is not admitted, or ``None`` when it is." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 160 "lo scope websocket di `__call__` non legge lo stato del server (503 solo su http, `server.py:220-238`); la middleware chain" | `60d6d5d6-c863-414c-8eec-a8deb8c89af4.jsonl` (2026-09-06, assistant) "come l'handshake ottiene la sessione senza rifare quello che fa la middleware" — unconfirmed proposal | DIVERGE |
| 11 | §8 "The middleware chain is the machine's, not an application's" — "**Attribution unverified, 2026-09-08.** … The following uniform-scope rationale is a proposed restriction, not a verified owner decision; S6 remains open." | The chain is built once per server, `src/genro_asgi/middleware/__init__.py:92` "`self._middleware_chain = build_chain(middleware or {}, self._base_call, self, registry)`" inside `MiddlewareMixin.__init__`, and `:122-124` `_base_call` delegates to the next `__call__` of the MRO; no code forbids a per-application activation, and no test asserts the prohibition | SILENT — no docstring of the middleware modules states the per-machine uniformity | SILENT | SILENT | SILENT |
| 14 | friction S2 "[placement · cross] — the WebSocket origin gate has nowhere to live." · "The middleware chain is the natural home for it and the middleware chain does not see WebSocket scopes. §7 states the boundary; nothing states where the gate goes." | The gate has a place: `src/genro_asgi/wsx.py:271-290` `_origin_refusal`, called at `:249` before `await self.socket.accept()` at `:253` · `tests/core/test_wsx_connection.py::test_an_unlisted_origin_is_refused_without_an_accept` | `src/genro_asgi/wsx.py:272` "Why this Origin is not admitted, or ``None`` when it is." · `:274` "No ``Origin`` at all passes: it is not a browser, and the gate exists"; SILENT in the middleware package: `src/genro_asgi/middleware/__init__.py` names no origin gate | `temp/revisione_coordinatore_websocket_2026-09-05.md` (2026-09-05) line 97 "`ErrorMiddleware`). Va scritto così nella fase 2, perché è il punto dove un esecutore \"corregge\" e reintroduce" | SILENT | DIVERGE |
| 15 | friction S3 "[undocumented · cross] — … the **middleware element declares five keyword parameters and no more**, so a sixth name is rejected by the grammar itself." · "The element's own docstring records this (\"one registered through `middleware_registry=` is not configurable here\")" | `src/genro_asgi/config/elements.py:214-221` declares `errors`, `logging`, `cors`, `auth`, `session` and `**extra: bool | dict`, so a sixth name is accepted · `tests/core/test_default_configuration.py::test_a_switch_named_outside_the_grammar_reaches_the_tree` passes `middleware={"stamp": True}` and asserts "`server.config("middleware.stamp") is True`" and "`server.get_middleware(StampMiddleware) is not None`" | `src/genro_asgi/config/elements.py:223` "Global middleware switches: one ``{name: bool \| dict}`` kwarg per middleware. … The six declared names are the core's own registry (``middleware.default_registry()``); a middleware registered from outside (``middleware_registry=``) is written by its own name and rides through ``**extra`` — the signature is OPEN so that the switches have ONE place, the configuration, whatever registry the class came from." | `.subtasks/config-always/finaldoc.md` (2026-09-12) line 58 "`middleware(...)` / `plugins(...)` gain `**kwargs`; the constructor kwargs disappear into the tree \| ✅" · line 75 "`middleware_registry`, `plugin_registry` \| they REGISTER classes; the switch for that name lives in the tree" | `25caae71-fa25-4628-a98f-d4026b590a86.jsonl` (2026-09-12, user) "I due elementi della grammatica hanno ora un parametro VAR_KEYWORD: i sei nomi del core restano dichiarati, un nome registrato da fuori (`middleware_registry` / `plugin_registry`) entra come attributo libero." | DIVERGE |
| 16 | friction S4 "[silent] — a misspelled log level becomes INFO without a word." | `src/genro_asgi/middleware/logging.py:53` "`self._level = getattr(logging, level.upper(), logging.INFO)`" — no validation, no log line; no test passes an unknown `level` | SILENT — no module or method docstring names the fallback | SILENT | SILENT | SILENT |
| 17 | friction S5 "[unratified] — `level` names the severity, not a threshold." · "so `level=\"WARNING\"` does not quieten the log — it makes every request a warning" | `src/genro_asgi/middleware/logging.py:69` "`self.logger.log(self._level, "<- %s from %s", request_info, client_ip)`" and `:89` "`self.logger.log(self._level, "-> %s %s (%.1fms)", request_info, status_code, duration)`" — the option is the emitted severity, never compared against a threshold; no test covers `level` | SILENT — `src/genro_asgi/middleware/logging.py:15` "Logs each request's arrival and completion (method, path, status, timing) through the instance logger" says nothing about the semantics of `level` | SILENT | SILENT | SILENT |
| 18 | friction S6 "[unratified] — nothing states that the middleware chain is uniform per machine." | The chain is a single instance on the server, `src/genro_asgi/middleware/__init__.py:92`; no code and no test states the uniformity as a rule | SILENT | SILENT | SILENT | SILENT |
| 19 | friction S7 "[untested] — the cross-origin layer's list-valued options and its credentialed path." · "Three statements of that module are uncovered: the branch that accepts an option already given as a list rather than a comma-separated string, and two of the header-building branches. Line numbers in [status.md](status.md)." | The list branch is covered: `src/genro_asgi/middleware/cors.py:40` "`return list(value)`" is reached by `tests/core/test_middleware_std.py::test_restricted_origins_reject_a_foreign_origin`, which passes `middleware={"cors": {"allow_origins": ["https://allowed.test"]}}` · the credentialed branch `cors.py:94-95` and `:102-103` is covered by `::test_credentialed_wildcard_echoes_origin_with_vary` "`assert headers[b"access-control-allow-credentials"] == b"true"`" · the `expose_headers` branch at `cors.py:104-107` has no test: `expose_headers` appears nowhere in `tests/` | `src/genro_asgi/middleware/cors.py:35` "Split a comma-separated string into a stripped list; pass a list through." · `:90` "CORS headers for one response, or ``[]`` when the origin is not allowed." | SILENT | SILENT | DIVERGE |
| 20 | friction S8 "[unratified] — the error negotiation cites a decision that does not contain it." · "The module attributes the `Accept`-driven error body to \"D4 error-body reconciliation\". D4 (SPECIFICATION.md:67) is about the administrative application" | The negotiation is in the code, `src/genro_asgi/middleware/errors.py:94` "`wants_json = self._wants_json(headers_dict(scope))`" and `:115-125` `_wants_json`, covered by `tests/core/test_middleware_std.py::TestErrorContentNegotiation`; no code carries the citation | `src/genro_asgi/middleware/errors.py:25` "Content negotiation (D4 error-body reconciliation)" · `SPECIFICATION.md:68` "### D4 — The `_server` app" | SILENT | SILENT | SILENT |
| 21 | friction S9 "[silent] — an error response loses everything the inner layers add on the way out." · "answers 200 **with** the cross-origin header and the session cookie and 404 **with neither**" | `src/genro_asgi/middleware/errors.py:84-85` "`response = self._error_response(exc, scope)`" then "`await response(scope, receive, send)`" — the outer `send`, not `tracking_send`, so `send_with_cors` (`cors.py:124`) and the session's cookie wrapper never run for it; no test asserts the missing headers | SILENT in the module | SILENT | `495bf5e3-7740-4559-8a90-01d6bca41f4e.jsonl` (2026-08-23, assistant) "Lo strato più esterno risponde sul `send` che ha ricevuto lui, che sta fuori da tutti gli altri. Provato: stessa route, stessa origine, stessa sessione nuova — il 200 porta l'header cross-origin e il cookie, il 404 non porta né l'uno né l'altro." — unconfirmed proposal | SILENT |
| 22 | friction S10 "[silent] — `errors=False` is accepted, and then nothing answers." · "an `HTTPNotFound` raised by the route resolution **escapes the server uncaught**" | `src/genro_asgi/middleware/base.py:155-156` "`else: continue`" — a false switch drops the layer with no error · `tests/core/test_middleware.py::test_false_switch_disables_a_default_middleware` builds `middleware={"errors": False}` and asserts "`with pytest.raises(RuntimeError, match="boom"): await http_get(server, "/boom")`", the exception leaving the server | `src/genro_asgi/middleware/errors.py:17` "``ErrorMiddleware`` (order 100, the only middleware enabled by default — ``errors=False`` disables it)" · `src/genro_asgi/middleware/base.py:136` "Every middleware named by ``config`` must exist in ``registry`` (``ValueError`` otherwise)" | SILENT | `495bf5e3-7740-4559-8a90-01d6bca41f4e.jsonl` (2026-08-23, assistant) "**`errors=False` è accettato**, e con lo strato esterno spento un `HTTPNotFound` **esce dal server non gestito**." — unconfirmed proposal | SILENT |
| 23 | friction S11 "[cross] — the request id never reaches the log line." · "The only layer that writes a log line writes the method, the path, the status and the elapsed time, and not the id." | `src/genro_asgi/middleware/logging.py:63` "`request_info = f"{method} {path}"`" and `:89` "`self.logger.log(self._level, "-> %s %s (%.1fms)", request_info, status_code, duration)`" — no id · `tests/core/test_middleware_std.py::test_records_one_entry_per_request` "`assert records[1].startswith("-> GET / 200")`" | SILENT — `src/genro_asgi/middleware/logging.py:17` "Logs each request's arrival and completion (method, path, status, timing)" lists the four fields and states no lack | SILENT | SILENT | SILENT |
| 25 | friction (follow-up 2026-09-08): "Inner CORS/session response wrappers are still bypassed by outer error responses. Disabling errors and unknown log-level fallback remain implemented limitations. This documentation task does not silently fix their runtime." | All three limitations are still in the code: `src/genro_asgi/middleware/errors.py:84-85` answers on the outer `send`, so `send_with_cors` (`cors.py:124`) and the session's cookie wrapper do not run for an error; `src/genro_asgi/middleware/base.py:155-156` "`else: continue`" drops the outermost layer on `errors=False`; `src/genro_asgi/middleware/logging.py:53` "`self._level = getattr(logging, level.upper(), logging.INFO)`" · `tests/core/test_middleware.py::test_false_switch_disables_a_default_middleware` asserts the exception leaves the server; no test asserts the bypassed headers, and none passes an unknown level | SILENT — no docstring of the middleware package states any of the three as a limitation | SILENT | `495bf5e3-7740-4559-8a90-01d6bca41f4e.jsonl` (2026-08-23, assistant) "il 200 porta l'header cross-origin e il cookie, il 404 non porta né l'uno né l'altro" and "**`errors=False` è accettato**, e con lo strato esterno spento un `HTTPNotFound` **esce dal server non gestito**." — unconfirmed proposal | SILENT |
| 26 | friction (follow-up 2026-09-08): "Historical uncovered branches are not a current coverage result." | No coverage run was repeated for this check. What is readable per branch: the two `cors.py` branches named by S7 now have tests (row 19), the `expose_headers` branch at `cors.py:104-107` has none, and `logging.py:53` has none · the claim is about a measurement, and no test states it | SILENT (not a docstring matter) | SILENT | SILENT | SILENT |

## Divergences

### #1
- decisions.md says: "**Source: D17, SPECIFICATION.md:229; D7, SPECIFICATION.md:100.**"
- the implementation agrees with the substance: `src/genro_asgi/middleware/__init__.py:76` defines
  `MiddlewareMixin`, `:92` builds the chain in its `__init__`, and
  `tests/core/test_middleware.py::test_plain_base_server_lacks_the_mixin_attrs` asserts
  "`assert not hasattr(server, "middleware_chain")`" on a bare `BaseServer`.
- the docstring says: `src/genro_asgi/middleware/__init__.py:15` "Middleware capability: the chain as
  a mixin over the base server (D16)." — it cites **D16**, not D17
- `SPECIFICATION.md:220` says: "**D16 — Extension = subclassing, made real by contract.**";
  `SPECIFICATION.md:232` says: "**D17 — Capabilities are mixins; communication is the first one.**"
  (decisions.md cites line 229)
- the handoff says: `temp/decisioni_registri_cancello_2026-08-25.md` (2026-08-25) "dei middleware:
  `MiddlewareMixin.__call__` (`src/genro_asgi/middleware/__init__.py:107`) sta sopra
  `BaseServer.__call__`" — no citation of D16 or D17
- the transcript `85a1e6be-cabc-4ba5-8347-cd0db2f8fd02.jsonl` of 2026-07-24 (user) says:
  "`MiddlewareMixin` — `middleware/__init__.py:80`; `default_registry()` — `:68`" — no citation of
  D16 or D17

The substance (a mixin composed before the server class, a base with no chain) coincides; the
decision cited as the source diverges.

### #2
- decisions.md says: "**Source: owner, 2026-08-23.** Each layer states **one number**, and the chain
  sorts itself: lowest outermost."
- the implementation agrees: `src/genro_asgi/middleware/base.py:158` "`enabled.sort(key=lambda item: item[0])`"
  and `:160` "`for _order, cls, options in reversed(enabled):`";
  `tests/core/test_middleware.py::test_chain_invokes_middlewares_in_order` asserts
  "`assert calls == ["early", "late"]`" with orders 200 and 800
- the docstring says: `src/genro_asgi/middleware/base.py:20` "Subclasses declare ``middleware_order``
  (lower = outermost…)" — in agreement
- no handoff and no finaldoc speaks of it
- the transcript `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` of 2026-08-24 (assistant) says:
  "ordinati da un intero `middleware_order`, il più basso all'esterno" — unconfirmed proposal; the
  same turn lists **six** middlewares ("`errors`, `wellknown`, `logging`, `cors`, `session`, `auth`")
  against the five of the current registry at `src/genro_asgi/middleware/__init__.py:67-73`

### #3
- decisions.md says: "**errors outermost**" · "**session outside identity**" · "**cross-origin outside session**"
- the implementation carries the three numbers: errors 100 (`errors.py:63`), cors 300
  (`cors.py:46`), session 400 (`session.py:58`), auth 450 (`authentication.py:42`). No test asserts
  the three relative positions.
- the docstring confirms two of the three in words: `session.py:34` "order 400 (OUTSIDE
  ``AuthMiddleware`` at 450…)" and `authentication.py:21` "order 450 (INSIDE ``SessionMiddleware`` at
  400…)"
- no handoff and no finaldoc states the three arguments
- the transcript `667567e2-53e5-4596-8b71-41ad7d67c4c6.jsonl` of 2026-08-08 (user) cites only
  `middleware_order = 400` for the session

### #4
- decisions.md says: "**Source: owner, 2026-08-23.** No layer inside the middleware chain builds an
  error response. It **raises**, and the outermost layer turns the exception into an answer."
- the implementation at `src/genro_asgi/middleware/cors.py:133-138` does otherwise: `_respond_preflight`
  ends "`if not headers: await send({"type": "http.response.start", "status": 400, "headers": []}); await send({"type": "http.response.body", "body": b""}); return`".
  A layer inside the chain builds a 400 and sends it itself, without raising.
  `tests/core/test_middleware_std.py::test_preflight_disallowed_origin_returns_400` asserts
  "`assert response_status(sent) == 400`" and "`assert b"access-control-allow-origin" not in response_headers(sent)`".
- the docstring says: `src/genro_asgi/middleware/errors.py:15` "Error middleware: the outermost
  try/except of the chain." — in agreement with decisions.md; and
  `src/genro_asgi/middleware/cors.py:111` "Short-circuit an OPTIONS preflight" — the short-circuit is
  documented, its status is not
- no handoff and no finaldoc states the rule "raising is how a layer answers"
- the transcript `277be791-7ca7-48fb-b185-e5c81ac7aafd.jsonl` of 2026-08-19 (assistant) says:
  "qualunque eccezione che esce dalla richiesta finisce in `ErrorMiddleware`" — unconfirmed proposal

### #5
- decisions.md says: "**Source: the module's own contract; no ratified decision states it.**"
- the implementation carries the negotiation: `src/genro_asgi/middleware/errors.py:115-125`
  `_wants_json`, covered by `tests/core/test_middleware_std.py::TestErrorContentNegotiation`
- the docstring says: `src/genro_asgi/middleware/errors.py:25` "Content negotiation (**D4 error-body
  reconciliation**): the error body follows the caller's ``Accept``." — it attributes the negotiation
  to a ratified decision
- `SPECIFICATION.md:68` says: "### D4 — The `_server` app / Automatic, not configured." — D4 does not
  speak of error bodies
- the handoff `.subtasks/bug_analysis/plan.md` of 2026-09-12 says: "`ErrorMiddleware` already answers
  an `HTTPException` correctly" — it does not name the source of the negotiation
- the transcript `277be791-7ca7-48fb-b185-e5c81ac7aafd.jsonl` of 2026-08-19 (assistant) says:
  "`{\"error\": \"Internal Server Error\"}` se il chiamante chiede JSON" — unconfirmed proposal

### #6
- decisions.md says: "Where the installation has a login surface, a 401 is the server asking the
  caller to identify themselves." · "A **browser navigation** — a GET whose `Accept` asks for HTML —
  is redirected to the login page, carrying where it was going so the login can put it back there."
- the implementation does otherwise: `src/genro_asgi/middleware/errors.py:87-113` `_error_response`
  has no 401 branch; `tests/core/test_middleware_std.py::test_browser_navigation_keeps_the_bare_401`
  asserts "`assert response_status(sent) == 401`" and "`assert b"location" not in response_headers(sent)`",
  and the class docstring of that test at `tests/core/test_middleware_std.py:240` says "D-SA-4: the
  core answers a bare 401, the same one to every caller."
- the docstring says: `src/genro_asgi/middleware/errors.py:32` "A 401 is answered exactly like any
  other error: the bare status with the exception's ``WWW-Authenticate`` challenge forwarded onto it
  (D-SA-4). The core never points a caller at a login page — it does not own one"
- `internals/10_server/030_middleware/status.md` (version 0.4, 2026-09-08) says: "The negotiation that
  turned a browser 401 into a 302 to a login page, and an API one into a `login_url` body, is gone,
  and so is the page it pointed at (D-SA-3)."
- the handoff of 2026-09-12 says: `temp/decisioni_serverapp_identita_2026-09-12.md` "**D-SA-4 — Il
  redirect fisso al login su 401 esce dal core.** Il core risponde 401 nudo con `WWW-Authenticate`."
  and `.subtasks/server_application_split/finaldoc.md` "A 401 is now answered like any other error,
  the same one to a browser and to an API caller."
- the transcript `983688a7-2cf7-48f0-a4bb-591df1aacf29.jsonl` of 2026-08-14 (assistant) says: "una
  navigazione da browser va a `login_page?next=<dove stavi andando>`, un chiamante programmatico
  tiene il 401 con `login_url` nel corpo" — unconfirmed proposal
- the source declared by decisions.md is "D24, SPECIFICATION.md:421"; `SPECIFICATION.md:426` says:
  "**D24 — Login attaches identity in place: the session id never changes.**"

### #8
- decisions.md says: "**Source: the module's own contract.** The outermost layer watches the outgoing
  side and knows whether the response has started."
- the implementation agrees: `src/genro_asgi/middleware/errors.py:70-74` and `:79-83`, with
  `tests/core/test_middleware_std.py::test_error_after_start_is_reraised_not_double_sent`
- the docstring says: `src/genro_asgi/middleware/errors.py:38` "an exception raised AFTER the response
  started cannot be answered (a second start would corrupt the stream), so it is logged and re-raised"
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #10
- decisions.md says: "**Invariant 4, SPECIFICATION.md:674** requires an origin gate on WebSocket
  handshakes … Where those live when this core grows long-lived conversations is
  [20_spa/030 channel](…)'s to decide, and until it does, this page records that the middleware chain
  does not reach them."
- the implementation places the gate: `src/genro_asgi/wsx.py:249` "`refusal = self._origin_refusal()`"
  before `:253` "`await self.socket.accept()`", with `_origin_refusal` at `:271-290`;
  `tests/core/test_wsx_connection.py::test_an_unlisted_origin_is_refused_without_an_accept` proves it
- `SPECIFICATION.md:680` says: "4. **Origin gate** on WebSocket handshakes (`handler.py:129-144`)."
  The line cited by decisions.md, `SPECIFICATION.md:674`, carries "2. **Thread-correct teardown**: a
  thread-local resource is released on the".
- the docstring says: `src/genro_asgi/wsx.py:272` "Why this Origin is not admitted, or ``None`` when
  it is."; `status.md` (2026-09-08) says "`WsxConnection` checks Origin and obtains handshake
  identity/session directly" — "nobody knows where the gate lives" is no longer the state of the code
- the handoff `temp/decisioni_websocket_2026-09-05.md` of 2026-09-05 says: "La middleware chain non
  tocca gli scope websocket"
- the transcript `60d6d5d6-c863-414c-8eec-a8deb8c89af4.jsonl` of 2026-09-06 (assistant) says: "come
  l'handshake ottiene la sessione senza rifare quello che fa la middleware" — unconfirmed proposal

### #11
- decisions.md says: "**Attribution unverified, 2026-09-08.** The earlier attribution to an owner
  ruling dated 2026-08-23 has not been recovered."
- the implementation says: the chain is built once in `MiddlewareMixin.__init__`
  (`src/genro_asgi/middleware/__init__.py:92`) and nothing in the code forbids a per-application
  activation; no test asserts the prohibition
- the docstring says: SILENT
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #14
- decisions.md says: "the WebSocket origin gate has nowhere to live" · "nothing states where the gate
  goes"
- the implementation says otherwise: `src/genro_asgi/wsx.py:271` `_origin_refusal` is the gate, called
  at `:249` on every handshake, and `tests/core/test_wsx_connection.py::test_an_unlisted_origin_is_refused_without_an_accept`
  asserts the refusal
- the same decisions.md, in the follow-up of 2026-09-08, says: "The Origin gate belongs to WSX
  handshake processing; raw applications own their handshake policy after the server state gate." —
  the two entries of the same file do not agree
- the docstring says: SILENT in the middleware package; `src/genro_asgi/wsx.py:272` carries it
- the handoff `temp/revisione_coordinatore_websocket_2026-09-05.md` of 2026-09-05 names
  `ErrorMiddleware` but not the gate
- no transcript states that the gate has no home

### #15
- decisions.md says: "the **middleware element declares five keyword parameters and no more**, so a
  sixth name is rejected by the grammar itself. The element's own docstring records this ('one
  registered through `middleware_registry=` is not configurable here')"
- the implementation says otherwise: `src/genro_asgi/config/elements.py:214-221` declares the five
  names plus "`**extra: bool | dict`", and
  `tests/core/test_default_configuration.py::test_a_switch_named_outside_the_grammar_reaches_the_tree`
  drives a sixth name through: "`middleware={"stamp": True}`", "`assert server.config("middleware.stamp") is True`"
- the docstring says: `src/genro_asgi/config/elements.py:223` "a middleware registered from outside
  (``middleware_registry=``) is written by its own name and rides through ``**extra`` — the signature
  is OPEN so that the switches have ONE place, the configuration, whatever registry the class came
  from." The sentence quoted by decisions.md is no longer in the file: "not configurable here" returns
  no hit over `src/`.
- the handoff `.subtasks/config-always/finaldoc.md` of 2026-09-12 says: "`middleware(...)` /
  `plugins(...)` gain `**kwargs`"
- the transcript `25caae71-fa25-4628-a98f-d4026b590a86.jsonl` of 2026-09-12 (user) says: "un nome
  registrato da fuori (`middleware_registry` / `plugin_registry`) entra come attributo libero"

### #16
- decisions.md says: "a misspelled log level becomes INFO without a word"
- the implementation says: `src/genro_asgi/middleware/logging.py:53`
  "`self._level = getattr(logging, level.upper(), logging.INFO)`" — in agreement; no test passes an
  unknown level
- the docstring says: SILENT — no docstring states the fallback; `status.md` (2026-09-08) does: "an
  unknown level falls back to INFO"
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #17
- decisions.md says: "`level` names the severity, not a threshold"
- the implementation says: `src/genro_asgi/middleware/logging.py:69` and `:89` pass `self._level` to
  `self.logger.log` for both lines — in agreement; no test covers `level`
- the docstring says: SILENT — `logging.py:15` describes the logger but not the semantics of `level`
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #18
- decisions.md says: "nothing states that the middleware chain is uniform per machine"
- the implementation says: one chain per server, built at `src/genro_asgi/middleware/__init__.py:92`;
  no rule is stated anywhere in the code
- the docstring says: SILENT
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #19
- decisions.md says: "Three statements of that module are uncovered: the branch that accepts an option
  already given as a list rather than a comma-separated string, and two of the header-building
  branches. Line numbers in [status.md](status.md)."
- the implementation says otherwise for two of the three: `tests/core/test_middleware_std.py::test_restricted_origins_reject_a_foreign_origin`
  passes `allow_origins` as a list and so reaches `src/genro_asgi/middleware/cors.py:40`
  "`return list(value)`"; `::test_credentialed_wildcard_echoes_origin_with_vary` reaches the
  credentialed branches at `cors.py:94-95` and `:102-103`. The `expose_headers` branch at
  `cors.py:104-107` stays uncovered: `expose_headers` appears in no file under `tests/`.
- `status.md` version 0.4 (2026-09-08) carries no line numbers for uncovered branches of `cors.py`: it
  says "Test references below identify the executable contracts; they are not a new coverage
  percentage."
- the docstring says: `cors.py:35` and `:90` describe the branches but not their coverage
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #20
- decisions.md says: "the error negotiation cites a decision that does not contain it"
- the implementation says: the negotiation lives at `src/genro_asgi/middleware/errors.py:115-125` and
  carries no citation; the citation is in the module docstring alone
- the docstring says: `errors.py:25` "Content negotiation (D4 error-body reconciliation)" — the wrong
  citation is still in the file
- `SPECIFICATION.md:68` says: "### D4 — The `_server` app" (decisions.md cites line 67)
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #21
- decisions.md says: "an error response loses everything the inner layers add on the way out … answers
  200 **with** the cross-origin header and the session cookie and 404 **with neither**"
- the implementation says: `src/genro_asgi/middleware/errors.py:84-85` answers on `send`, the callable
  the layer was handed, while the chain ran on `tracking_send`; the cross-origin wrapper
  `send_with_cors` (`cors.py:124`) is therefore not in the path of that response. No test asserts the
  missing headers.
- the docstring says: SILENT in the module; `status.md` (2026-09-08) says it: "The error middleware
  writes through its own outer `send`. Thus an error response bypasses inner CORS/session response
  wrappers"
- no handoff and no finaldoc speaks of it
- the transcript `495bf5e3-7740-4559-8a90-01d6bca41f4e.jsonl` of 2026-08-23 (assistant) says: "il 200
  porta l'header cross-origin e il cookie, il 404 non porta né l'uno né l'altro" — not confirmed by
  the user in the following turn (the user's next turn is "vorei dare una occhita col, serverono di
  dov sev lo riattivi")

### #22
- decisions.md says: "**S10 [silent] — `errors=False` is accepted, and then nothing answers.** The
  switch is a plain member of the five, so a description may turn the outermost layer off. With it
  off, an `HTTPNotFound` raised by the route resolution **escapes the server uncaught**"
- the implementation says: `src/genro_asgi/middleware/base.py:155-156` drops a layer whose switch is
  false without raising, and `tests/core/test_middleware.py::test_false_switch_disables_a_default_middleware`
  asserts that the exception leaves the server: "`with pytest.raises(RuntimeError, match="boom"): await http_get(server, "/boom")`".
  The test raises a `RuntimeError` from the handler; no test drives an `HTTPNotFound` from the route
  resolution with `errors=False`.
- the docstring says: `errors.py:17` "the only middleware enabled by default — ``errors=False``
  disables it" — it confirms the switch is accepted, it does not say what happens next
- no handoff and no finaldoc speaks of it
- the transcript `495bf5e3-7740-4559-8a90-01d6bca41f4e.jsonl` of 2026-08-23 (assistant) says: "un
  `HTTPNotFound` **esce dal server non gestito**." — not confirmed by the user in the following turn

### #23
- decisions.md says: "the request id never reaches the log line"
- the implementation says: `src/genro_asgi/middleware/logging.py:63` builds `request_info` from method
  and path only, and `:89` writes method, path, status and duration;
  `tests/core/test_middleware_std.py::test_records_one_entry_per_request` asserts
  "`assert records[1].startswith("-> GET / 200")`" — in agreement
- the docstring says: SILENT — `logging.py:17` lists "method, path, status, timing", which is
  consistent but states no lack
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #25
- decisions.md says: "Inner CORS/session response wrappers are still bypassed by outer error
  responses. Disabling errors and unknown log-level fallback remain implemented limitations."
- the implementation agrees on all three: `src/genro_asgi/middleware/errors.py:84-85` answers on the
  outer `send`; `src/genro_asgi/middleware/base.py:155-156` drops the layer on a false switch, proved
  by `tests/core/test_middleware.py::test_false_switch_disables_a_default_middleware`;
  `src/genro_asgi/middleware/logging.py:53` falls back to INFO
- the docstring says: SILENT — none of the three is stated as a limitation in the package
- no handoff and no finaldoc speaks of it
- the transcript `495bf5e3-7740-4559-8a90-01d6bca41f4e.jsonl` of 2026-08-23 (assistant) carries two of
  the three, and the user did not confirm them in the following turn — unconfirmed proposal

### #26
- decisions.md says: "Historical uncovered branches are not a current coverage result."
- the implementation says: no coverage run was repeated here; per branch, the two `cors.py` branches
  named by S7 now have tests and `expose_headers` does not (row 19)
- the docstring says: SILENT
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

