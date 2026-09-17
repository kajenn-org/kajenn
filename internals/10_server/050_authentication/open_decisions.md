# Authentication — open decisions

**Version**: 0.1 · **Last Updated**: 2026-09-17 · **Status**: 🔴 DA REVISIONARE

Source report: `kajenn-meta/verification/10_server/050_authentication.md`.

Rows of that report whose verdict is `DIVERGE` or `SILENT`, with the matching `Divergences` blocks. Both are verbatim from the report.

## Table

| # | Entry in decisions.md | Implementation (code file:line, test) | Docstring (file:line, text) | Handoff/finaldoc (file, date, text) | Transcript (uuid, date, role, text) | Verdict |
|---|---|---|---|---|---|---|
| 1 | "Seeded ahead of the audit — the avatar for a site-authenticated user" · "**Source: owner, 2026-08-24 … Not yet implemented.** A user authenticated by the hosted site itself (the genropy legacy login) must not be anonymous on the non-legacy branches: without an avatar, a tagged route challenges a user who already logged in." · "the identity block the site declares on the return … becomes a server avatar attached to the session" | `src/genro_asgi_multiworker_spa/spa_app.py:1158` reads from the return `settled = reply.info.get("connection_id")` and `:1160` writes `response.set_cookie(SPA_CONNECTION_ID_COOKIE, settled, …)` and nothing else; `attach_avatar` does not appear anywhere in `src/genro_asgi_multiworker_spa/` · `tests/spa/test_spa_application.py::test_the_connection_the_site_named_becomes_the_cookie` asserts only `f"{SPA_CONNECTION_ID_COOKIE}=site-1" in cookie` | `src/genro_asgi_multiworker_spa/spa_app.py:1051` "Nothing is minted: the identity is the site's to give, and it gives it while serving. What comes back names the connection the request settled on, and that is what the cookie is written with." | `temp/design_step1_identita_registri_2026-08-01.md` (2026-08-01) lines 119-122 "**Ponte di login** (senso unico): l'atto di login legacy costruisce l'avatar → `session.attach_avatar(...)` → POI aggiorna la voce connections nel formato legacy." | `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` (2026-08-24 09:53, user) "altrimenti se l'utente autenticato su genrpy legacy va in un. ramo non legacy non ha tags" · (2026-08-24 09:59, user) "lo stesso alla trasformaxzione da qgets a user dobiamo aggiungere delle info che raggiungano il middleware per dargli le cose necessarie a fare lo'avatar" · (2026-08-24 10:00, user) "proviamo cosiù: intanto mettiamo queste cosde nel disegno e poi sappiamo che dovremo implementarle" | DIVERGE |
| 2 | "**identity**: declared by the site at its guest→user transition" | SILENT — the return path reads one key only, `src/genro_asgi_multiworker_spa/spa_app.py:1158` "`settled = reply.info.get("connection_id")`"; no code reads an identity block from the site's reply, and no test asserts one | SILENT — no docstring of the core or of the pool describes an identity block on the return | `temp/design_step1_identita_registri_2026-08-01.md` (2026-08-01) lines 112-115 "**Registro `connections`**: aggiunto via `add_register` — voce: `connection_id`, `session_id`, `user`, `user_id`, `user_name`, `user_tags`, `avatar_extra`, `browser_name`, ip/user_agent." | `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` (2026-08-24 09:59, assistant) "il sito dichiara «questa connessione ora è l'utente X», il front lo consegna, e l'avatar viene coniato e attaccato alla sessione" — taken up by the user at 10:00 "intanto mettiamo queste cosde nel disegno" | SILENT |
| 3 | "**tags**: assigned by the SPA application's server-side configuration, never declared by the site — the same shape as the OIDC method, which mints `Avatar(identity, provider_config_tags)`. The tag vocabulary stays the server's" | The OIDC shape exists: `src/genro_asgi_server_app/oidc_method.py:253` "`avatar = Avatar(identity, self.provider.get("tags", []))`" and `:254` "`session.attach_avatar(avatar)`"; `src/genro_asgi/auth/core.py:179` "`return Avatar(result["identity"], result["tags"])`" · `tests/server_app/test_oidc.py::test_callback_authenticates_and_redirects_to_next` asserts "`promoted.avatar().identity == "alice@example.com"`" and "`promoted.avatar().tags == []`" against the provider config `"tags": []` at `tests/server_app/test_oidc.py:57`. The application of that shape to the hosted site is SILENT: no code mints an `Avatar` from a site return | `src/genro_asgi_server_app/oidc_method.py:240` "Route: GET /_server/auth/<method_id>/callback"; no docstring extends the shape to the hosted site | `temp/design_step1_identita_registri_2026-08-01.md` (2026-08-01) lines 112-115 "`user_tags`, `avatar_extra`" among the fields of the connection item written by the legacy login | `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` (2026-08-24 09:59, assistant) "b) **il sito dichiara solo l'identity**, e i tag li assegna la configurazione dell'app SPA sul server (come fa il provider OIDC) … Consiglio: **b)**" · (2026-08-24 10:03, user) "non è veroi nel metodo ci sono le rules non i tag" · (2026-08-24 10:08, assistant) "il **metodo routed** (decorato `@route`) dichiara la **`auth_rule`** … **l'avatar dell'utente** porta i **tag** … io parlavo del **metodo di autenticazione** (la config del provider OIDC)" — the user does not answer again on the a)/b) choice | DIVERGE |

