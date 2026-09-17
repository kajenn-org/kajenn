# Websocket — open decisions

**Version**: 0.1 · **Last Updated**: 2026-09-17 · **Status**: 🔴 DA REVISIONARE

Source report: `kajenn-meta/verification/10_server/055_websocket.md`.

Rows of that report whose verdict is `DIVERGE` or `SILENT`, with the matching `Divergences` blocks. Both are verbatim from the report.

## Table

| # | Entry in decisions.md | Implementation (code file:line, test) | Docstring (file:line, text) | Handoff/finaldoc (file, date, text) | Transcript (uuid, date, role, text) | Verdict |
|---|---|---|---|---|---|---|
| 2 | §1 "That last one is judged HIGHER than the others (owner, 2026-09-07): above the demux, before anybody knows which application would have served the socket, and for the raw mode too." · "the handshake is turned away before the accept, so the browser sees it fail with no readable code — 1013 exists only after an accept" | `src/genro_asgi/server.py:482-484` "`if self.state != RUNNING: await WebSocket(scope, receive, send).refuse(1013, "server restarting"); return`" — before `:485` "`app, target = self.demux(scope)`" and before the raw seam at `:486-489` · `src/genro_asgi/websocket.py:162` `refuse` turns the handshake away without accepting · `tests/core/test_websocket_raw_seam.py::test_a_server_that_is_not_running_never_reaches_it` and `::test_a_server_that_is_not_running_refuses_it_the_same_way` | `src/genro_asgi/server.py:464` "The state is judged FIRST, above the demux, for every websocket: a server that is not ``RUNNING`` takes no new connection in charge, and the handshake is turned away before the accept. The browser sees the handshake fail with no readable code — 1013 exists only after an accept, and in the raw mode the accept belongs to the application" | SILENT — the register `temp/decisioni_websocket_2026-09-05.md` carries no 2026-09-07 decision on the server state above the demux; `temp/handoff_2026-09-07.md` does not carry it | SILENT | SILENT |
| 4 | §1 "**Deliberately not built**: a direct browser → worker channel for high-frequency traffic. It is reopened only on a measurement (owner, 2026-09-05)" | SILENT — no browser-to-worker channel exists in `src/`; the only road to the worker is the lane (`spa_commander.serve_wsx_request`), and no test names a direct channel | SILENT — no docstring names the direct channel or the measurement | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 204 "**RINVIATO**: «canale diretto browser → worker per il traffico ad alta frequenza», da riaprire su una misura (messaggi/s che il server regge con la lane in mezzo; il banco dei salti si estende)." | `cecd1427-0cdd-4b7a-9c69-7b011c3523fa.jsonl` (2026-09-05 09:29, user, record 588) "il traffico tra websocket e spa è al 99.9% sul suo worjker. per motivi di velocita non sarebbe comunque preferibile . il client potrebbe nel caso aver un websocket cobale verso il server e uno locale per il grosso del traffioco. avrebbe senso ?" · (2026-09-05 09:31, user, record 599) "ok. prmario quello e websocket alta frequenz asoloun domani. il vantaggio è cfhe tutte quelle che sono ora rpc potrebbero diventaree websocket mi pare" | SILENT |
| 5 | §2 "**Source: owner, 2026-09-05 (W-2), the third road.** A message for the SPA becomes an ordinary CALL on the worker's lane, in the `http` form the worker already serves. The wire keeps its two lanes, CALL and REPLY: no third envelope type, no port in the worker, no client websocket opened from the server." | `src/genro_asgi_multiworker_spa/orchestration/spa_commander.py:734` `async def serve_wsx_request(...)` · `src/genro_asgi_multiworker_spa/orchestration/spa_worker.py:1462` `async def serve_wsx(self, frame, payload)` · `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestOpeningTheChannelOfAPage::test_the_worker_wrote_the_channel_on_the_row` drives a message from the socket to the worker over the lane; no socket and no port is opened towards the worker anywhere in `src/` | `src/genro_asgi_multiworker_spa/orchestration/spa_commander.py:737` "Serve one channel command of a page, the way a request of the site is served." · `spa_worker.py:1463` "Serve the ``wsx`` CALL form: a channel command of one page." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 66 "La terza strada: **websocket terminato al server, worker raggiunto con CALL ordinarie**" | `4aae3a9b-514f-4fbf-9b34-26fb2566336c.jsonl` (2026-09-05, assistant) "Il legacy apriva un secondo websocket verso il worker" — unconfirmed proposal; no user turn of 2026-09-05 chooses the third road | SILENT |
| 6 | §2 "**Deliberately not built**: the worker holding its own websocket behind a port of its own … never a switch between two transports both built (owner, 2026-09-05: «potremmo immaginare di decidere a livello di configurazione?» → yes, as the choice of the class)." | SILENT — no port and no websocket server exists in `src/genro_asgi_multiworker_spa/orchestration/`; no configuration switch between two transports exists | SILENT | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 76 "«potremmo immaginare di decidere a livello di configurazione?» → sì come scelta della classe (`worker_class` nella ricetta), non come interruttore fra due trasporti entrambi costruiti." | SILENT | SILENT |
| 7 | §3 "**Source: owner, 2026-09-05 (W-3), «a», widened; owner, 2026-09-06 (W-11).** On the websocket travel the page's rpc … the commands addressed to one page, and the shared object when it exists." · "What does NOT travel here is the unsolicited delivery of what happens elsewhere … That delivery stays **pull**, as ratified on 2026-08-29" | SILENT — the only server-initiated write is `src/genro_asgi/server.py:300` `send_message`, addressed to one page; nothing pushes dbevents or another user's writes onto a socket, and no test asserts the prohibition | SILENT — no docstring of the core states the prohibition of push | `temp/decisioni_websocket_2026-09-05.md` (2026-09-06) line 553 "«sul websocket viaggia ciò che una pagina chiede (rpc, anche di sincronizzazione dei suoi dati) e ciò che il server manda a UNA pagina con un comando indirizzato via send_message; non viaggia la consegna non sollecitata di ciò che accade altrove (scritture di altri utenti, dbevents delle tabelle), che resta a richiesta come ratificato il 2026-08-29.»" | SILENT | SILENT |
| 9 | §4 "`data` is the TYTX string — what `to_tytx(value, \"json\")` produces, placed in the envelope as a JSON string; the receiver calls `from_tytx(envelope.data, \"json\")`." · "Bytes travel as the `RAW` type of genro-tytx." | `src/genro_asgi/wsx.py:150-152` "`self.serialized_data = serialized_data or SerializedWsxPayload(to_tytx(data, "json") if data is not None else None)`" — but reading goes through `:142` "`serialized_data = SerializedWsxPayload(fields.get("data"))`" and `:158-160` the `data` property returns `self.serialized_data.decode()`, an explicit consumer decode, while routing carries `serialized_data` · `tests/core/test_wsx_envelope.py::test_bytes_travel_base64_inside_the_json_body` asserts "`"YWI=::RAW" in body["data"]`", `::test_a_bag_comes_back_a_bag_with_its_values_and_attributes`, `::test_a_decimal_comes_back_exact` | `src/genro_asgi/wsx.py:37` "**The application data stays serialized while routing.** The outer JSON contains a TYTX string. WsxEnvelope parses only that JSON and keeps an explicit SerializedWsxPayload. Its data property is an opt-in consumer decoder; routing uses serialized_data." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 314 "valori tipizzati in TYTX dentro `data`" · `temp/review_issue72_sintesi_2026-09-08.md` (2026-09-08) line 13 lists the points "verificati intatti" | `6b6cc58d-68ce-4eb7-adc7-2f2f247ae9a3.jsonl` (2026-09-07, assistant) "B — bytes in `send_message` \| `spa/orchestration/spa_worker.py:1629` `to_tytx(data, \"json\")`; `spa_app.py:448` `from_tytx` \| chiuso su entrambi i lati" | DIVERGE |
| 11 | §4b "**Source: owner, 2026-09-05 (W-4d), «se non c'è id NON rispondiamo».** It is executed and nothing is answered; a failure goes to the log." · "`reply_path`, a common field of the envelope rather than a per-application convention — chosen so «che venga gestita in modo anarchico» could not happen" | `src/genro_asgi/wsx.py:358-359` "`if envelope.id is not None: await self._answer(envelope, status, data)`" and `:383` "`self._logger.exception("Websocket: message %s failed", envelope.path)`" · `reply_path` is a field of the envelope at `:193` and reaches the scope at `:421-422` · `tests/core/test_wsx_connection.py::test_a_message_with_no_id_is_answered_by_nothing`, `::test_the_page_and_the_reply_path_reach_the_application` | `src/genro_asgi/wsx.py:342` "A message with an ``id`` is registered in the server's request registry … A message without one is an event: served the same way, answered by nothing, its failure logged." · `:33` "``reply_path`` is where a page asks to be called back when the work is done." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 330 "Senza `id` = EVENTO fire-and-forget: eseguito, nessuna risposta («se non c'è id NON rispondiamo»); un errore va nel log … Scelto il campo comune invece della convenzione per app «per evitare che venga gestita in modo anarchico»." | SILENT | SILENT |
| 12 | §4b "An event is also NOT registered in the server's `RequestRegistry` (owner, 2026-09-05, W-4f): the shutdown does not wait for it" | `src/genro_asgi/wsx.py:350` "`item = self.server.requests.register(scope) if envelope.id else None`" · `tests/core/test_wsx_connection.py::test_a_message_with_an_id_is_a_registered_request` and `::test_an_event_is_served_and_counted_nowhere` | `src/genro_asgi/wsx.py:342` "A message with an ``id`` is registered in the server's request registry — the shutdown waits for it, and it shows in the picture — and answered with its status." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 341 "DECISA: «a, con il controllo che le chiamate senza id non vengano registrate in quanto fire and forget»" | SILENT | SILENT |
| 13 | §4c "**Source: owner, 2026-09-05 (W-4e), «Le chiavi si chiamano `genro.page_id` e `genro.reply_path`, documentate in `spa/environ.py` accanto a `genro.identity`. Su una request http vera sono assenti, non `None`».** Synthetic headers were refused" | The keys exist and are conditional: `src/genro_asgi/wsx.py:419-422` "`if envelope.page_id is not None: scope["genro.page_id"] = envelope.page_id`" / "`if envelope.reply_path is not None: scope["genro.reply_path"] = envelope.reply_path`", and `src/genro_asgi_multiworker_spa/environ.py:43` "`CALL_KEYS = ("genro.page_id", "genro.reply_path")`" beside `:91` "`"genro.identity": identity`". The file they are documented in is `src/genro_asgi_multiworker_spa/environ.py`; the path `spa/environ.py` cited by the decision does not exist. No synthetic header carries them · `tests/core/test_wsx_connection.py::test_the_page_and_the_reply_path_reach_the_application`, `::test_a_message_without_them_leaves_them_out_of_the_scope` | `src/genro_asgi/wsx.py:405-406` "``genro.page_id`` and ``genro.reply_path`` are there only when the message carried them." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 336 "Parole del titolare: «Le chiavi si chiamano genro.page_id e genro.reply_path, documentate in spa/environ.py accanto a genro.identity. Su una request http vera sono assenti, non None.» Scartati gli header sintetici (falsificabili da fuori)." | `25caae71-fa25-4628-a98f-d4026b590a86.jsonl` (2026-09-12, assistant) "`REQUEST_METHOD == \"WSK\"` e `genro.page_id` in environ" | DIVERGE |
| 15 | §5 "Every message is then placed like a request: the barrier, the index, the worker of the moment. The worker does not own the physical websocket and receives no physical-disconnect notification." · "What is given up is presence" | `src/genro_asgi_multiworker_spa/orchestration/spa_commander.py:761` `resolve_worker` is the one placement road, reached by `serve_wsx_request` at `:734` · nothing notifies a worker of a physical disconnect: `src/genro_asgi/wsx.py:240-242` unregisters the socket in the `finally` of `serve()` and tells nobody · no test asserts a presence notion | `src/genro_asgi_multiworker_spa/orchestration/spa_commander.py:780` "Every form of request comes through here, so the barrier, the reception-first rule and the placement are written once and every caller meets them the same way." — presence SILENT | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 401 and following (W-5, choice «a+») | SILENT | SILENT |
| 18 | §6 "A route-level metadatum was considered and WITHDRAWN: the grain is the page." | SILENT — no route metadatum for the websocket exists in the routing code, and no test names one | SILENT — no docstring names the withdrawn route metadatum | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 506 "Il metadato di route è RITIRATO. «sì mi pare buona»." | SILENT | SILENT |
| 20 | §7 "Per message the rule holds independently: a message addressed to the SPA from a socket carrying no connection id is answered with status 403." | `src/genro_asgi_multiworker_spa/spa_app.py:508-509` "`if cid is None: raise HTTPBadRequest("this connection carries no cookie")`" — status 400, not 403; the 403 at `:512` is the other case, "`raise HTTPForbidden(f"page {page_id!r} is not this connection's")`" · `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestACallerWithNoCookie::test_the_command_refuses_a_request_that_carries_no_connection` asserts "`with pytest.raises(HTTPBadRequest, match="no cookie")`" | `src/genro_asgi_multiworker_spa/spa_app.py:497` "HTTPBadRequest: the message names no page, or the request carries no connection at all." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-06) line 577 "Per ogni messaggio verso la SPA da un socket senza cid: 403." | SILENT | DIVERGE |
| 21 | §8 "**Source: owner, 2026-09-05 (W-4b), «a»; refined 2026-09-06 (W-12).** A message the server sends by itself has the shape of a request … Server messages carry no `id`" · "`send_message` is fire-and-forget … «Delivered» means the ASGI `send` returned — never «executed by the page»." | `src/genro_asgi/server.py:319-324` "`socket = self.websockets.get_page_socket(page_id)`", "`if socket is None or not socket.connected: return False`", "`envelope = WsxEnvelope(method="WSK", path=path, data=data, page_id=page_id)`", "`await socket.send_text(envelope.encode())`", "`return True`" — no `id` is set and nothing is awaited afterwards · `tests/core/test_websocket_server_send.py::test_the_message_carries_no_id_and_names_its_page`, `::test_it_has_the_shape_of_a_request`, `::test_a_page_that_speaks_on_no_socket_is_told_so`, `::test_nothing_is_written_after_the_browser_left` | `src/genro_asgi/server.py:313` "The message has the shape of a request and carries NO ``id``: it is not an answer, and nobody answers it … DELIVERED means written to the socket, never executed by the page: nothing here waits for anything." · `spa_worker.py:1623` "Fire and forget in meaning, not in mechanics — delivered says written to the socket, never executed by the page (W-12)." | `temp/decisioni_websocket_2026-09-05.md` (2026-09-06) line 561 "«send_message è fire-and-forget: la REPLY dice \"scritto sul socket\" o \"nessun websocket per questa pagina\"; i messaggi del server viaggiano senza id; una risposta del browser è una rpc del client sul reply_path o su un path suo.»" | SILENT | SILENT |
| 23 | §10 "**Source: owner, 2026-09-06.** … `SpaWorker.asgi_app` is the seam a consumer assigns; `AsgiSeam` builds the ASGI scope from the `http` dict a CALL carries" · "Synchronous work … runs on the traffic pool through `SpaWorker.run_sync(work)`, which copies the CALL's context" · "There is no streaming, deliberately" | `src/genro_asgi_multiworker_spa/environ.py:57` `build_scope(self, http, identity=None)` and `:120` "`return await BufferedAsgiEndpoint(self.asgi_app, reject_streaming=False).serve(scope, http.get("body") or b"")`" — the answer is buffered whole, never streamed · `src/genro_asgi_multiworker_spa/orchestration/spa_worker.py:724` `run_sync` · `src/genro_asgi/wsx.py:439` "`raise RuntimeError("a streaming answer cannot travel on a websocket message")`" · `tests/spa/orchestration/test_orchestration_asgi_seam.py`; `tests/core/test_wsx_connection.py::test_a_streaming_answer_is_refused_out_loud` | `src/genro_asgi_multiworker_spa/environ.py:47` "One ASGI application, called from the facts of a CALL." · `spa_worker.py:733` "The thread runs under a COPY of the calling task's context, so it finds the request slot of the CALL it serves" | `temp/handoff_2026-09-06_coordinatore.md` (2026-09-06) line 37 "battesimi N1-N13 + `asgi_app`, `AsgiSeam`, `run_sync`, `handshake_cookie`, `sequential`." | `6b54f386-ee2e-4878-8ed5-3cee73e9df7b.jsonl` (2026-09-07, assistant) "`SpaWorker.asgi_app` con `AsgiSeam` (fase 4a). Il WSGI è la scorciatoia `WsgiSeam`, non la regola." — unconfirmed proposal | SILENT |
| 24 | §11 "**Source: owner, 2026-09-06, form B.** … The legacy enters through `WsgiSeam`, which is an ASGI application around the WSGI callable" · "Assigning both is an explicit error … **That last requirement was explicitly reversed by the owner on 2026-09-07 in §14 below:** a worker without either callable may boot" | `src/genro_asgi_multiworker_spa/orchestration/spa_worker.py:710-714` "`if self.asgi_app is not None and self.wsgi_app is not None: raise RuntimeError(f"Worker {self.name}: asgi_app and wsgi_app are both assigned; the WSGI shortcut is an alternative to the ASGI seam, not an addition")`", `:715-718` returns `AsgiSeam(self.asgi_app)` or `AsgiSeam(WsgiSeam(self.wsgi_app, self))`, `:719-722` raises only when the seam is ASKED for: "`Worker {self.name}: neither asgi_app nor wsgi_app is assigned; this worker hosts no application`" — a worker with neither boots · `tests/spa/orchestration/test_orchestration_asgi_seam.py` | `src/genro_asgi_multiworker_spa/environ.py:126` "One WSGI callable, reached as an ASGI application." · `spa_worker.py:703` "BOTH is a contradiction somebody declared, and ``WorkerEntry`` reads this at boot for exactly that case" · `:706` "NEITHER is the base worker, which is legitimate: it serves its orders and hosts nothing, and it learns so here, when an http CALL finally asks it to serve a request." | `temp/handoff_2026-09-07.md` (2026-09-07) — the day the rule was reversed | SILENT | SILENT |
| 25 | §11 "The mixed routing lives in the CONSUMER's ASGI router … The core knows no path prefixes: a rule on the path inside the core was the alternative, and it was refused." | SILENT — no path prefix rule exists in `src/genro_asgi_multiworker_spa/`: `hosted_app_seam` resolves one callable and the path is never inspected for a family; no test asserts the absence | `src/genro_asgi_multiworker_spa/environ.py:128` "What a hosted ASGI application calls to delegate one request to the legacy" — the rule "no path prefixes in the core" is SILENT | SILENT | SILENT | SILENT |
| 26 | §12 "**Source: owner, 2026-09-05 (W-7), «pensavo che, capito il problema, riscrivessi ex novo con nomi giusti e convenzioni attuali».** … genro-tytx as a hard dependency" · "The pipe that copied frames towards a worker is not rewritten in any form." | `pyproject.toml:39` "`"genro-tytx>=0.15.0",`" among the runtime dependencies and `:56` "`"genro-tytx[msgpack]>=0.15.0",`"; a search over `src/` for `ws_pipe`, `WebSocketPipe` and `nats` returns nothing | SILENT — no docstring states the rewrite or the absence of the pipe | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 461 "### Scelta del titolare (2026-09-05): riscrivere ex novo — la (c), con precisazione" · line 441 "`ws_pipe.py` (191) — `WebSocketPipe` \| **NON SERVE** \| Trasporto scartato (W-2)" | SILENT | SILENT |
| 27 | §13 "**Source: owner, 2026-09-05 (W-6), «diciamo che sotto genro-asgi gnrasync è tutto nuovo»** … Under genro-asgi there is no separate async process." | SILENT — `gnrasync` does not exist in this repository; the rebuilding belongs to the genropy-asgi bridge, outside this perimeter | SILENT | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 418 "Parole del titolare: «diciamo che sotto genro-asgi gnrasync è tutto nuovo» e «ovviamente gnrasync è compito del bridge rifarlo e fare in modo che venga importato lui, magari con una modesta modifica a genropy»." · `temp/issue_genropy_asgi_websocket_2026-09-07.md` (2026-09-07) line 67 "**`gnrasync` falls.** Under genro-asgi the separate async process does not exist." | `63a093d9-ad0f-4275-b0cc-24d9039bbbad.jsonl` (2026-09-06, user) "gnrasync cade (W-6 del registro). Il bridge lo rifà e si fa importare da genropy al posto suo." | SILENT |
| 28 | §14 "Every name below was baptised by the owner, one per turn, between 2026-09-05 and 2026-09-07 — the ones the code needed as it was written included, because a name that was not foreseen is still a name." (table of 35 names) | The names of the table exist in the code at the points cited in the rows above, and the code carries others the table does not list: `src/genro_asgi/wsx_payload.py` defines `SerializedWsxPayload` and `WsxResponseEncoder` (imported at `src/genro_asgi/wsx.py:77`), `src/genro_asgi/server.py:326` `async def send_serialized_message(self, page_id, path, payload: SerializedWsxPayload)` · `tests/core/test_wsx_envelope.py` and `tests/core/test_websocket_server_send.py` exercise them | `src/genro_asgi/wsx.py:37-40` "keeps an explicit SerializedWsxPayload. Its data property is an opt-in consumer decoder; routing uses serialized_data." · `src/genro_asgi/server.py:329` "Forward an explicitly serialized application value to its page." | `temp/websocket_indagine_2026-09-05_journal.md` (2026-09-06) line 148 "battesimo N17: property `handshake_cookie` («c»)" · `temp/decisioni_websocket_2026-09-05.md` line 581 "## Battesimi del 2026-09-06 (sessione `asgi-ws`, uno per turno)" | `cecd1427-0cdd-4b7a-9c69-7b011c3523fa.jsonl` (2026-09-06, user) "c): handshake_cookie, property, rende il nome del cookie che l'handshake deve portare, None se nessuno. Prossimo." | DIVERGE |
| 29 | §14 "**What the boot checks** (owner, 2026-09-07). … The plan said «neither → error»; the plan was wrong" · "**Where the page names itself in `openchannel`** (owner, 2026-09-07). In the envelope's own `page_id` field, never in the payload" · "**Who binds the page to the socket** (owner, 2026-09-07). The application decides … and the connection binds … only because the answer was 200." | `src/genro_asgi_multiworker_spa/spa_app.py:504-506` "`page_id = _request.scope.get("genro.page_id")`" / "`raise HTTPBadRequest("openchannel names no page: put it in the envelope's page_id")`" — never the payload · `src/genro_asgi/wsx.py:392-396` "`if status == 200 and target.get("path") == OPENCHANNEL_PATH and envelope.page_id:`" … "`self.server.websockets.bind_page(envelope.page_id, self.socket)`" · `spa_worker.py:718-721` for the boot check · `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestAPageThatIsNotThisConnections::test_neither_is_ever_bound_to_the_socket`, `::test_a_message_that_names_no_page_is_refused` | `spa_worker.py:706` "NEITHER is the base worker, which is legitimate" · `spa_app.py:493` "the page it names travels in the envelope's own field, never in the payload" and `:500` "The page is bound to the socket by the CONNECTION, not here, and only because this answered 200: whoever holds the socket does the binding, whoever holds the pool decides (owner, 2026-09-07)." | SILENT — no handoff of 2026-09-07 records these three refinements | SILENT | SILENT |
| 30 | §14a "**Source: owner, 2026-09-07, confirming a reading of two decisions already taken.** The `wsx` field travels in the parcel like any other datum of a page … What stays behind is `call_lock` alone" | `src/genro_asgi_multiworker_spa/register_row.py:103` "`fields_left_behind = RegisterRow.fields_left_behind \| {"connection_id", "call_lock"}`" — `wsx` is not among them · `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestTheChannelSurvivesTheDeposit::test_a_page_woken_from_the_deposit_still_has_its_channel` asserts "`worker.page_register.get(PAGE)["wsx"] == {"sequential": True}`" after a freeze and an adopt, and `::test_the_queue_of_a_woken_page_is_a_fresh_one` asserts "`isinstance(after, asyncio.Lock) and after is not before`" | `src/genro_asgi_multiworker_spa/register_row.py:88` "It TRAVELS in the parcel — it is ``True`` or a dict of plain data — because a user parked for being idle and woken by his next request never lost his websocket" · `:106` "it never travels — a page that comes back from the deposit gets a fresh one" | SILENT | SILENT | SILENT |
| 31 | §15 "**What is deliberately absent** — No pipe and no port in the worker (§2). · No push of datachanges or dbevents (§3). · No shared object yet … · No NATS, no pub/sub, no streaming, no binary frames. · No direct browser → worker channel (§1). · The monitor is NOT the first consumer" | Four of the six absences are readable in the code: no `ws_pipe`, no `nats` and no port in `src/`; `src/genro_asgi/wsx.py:439` refuses a streaming answer out loud; `:316-318` a non-text frame is dropped ("`if text is None:`" / "`self._logger.warning("Websocket: a binary frame carries no WSX message")`" / "`continue`") · `tests/core/test_wsx_connection.py::test_a_binary_frame_is_ignored_and_the_socket_lives_on`, `::test_a_streaming_answer_is_refused_out_loud`. The shared object and the monitor are absences nothing in the code states | SILENT — no docstring lists the absences | `temp/decisioni_websocket_2026-09-05.md` (2026-09-05) line 140 "**Stato**: RINVIATA dal titolare il 2026-09-05 — «non è nelle priorità attuali. Però dobbiamo tenerlo presente come problema di fondo per evitare decisioni che poi alzino il livello di difficoltà»." · line 683 "la prova con un browser vero … NON si fa prima del merge della PR #69. La fa **genro-pages**, primo consumatore" | SILENT | SILENT |

