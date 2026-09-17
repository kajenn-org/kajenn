# Sessions — open decisions

**Version**: 0.1 · **Last Updated**: 2026-09-17 · **Status**: 🔴 DA REVISIONARE

Source report: `kajenn-meta/verification/10_server/040_sessions.md`.

Rows of that report whose verdict is `DIVERGE` or `SILENT`, with the matching `Divergences` blocks. Both are verbatim from the report.

## Table

| # | Entry in decisions.md | Implementation (code file:line, test) | Docstring (file:line, text) | Handoff/finaldoc (file, date, text) | Transcript (uuid, date, role, text) | Verdict |
|---|---|---|---|---|---|---|
| 1 | "Seeded ahead of the audit — the session knows its authenticating connection" · "**Source: owner, 2026-08-24 (interview on 020_applications S2). Not yet implemented.** The session record carries the connection id of the SPA connection that authenticated it — a **scalar**, written once by the first application that performs authentication, cleared when the session's identity decays. The reverse direction (connection → session) lives on the `connection_register_item`." | SILENT — `src/genro_asgi/session/session.py:63` `__slots__ = ("_id", "_meta", "_data", "_avatars", "_dirty")`; `grep` for `connection_id` and `connection_register_item` over `src/genro_asgi/session/` returns nothing; no test in `tests/` exercises a connection id on a `Session` | SILENT — `src/genro_asgi/session/session.py:15` "Server-managed session: id, meta, Bag data, and a keyed collection of Avatars." and `:61` "Server-managed session with meta, Bag data, and keyed identity avatars."; no docstring names a connection | `temp/design_identita_avatars_worker_2026-08-01.md` (2026-08-01) lines 100-103 "La **connection NON diventa un concetto del core**: è un'entità DELLO STRATO LEGACY MONTATO, legata 1:1 alla session (la voce connection porta il session_id, o connection_id derivato dalla session)." | `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` (2026-08-24 09:57, user) "la session del server dovrebbe avere la connection id in modo da avere anche l'info opposta" · (2026-08-24 09:58, user) "dipende solo da chi fa autenticazio9ne ed è solo la primaapp che la fa" · (2026-08-24 10:00, user) "proviamo cosiù: intanto mettiamo queste cosde nel disegno e poi sappiamo che dovremo implementarle" | DIVERGE |

## Divergences

### #1
- decisions.md says: "The session record carries the connection id of the SPA connection that authenticated it — a **scalar**, written once by the first application that performs authentication, cleared when the session's identity decays."
- the implementation says: SILENT. `src/genro_asgi/session/session.py:63` declares
  `__slots__ = ("_id", "_meta", "_data", "_avatars", "_dirty")`, and `Session.__init__` at
  `src/genro_asgi/session/session.py:67` takes `(self, session_id: str, avatar: Avatar | None, ttl: int)`.
  A `grep` for `connection_id` and `connection_register_item` over `src/genro_asgi/session/` returns
  no line. In `tests/`, `connection_id` appears only as the `spa_connection_id` cookie of the SPA
  worker fixtures (`tests/core/opaque_spa_worker.py:13` "`COOKIE = "spa_connection_id"`"), never as a
  field of a `Session`. No test exercises the decision.
- the docstring says: SILENT. `src/genro_asgi/session/session.py:15` "Server-managed session: id,
  meta, Bag data, and a keyed collection of Avatars." and `:61` "Server-managed session with meta,
  Bag data, and keyed identity avatars."
- `internals/10_server/040_sessions/status.md` (version 0.2, 2026-09-08) says: "The authenticating
  SPA connection link described in the decisions is absent from `Session` and from the SPA
  response-building path."
- the handoff of 2026-08-01 says: `temp/design_identita_avatars_worker_2026-08-01.md` "La
  **connection NON diventa un concetto del core**" and puts the link on the connection item ("la voce
  connection porta il session_id"), that is, in the reverse direction only. The same file, line 110,
  says "direzione a senso unico: il legacy scrive nella session, mai la session nel registro legacy
  fuori dal momento di login". No later handoff and no finaldoc records the decision of 2026-08-24.
- the transcript `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` of 2026-08-24 says, in order:
  (09:57, user) "la session del server dovrebbe avere la connection id in modo da avere anche l'info
  opposta"; (09:57, assistant) "il valore nel record di sessione non può essere uno scalare, serve
  una voce per app (`app code → connection id`)"; (09:58, user) "dipende solo da chi fa
  autenticazio9ne ed è solo la primaapp che la fa"; (09:58, assistant) "il campo lato sessione non è
  una mappa — è **lo scalare della connessione che ha autenticato**. La regola: scrive il legame solo
  l'app che fa l'autenticazione, ed è la prima che la fa" and "si azzera solo quando l'identità della
  sessione decade (logout, scadenza)"; (10:00, user) "proviamo cosiù: intanto mettiamo queste cosde
  nel disegno e poi sappiamo che dovremo implementarle"; (10:01, assistant) "**040 sessions**: il
  record di sessione conosce la connessione autenticante".

The transcript agrees with decisions.md and is confirmed by the user. The implementation is silent,
and the design of 2026-08-01 diverges by keeping the connection outside the core.