## Divergences

### #1
- decisions.md says: "the identity block the site declares on the return … becomes a server avatar attached to the session"
- the implementation at `src/genro_asgi_multiworker_spa/spa_app.py:1158-1167` does something else with
  the return: "`settled = reply.info.get("connection_id")`", then
  "`if settled is not None and settled != carried:`" and "`response.set_cookie(SPA_CONNECTION_ID_COOKIE, settled, max_age=CONNECTION_COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax")`".
  The return writes the connection cookie, not an avatar. `attach_avatar` does not appear anywhere in
  `src/genro_asgi_multiworker_spa/`. The test that covers that path,
  `tests/spa/test_spa_application.py::test_the_connection_the_site_named_becomes_the_cookie`,
  asserts the cookie only.
- the docstring says: the opposite, on the return. `src/genro_asgi_multiworker_spa/spa_app.py:1051`
  "Nothing is minted: the identity is the site's to give, and it gives it while serving."
- `internals/10_server/050_authentication/status.md` (version 0.2, 2026-09-08) says: "A hosted site's
  connection-user change updates the SPA pool's indexes. It does not implement the separately
  recorded return-path conversion into a core session avatar with server-configured tags."
- the handoff of 2026-08-01 says: `temp/design_step1_identita_registri_2026-08-01.md` "l'atto di
  login legacy costruisce l'avatar → `session.attach_avatar(...)` → POI aggiorna la voce connections
  nel formato legacy". Writer and moment differ from those of decisions.md: there the legacy login
  writes, here the front writes on the return.
- the transcript `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` of 2026-08-24 (user, 09:53) says:
  "altrimenti se l'utente autenticato su genrpy legacy va in un. ramo non legacy non ha tags", and
  (user, 10:00) "proviamo cosiù: intanto mettiamo queste cosde nel disegno e poi sappiamo che dovremo
  implementarle" — in agreement with decisions.md.

### #2
- decisions.md says: "**identity**: declared by the site at its guest→user transition"
- the implementation says: SILENT. The only key the front reads from the site's reply is the
  connection id, `src/genro_asgi_multiworker_spa/spa_app.py:1158` "`settled = reply.info.get("connection_id")`".
  No test asserts an identity block on the return.
- the docstring says: SILENT
- no handoff and no finaldoc names an identity block declared by the site on the return;
  `temp/design_step1_identita_registri_2026-08-01.md` lists the fields of the connection item, which
  is another structure
- the transcript `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` of 2026-08-24 (assistant, 09:59) says:
  "il sito dichiara «questa connessione ora è l'utente X», il front lo consegna, e l'avatar viene
  coniato e attaccato alla sessione"; the user takes it up at 10:00 with "intanto mettiamo queste
  cosde nel disegno"

### #3
- decisions.md says: "**tags**: assigned by the SPA application's server-side configuration, never declared by the site"
- the implementation has the cited shape for OIDC — `src/genro_asgi_server_app/oidc_method.py:253`
  "`avatar = Avatar(identity, self.provider.get("tags", []))`", with the identity taken from the
  token payload at `:250` "`identity = payload.get(self.provider.get("identity_claim", "email"))`" —
  and `tests/server_app/test_oidc.py::test_callback_authenticates_and_redirects_to_next` proves it.
  Nothing extends that shape to the hosted site.
- the docstring says: no docstring extends the shape to the hosted site
- the handoff of 2026-08-01 says: `temp/design_step1_identita_registri_2026-08-01.md` lines 112-115
  put `user_tags` among the fields the legacy login writes on the connection item, that is, tags
  declared by the site
- the transcript `2390696f-1c5b-4b9d-aaea-370d6eff0d84.jsonl` of 2026-08-24 says: (assistant, 09:59)
  "**il sito dichiara solo l'identity**, e i tag li assegna la configurazione dell'app SPA sul server
  (come fa il provider OIDC) … Consiglio: **b)**"; (user, 10:03) "non è veroi nel metodo ci sono le
  rules non i tag"; (assistant, 10:08) "Il disegno seminato in 050 resta giusto così com'è: al
  sito-autenticato i tag li dà la configurazione server dell'app SPA". The user neither confirms nor
  refuses choice b) after the clarification: the conversation moves on. By the rule of the BRIEF it
  stays an unconfirmed proposal.