## Divergences

### #2
- decisions.md says: "That last one is judged HIGHER than the others (owner, 2026-09-07): above the demux, before anybody knows which application would have served the socket, and for the raw mode too."
- the implementation agrees: `src/genro_asgi/server.py:482-484` refuses before `:485` `self.demux(scope)` and before the raw seam at `:486-489`, covered by
  `tests/core/test_websocket_raw_seam.py::test_a_server_that_is_not_running_never_reaches_it`
- the docstring says the same: `src/genro_asgi/server.py:464` "The state is judged FIRST, above the demux, for every websocket"
- no handoff and no finaldoc records the 2026-09-07 decision on the server state above the demux:
  the register `temp/decisioni_websocket_2026-09-05.md` closes the points of 2026-09-07 on other
  subjects (the live probe, the three refinements of §14)
- no transcript speaks of it

### #4
- decisions.md says: "**Deliberately not built**: a direct browser → worker channel for high-frequency traffic."
- the implementation says: SILENT — the only road to the worker is the lane
- the docstring says: SILENT
- the handoff of 2026-09-05 says: `temp/decisioni_websocket_2026-09-05.md` "**RINVIATO**: «canale diretto browser → worker per il traffico ad alta frequenza», da riaprire su una misura"
- the transcript `cecd1427-0cdd-4b7a-9c69-7b011c3523fa.jsonl` of 2026-09-05 (user, record 588) asks
  it — "il client potrebbe nel caso aver un websocket cobale verso il server e uno locale per il
  grosso del traffioco. avrebbe senso ?" — and (user, record 599) postpones it: "ok. prmario quello e
  websocket alta frequenz asoloun domani". The measurement that would reopen it is named by the
  handoff, not by the transcript.

### #5
- decisions.md says: "**Source: owner, 2026-09-05 (W-2), the third road.** A message for the SPA
  becomes an ordinary CALL on the worker's lane … no third envelope type, no port in the worker, no
  client websocket opened from the server."
- the implementation agrees: `src/genro_asgi_multiworker_spa/orchestration/spa_commander.py:734`
  `serve_wsx_request` and `spa_worker.py:1462` `serve_wsx` serve the message on the lane the worker
  already answers, proved by
  `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestOpeningTheChannelOfAPage::test_the_worker_wrote_the_channel_on_the_row`;
  no socket and no port towards the worker exists in `src/`
- the docstring says: `spa_commander.py:737` "Serve one channel command of a page, the way a request
  of the site is served."
- the handoff of 2026-09-05 says: `temp/decisioni_websocket_2026-09-05.md` line 66 "La terza strada:
  **websocket terminato al server, worker raggiunto con CALL ordinarie**"
- the transcript `4aae3a9b-514f-4fbf-9b34-26fb2566336c.jsonl` of 2026-09-05 (assistant) says only "Il
  legacy apriva un secondo websocket verso il worker", the context of question W-2 — unconfirmed
  proposal. A search of the user turns of 2026-09-05 in
  `cecd1427-0cdd-4b7a-9c69-7b011c3523fa.jsonl` and
  `4aae3a9b-514f-4fbf-9b34-26fb2566336c.jsonl` finds no turn choosing the third road.

### #6
- decisions.md says: "never a switch between two transports both built"
- the implementation says: SILENT — no second transport exists
- the docstring says: SILENT
- the handoff of 2026-09-05 says: "sì come scelta della classe (`worker_class` nella ricetta), non come interruttore fra due trasporti entrambi costruiti"
- no transcript speaks of it

### #7
- decisions.md says: "That delivery stays **pull**, as ratified on 2026-08-29 — a queue per page, collected on the page's next call."
- the implementation says: SILENT — the only server-initiated write is `send_message`, addressed to one page
- the docstring says: SILENT — no core module states the prohibition
- the handoff of 2026-09-06 says: `temp/decisioni_websocket_2026-09-05.md` line 553, with the owner's words
- no transcript speaks of it

### #9
- decisions.md says: "`data` is the TYTX string — what `to_tytx(value, \"json\")` produces, placed in the envelope as a JSON string; the receiver calls `from_tytx(envelope.data, \"json\")`."
- the implementation says: the value constructor still serializes, `src/genro_asgi/wsx.py:150-152`
  "`self.serialized_data = serialized_data or SerializedWsxPayload(to_tytx(data, "json") if data is not None else None)`",
  but the receiving side no longer decodes on the way: `:142` keeps a `SerializedWsxPayload` and
  `:158-160` makes `data` an explicit consumer decode ("`return self.serialized_data.decode()`"),
  while routing carries `serialized_data`. `tests/core/test_wsx_envelope.py::test_bytes_travel_base64_inside_the_json_body`
  asserts the RAW form on the wire.
- the docstring says: `src/genro_asgi/wsx.py:37` "WsxEnvelope parses only that JSON and keeps an
  explicit SerializedWsxPayload. Its data property is an opt-in consumer decoder; **routing uses
  serialized_data**. Value constructors and public send_message still serialize ordinary Python values."
- the handoff of 2026-09-05 says: "valori tipizzati in TYTX dentro `data`", naming neither
  `serialized_data` nor `SerializedWsxPayload`
- the transcript `6b6cc58d-68ce-4eb7-adc7-2f2f247ae9a3.jsonl` of 2026-09-07 (assistant) says:
  "`to_tytx(data, \"json\")`; `spa_app.py:448` `from_tytx` \| chiuso su entrambi i lati" — the form
  decisions.md describes, not the one of the current code

### #11
- decisions.md says: "It is executed and nothing is answered; a failure goes to the log." ·
  "`reply_path`, a common field of the envelope rather than a per-application convention"
- the implementation agrees: `src/genro_asgi/wsx.py:358-359` answers only when `envelope.id is not
  None`, `:383-384` logs the failure, and `reply_path` is a field of the envelope, not of an
  application; `tests/core/test_wsx_connection.py::test_a_message_with_no_id_is_answered_by_nothing`
- the docstring says: `src/genro_asgi/wsx.py:342` "A message without one is an event: served the same
  way, answered by nothing, its failure logged."
- the handoff of 2026-09-05 says: "Senza `id` = EVENTO fire-and-forget: eseguito, nessuna risposta
  («se non c'è id NON rispondiamo»); un errore va nel log"
- no transcript speaks of it

### #12
- decisions.md says: "An event is also NOT registered in the server's `RequestRegistry` (owner,
  2026-09-05, W-4f): the shutdown does not wait for it"
- the implementation agrees: `src/genro_asgi/wsx.py:350` "`item = self.server.requests.register(scope) if envelope.id else None`",
  with `tests/core/test_wsx_connection.py::test_an_event_is_served_and_counted_nowhere`
- the docstring says: `src/genro_asgi/wsx.py:342` "A message with an ``id`` is registered in the
  server's request registry — the shutdown waits for it"
- the handoff of 2026-09-05 says: "DECISA: «a, con il controllo che le chiamate senza id non vengano
  registrate in quanto fire and forget»"
- no transcript speaks of it

### #13
- decisions.md says: "Le chiavi si chiamano `genro.page_id` e `genro.reply_path`, documentate in
  `spa/environ.py` accanto a `genro.identity`"
- the implementation says: the keys exist and are conditional (`src/genro_asgi/wsx.py:419-422`), and
  they are declared at `src/genro_asgi_multiworker_spa/environ.py:43` "`CALL_KEYS = ("genro.page_id", "genro.reply_path")`"
  beside `:91` "`"genro.identity": identity`". The path `spa/environ.py` does not exist: the SPA
  package is `src/genro_asgi_multiworker_spa/`.
- the docstring says: `src/genro_asgi/wsx.py:405-406` "``genro.page_id`` and ``genro.reply_path`` are
  there only when the message carried them."
- the handoff of 2026-09-05 says: `temp/decisioni_websocket_2026-09-05.md` line 336, with the same
  path `spa/environ.py`
- the transcript `25caae71-fa25-4628-a98f-d4026b590a86.jsonl` of 2026-09-12 (assistant) says:
  "`genro.page_id` in environ"

### #15
- decisions.md says: "The worker does not own the physical websocket and receives no
  physical-disconnect notification." · "What is given up is presence"
- the implementation agrees: `src/genro_asgi/wsx.py:240-242` unregisters the socket in the `finally`
  of `serve()` and notifies no worker; no code and no test carries a presence notion
- the docstring says: SILENT — no module states the renunciation of presence
- the handoff of 2026-09-05 records the «a+» choice of W-5 without stating the renunciation of
  presence in those words
- no transcript speaks of it

### #18
- decisions.md says: "A route-level metadatum was considered and WITHDRAWN: the grain is the page."
- the implementation says: SILENT — no route metadatum exists
- the docstring says: SILENT
- the handoff of 2026-09-05 says: "Il metadato di route è RITIRATO. «sì mi pare buona»."
- no transcript speaks of it

### #20
- decisions.md says: "a message addressed to the SPA from a socket carrying no connection id is
  answered with **status 403**"
- the implementation says otherwise: `src/genro_asgi_multiworker_spa/spa_app.py:508-509` "`if cid is
  None: raise HTTPBadRequest("this connection carries no cookie")`" — a 400. The 403 in the code
  (`HTTPForbidden` at `:512`) is another case: "page … is not this connection's". The test
  `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestACallerWithNoCookie::test_the_command_refuses_a_request_that_carries_no_connection`
  fixes the 400: "`with pytest.raises(HTTPBadRequest, match="no cookie")`".
- the docstring says: `src/genro_asgi_multiworker_spa/spa_app.py:497` "**HTTPBadRequest**: the message
  names no page, or **the request carries no connection at all**"
- the handoff of 2026-09-06 says: `temp/decisioni_websocket_2026-09-05.md` line 577 "Per ogni
  messaggio verso la SPA da un socket senza cid: **403**."
- no transcript speaks of it

### #21
- decisions.md says: "Server messages carry no `id`; an answer from the browser is a client rpc on
  `reply_path` or on a path of its own." · "«Delivered» means the ASGI `send` returned — never
  «executed by the page»."
- the implementation agrees: `src/genro_asgi/server.py:322-324` builds the envelope without an `id`
  and returns right after the write;
  `tests/core/test_websocket_server_send.py::test_the_message_carries_no_id_and_names_its_page`
- the docstring says: `src/genro_asgi/server.py:313` "The message has the shape of a request and
  carries NO ``id``" and "DELIVERED means written to the socket, never executed by the page"
- the handoff of 2026-09-06 says: `temp/decisioni_websocket_2026-09-05.md` line 561, with the owner's
  words
- no transcript speaks of it

### #23
- decisions.md says: "There is no streaming, deliberately, exactly as for WSGI"
- the implementation says: the seam buffers instead of streaming,
  `src/genro_asgi_multiworker_spa/environ.py:120` "`return await BufferedAsgiEndpoint(self.asgi_app, reject_streaming=False).serve(scope, http.get("body") or b"")`",
  and on the WSX road a streaming answer is refused out loud, `src/genro_asgi/wsx.py:439`
  "`raise RuntimeError("a streaming answer cannot travel on a websocket message")`", proved by
  `tests/core/test_wsx_connection.py::test_a_streaming_answer_is_refused_out_loud`
- the docstring says: SILENT for the streaming of the seam; `AsgiSeam` and `run_sync` are documented
- the handoff of 2026-09-06 says: `temp/handoff_2026-09-06_coordinatore.md` lists the namings but not
  the renunciation of streaming
- the transcript `6b54f386-ee2e-4878-8ed5-3cee73e9df7b.jsonl` of 2026-09-07 (assistant) says:
  "`SpaWorker.asgi_app` con `AsgiSeam` (fase 4a)" — unconfirmed proposal

### #24
- decisions.md says: "**That last requirement was explicitly reversed by the owner on 2026-09-07 in
  §14 below:** a worker without either callable may boot"
- the implementation agrees: `src/genro_asgi_multiworker_spa/orchestration/spa_worker.py:719-722`
  raises only when `hosted_app_seam` is asked for, while `:710-714` raises for BOTH
- the docstring says: `spa_worker.py:706` "NEITHER is the base worker, which is legitimate"
- no handoff and no finaldoc records the reversal of 2026-09-07
- no transcript speaks of it

### #25
- decisions.md says: "The core knows no path prefixes: a rule on the path inside the core was the
  alternative, and it was refused."
- the implementation says: SILENT — no path prefix rule exists in the core
- the docstring says: SILENT
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #26
- decisions.md says: "genro-tytx as a hard dependency" · "The pipe that copied frames towards a
  worker is not rewritten in any form."
- the implementation says: `pyproject.toml:39` "`"genro-tytx>=0.15.0",`" and `:56`
  "`"genro-tytx[msgpack]>=0.15.0",`"; no `ws_pipe` module and no `WebSocketPipe` exists in `src/`
- the docstring says: SILENT
- the handoff of 2026-09-05 says: "`ws_pipe.py` (191) — `WebSocketPipe` | **NON SERVE** | Trasporto
  scartato (W-2)"
- no transcript speaks of it

### #27
- decisions.md says: "Under genro-asgi there is no separate async process."
- the implementation says: SILENT — `gnrasync` belongs to genropy, outside this repository
- the docstring says: SILENT
- the handoff of 2026-09-05 and the one of 2026-09-07 say it with the owner's words
- the transcript `63a093d9-ad0f-4275-b0cc-24d9039bbbad.jsonl` of 2026-09-06 (user) says: "gnrasync
  cade (W-6 del registro). Il bridge lo rifà e si fa importare da genropy al posto suo."

### #28
- decisions.md says: "Every name below was baptised by the owner … the ones the code needed as it was
  written included, because a name that was not foreseen is still a name."
- the implementation carries names the table does not list: `src/genro_asgi/wsx_payload.py` defines
  `SerializedWsxPayload` and `WsxResponseEncoder`, imported at `src/genro_asgi/wsx.py:77`
  ("`from .wsx_payload import SerializedWsxPayload, WsxResponseEncoder`"), and
  `src/genro_asgi/server.py:326` defines `send_serialized_message`
- the docstring says: `src/genro_asgi/wsx.py:37-40` and `src/genro_asgi/server.py:329` document those
  three names, none of which is in the table of §14
- the handoffs record the namings N1…N13 and N14 onwards, and none of the three names above appears
  among them
- the transcript `cecd1427-0cdd-4b7a-9c69-7b011c3523fa.jsonl` of 2026-09-06 (user) shows the
  procedure, one name per turn: "c): handshake_cookie … Prossimo."

### #29
- decisions.md says: "**Where the page names itself in `openchannel`** (owner, 2026-09-07). In the
  envelope's own `page_id` field, never in the payload"
- the implementation agrees: `src/genro_asgi_multiworker_spa/spa_app.py:504` reads the page from
  `_request.scope.get("genro.page_id")` and `:506` refuses a message that names none;
  `src/genro_asgi/wsx.py:392-396` binds the page only on a 200
- the docstring says the same: `spa_app.py:486-487` "the page it names travels in the envelope's own
  field, never in the payload"
- no handoff and no finaldoc records the three refinements of 2026-09-07
- no transcript speaks of it

### #30
- decisions.md says: "What stays behind is `call_lock` alone"
- the implementation agrees: `src/genro_asgi_multiworker_spa/register_row.py:103` leaves behind
  `connection_id` and `call_lock` only, and
  `tests/spa/orchestration/test_orchestration_websocket_e2e.py::TestTheChannelSurvivesTheDeposit`
  proves both halves — the channel comes back, the lock is a fresh one
- the docstring says the same: `register_row.py:88` and `:106`
- no handoff and no finaldoc speaks of it
- no transcript speaks of it

### #31
- decisions.md states the list of six deliberate absences
- the implementation shows four of them: no pipe, no port, no NATS in `src/`; a streaming answer
  refused at `src/genro_asgi/wsx.py:439`; a binary frame dropped at `src/genro_asgi/wsx.py:316-318`, proved by
  `tests/core/test_wsx_connection.py::test_a_binary_frame_is_ignored_and_the_socket_lives_on`
- the docstring says: SILENT
- the handoff of 2026-09-05 records two of the six (the postponed shared object, the monitor not the
  first consumer); the other four are not listed as absences in any handoff
- no transcript speaks of it

