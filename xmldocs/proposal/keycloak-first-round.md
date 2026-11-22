<!-- generated from keycloak-first-round.xml by xmldocs: do not edit, edit the XML -->

## Keycloak in kajenn: the base and the first round
*Proposal · proposal/keycloak-first-round · 2026-11-22 · review*

### Problem

Applications on kajenn authenticate people against a shared Keycloak. Meetlot (`kajenn-webrtc`) does it today with its own code. kajenn must offer it to every application, with or without Keycloak.

This document is the one place where the decisions live. Issues, pull requests and notes point here; when they disagree with it, this document wins. Each decision says who took it: **owner**, or **delegated** (a default chosen by the developer on the owner's request, to be confirmed).

### Current state

- `TEST` `src/kajenn/auth_app/oidc_method.py` · 2026-11-22 — On branch `feat/keycloak-first-round` (PR #165) an OIDC login finds a user only by the `iss` + `sub` link, and a provider is off while a user of the store is not linked to its issuer.
- `READ` `kajenn-authlot/docs/model/README.md` · 2026-11-22 — Authlot (draft, not deployed) declares `authlot.person_application` with `local_user_id` and `local_username` per person and application, and states that no link is made automatically by email.
- `READ` `kajenn-authlot/src/kajenn_keycloak/keycloak_reader.py` · 2026-11-22 — Authlot reads the Keycloak Admin REST API with a service account (`client_credentials`); it writes nothing.

### Proposed solution

#### The base: start from aligned stores

The scenarios are limited on purpose.

1. **Start empty or aligned.** A site born with Keycloak has no user that Keycloak does not know: every person enters from Keycloak and is born linked. A site that adds Keycloak later runs a realignment tool first: it creates (or finds) the Keycloak account of every kajenn user and writes the link both ways. Until every user is linked the provider stays off.
2. **Stay aligned.** From then on kajenn keeps the two stores aligned when it creates, changes or disables a user.
3. **Repair.** An interactive repair tool finds misalignments and problems and asks a person to decide each one.

Step 1 (the gate) is in PR #165. The realignment tool, the alignment of step 2 and the repair tool are not written yet; they are the base the rest builds on.

#### Where the administration lives

`services.softwell.it` is one kajenn site with several applications on different routes: the recipe `SoftwellSite` of `softwell-services` mounts `_auth`, `/clients` and the signing service (today `/connectors`: it signs site connectors for GenroApp instances, which call it with an instance token; it may sign other things later); Authlot becomes one more mount of that site and shares its `_auth` (owner, reported by the softwell-services session, 2026-11-22).

All administration has a GUI in Authlot, mounted at `services.softwell.it` (owner, 2026-11-22). The realignment and the repair tools are used from there. Authlot's plan names `auth.genro.dev` for the users' portal (`d-portal-host`): to verify whether the two addresses stay separate.

#### Who decides what

Keycloak says who the person is. `_auth` (users, groups, tags, tokens) and the applications decide what the person can do. The `_auth` user record is the source of truth for the person's data.

### Decisions

- **d-who-what** `decided` — Who decides what a person entering with Keycloak can do?
   1. **Keycloak says who; _auth and the applications decide what. Keycloak groups do not become tags**
   → Choice: **Keycloak says who; _auth and the applications decide what. Keycloak groups do not become tags** (owner, 2026-11-20)
- **d-lookup** `decided` — How does a login find the kajenn user of a Keycloak person?
   1. **By iss+sub, then preferred_username, then verified email. Withdrawn: a username can be chosen by another person, and _temporary_admin was among the candidates**
   2. **Only by iss+sub. Never by username or email (the email lookup of 0.10.0 is removed too)**
   → Choice: **Only by iss+sub. Never by username or email (the email lookup of 0.10.0 is removed too)** (owner, 2026-11-22)
- **d-gate** `decided` — When can a provider be used?
   1. **Only when every user of the store is linked to its issuer (system users and _temporary_admin do not count). Otherwise start and callback answer 403 and login_methods leaves it out. A computed condition, no switch**
   → Choice: **Only when every user of the store is linked to its issuer (system users and _temporary_admin do not count). Otherwise start and callback answer 403 and login_methods leaves it out. A computed condition, no switch** (owner (rule); delegated (computed condition), 2026-11-22)
- **d-admin-gui** `decided` — Where is the administration?
   1. **A GUI in Authlot, mounted at services.softwell.it, for all administration**
   → Choice: **A GUI in Authlot, mounted at services.softwell.it, for all administration** (owner, 2026-11-22)
- **d-realign** `open` — Realignment tool: what it does with each kajenn user and how passwords move. Its GUI is in Authlot (d-admin-gui); which part runs in kajenn and which in Authlot is to design.
   1. **kajenn exposes what the tool needs on _auth (users, links); Authlot talks to the Keycloak Admin API with a service account, as its reader does, and drives the realignment; to be designed**
- **d-reverse-link** `open` — Where does the reverse link (Keycloak person → kajenn user) live? Issue #163.
   1. **An attribute on the Keycloak user, one per application**
   2. **Authlot's person_application table (local_user_id, local_username)**
- **d-app-rules** `decided` — Can two applications on one server have different registration rules?
   1. **Yes: the application passes its rules (registration yes or no, status and tags of new users) to _auth, server side**
   → Choice: **Yes: the application passes its rules (registration yes or no, status and tags of new users) to _auth, server side** (owner, 2026-11-20)
- **d-app-rules-where** `decided` — Where are the rules of an application written?
   1. **BaseApplication.self_registration in the application code (PR #165, delegated): superseded**
   2. **Only in Keycloak client attributes, set in Authlot (Authlot d-client-policy): superseded as the only place**
   3. **In the configuration of the application, genropy and kajenn alike: a keycloak section whose presence turns the feature on, with meaningful children. Its values are the defaults; Authlot takes them as initial values and the administrator changes them there**
   → Choice: **In the configuration of the application, genropy and kajenn alike: a keycloak section whose presence turns the feature on, with meaningful children. Its values are the defaults; Authlot takes them as initial values and the administrator changes them there** (owner, 2026-11-22)
- **d-config-syntax** `decided` — Syntax of the keycloak section.
   1. **keycloak(url) with meaningful children: client(realm, client_id, client_secret); selfregister(tags) — present: self registration admitted, absent: not admitted; invite(expires, by, first_steps, return_to); phone(collect: no|optional|required, otp); username(chosen_by: person|inviter, when_taken: ask|counter); shared_data (to define with Authlot). genropy: the package node in instanceconfig packages carries url and the children; kajenn: the same element under the application in the recipe. A missing child means the application defaults (selfregister and invite: not admitted); a missing keycloak node means no Keycloak login for that application. registration(admitted, status, ...) of the first draft is replaced by selfregister and invite**
   → Choice: **keycloak(url) with meaningful children: client(realm, client_id, client_secret); selfregister(tags) — present: self registration admitted, absent: not admitted; invite(expires, by, first_steps, return_to); phone(collect: no|optional|required, otp); username(chosen_by: person|inviter, when_taken: ask|counter); shared_data (to define with Authlot). genropy: the package node in instanceconfig packages carries url and the children; kajenn: the same element under the application in the recipe. A missing child means the application defaults (selfregister and invite: not admitted); a missing keycloak node means no Keycloak login for that application. registration(admitted, status, ...) of the first draft is replaced by selfregister and invite** (owner (structure, selfregister, invite); delegated, to review (parameter names of invite), 2026-11-22)
- **d-selfregister** `decided` — What happens to a person who registers by itself?
   1. **Always born waiting in the application, with the tags of selfregister; it cannot use the application until an administrator approves it. When it signs in before the approval it gets a message saying an administrator must approve it, not a bare refusal**
   → Choice: **Always born waiting in the application, with the tags of selfregister; it cannot use the application until an administrator approves it. When it signs in before the approval it gets a message saying an administrator must approve it, not a bare refusal** (owner, 2026-11-22)
- **d-invite** `decided` — What is an invitation?
   1. **The administrator creates the user in the application first, with its tags. At that moment its Keycloak account is created, or an existing one is found, and the link is written both ways: no user is ever unlinked, so the provider gate holds. The invitation is the email Keycloak sends to complete first_steps; it ends on return_to**
   → Choice: **The administrator creates the user in the application first, with its tags. At that moment its Keycloak account is created, or an existing one is found, and the link is written both ways: no user is ever unlinked, so the provider gate holds. The invitation is the email Keycloak sends to complete first_steps; it ends on return_to** (owner, 2026-11-22)
- **d-invite-removal** `decided` — An invitation is revoked or expires: what is removed?
   1. **The application user and its Keycloak account. The Keycloak account is removed only when it was born with that invitation and is linked to no other application; otherwise only the application user and its link are removed**
   → Choice: **The application user and its Keycloak account. The Keycloak account is removed only when it was born with that invitation and is linked to no other application; otherwise only the application user and its link are removed** (owner (both); delegated, to review (account shared with other applications), 2026-11-22)
- **d-rules-current-value** `decided` — After an administrator changes a rule in Authlot, which value does a registration use and where is it read?
   1. **Authlot writes the change into the attributes of the Keycloak client (Admin API; Authlot's keys authlot.*). The application reads them when it registers a new person; its keycloak section is the default until Authlot wrote. A change applies from the next registration on: users already registered are not touched**
   2. **Authlot rewrites the configuration of every site and instance**
   → Choice: **Authlot writes the change into the attributes of the Keycloak client (Admin API; Authlot's keys authlot.*). The application reads them when it registers a new person; its keycloak section is the default until Authlot wrote. A change applies from the next registration on: users already registered are not touched** (owner, 2026-11-22)
- **d-local-user-create** `open` — Who creates the local user of a new person? kajenn: \_auth at the callback. Authlot (d-client-create): an endpoint of the application, called during registration.
   1. **_auth at the callback (implemented in #165)**
   2. **An endpoint of the application, called by Authlot**
- **d-known-user-new-app** `decided` — A known user enters through another application that admits registration: does it get that application's tags?
   1. **Yes, the tags it lacks are added**
   → Choice: **Yes, the tags it lacks are added** (delegated, to review, 2026-11-20)
- **d-app-enabled** `decided` — What if the application of a login is disabled or removed?
   1. **Checked at start and at the callback; 403 before the user is touched**
   → Choice: **Checked at start and at the callback; 403 before the user is touched** (owner (approved review fix), 2026-11-22)
- **d-person-data** `decided` — Where do name, email and photo live?
   1. **In the _auth user record (firstname, lastname, photo next to email), answered as avatar.data on every request. At every Keycloak login the empty fields take the claims; a filled field is kept**
   → Choice: **In the _auth user record (firstname, lastname, photo next to email), answered as avatar.data on every request. At every Keycloak login the empty fields take the claims; a filled field is kept** (owner, 2026-11-20)
- **d-login-mode** `decided` — With Keycloak, does the kajenn password stay?
   1. **Each server chooses: login(password=False) turns it off; gak_ tokens stay valid; _temporary_admin keeps its password while it exists. Authlot states the opposite (every app works with its password when Keycloak is down): to align when Authlot resumes**
   → Choice: **Each server chooses: login(password=False) turns it off; gak_ tokens stay valid; _temporary_admin keeps its password while it exists. Authlot states the opposite (every app works with its password when Keycloak is down): to align when Authlot resumes** (owner, 2026-11-20)
- **d-app-client** `decided` — Can an application have its own Keycloak client?
   1. **Yes (login page branding, azp check). The client of an application is one more provider of _auth; its secret stays in _auth**
   → Choice: **Yes (login page branding, azp check). The client of an application is one more provider of _auth; its secret stays in _auth** (owner (yes); delegated, to review (as a provider), 2026-11-20)
- **d-logout** `decided` — What does Esci close?
   1. **One command: the kajenn session of the server (every application); not the Keycloak login. The page offers a button to sign in again. POST only; an Origin other than the scheme, host and port of external_url gets 403**
   → Choice: **One command: the kajenn session of the server (every application); not the Keycloak login. The page offers a button to sign in again. POST only; an Origin other than the scheme, host and port of external_url gets 403** (owner, 2026-11-20)
- **d-link-direction** `decided` — Two-way link in the first round?
   1. **It is part of the base (realignment), not later work: see d-realign and d-reverse-link**
   → Choice: **It is part of the base (realignment), not later work: see d-realign and d-reverse-link** (owner, 2026-11-22)
- **d-programs** `decided` — How do programs enter?
   1. **gak_ tokens; Keycloak access tokens for MCP clients come later**
   → Choice: **gak_ tokens; Keycloak access tokens for MCP clients come later** (owner, 2026-11-20)
- **d-desktop** `decided` — Desktop sign-in.
   1. **In kajenn: state desktop.<port>.<state>, relay to http://127.0.0.1:<port>/oidc/callback, nothing added to the authorization URL, the app side stays in each desktop app. Booklet xmldocs/booklet/desktop-sign-in**
   → Choice: **In kajenn: state desktop.<port>.<state>, relay to http://127.0.0.1:<port>/oidc/callback, nothing added to the authorization URL, the app side stays in each desktop app. Booklet xmldocs/booklet/desktop-sign-in** (owner (in kajenn now); delegated, to review (names and path), 2026-11-20)
- **d-callback-checks** `decided` — Checks of the callback.
   1. **nonce; iss of the response (before the desktop relay too); session_state accepted; error answered 400; azp; session cookie when the login is the first request; credential cache emptied after a login that wrote the user**
   → Choice: **nonce; iss of the response (before the desktop relay too); session_state accepted; error answered 400; azp; session cookie when the login is the first request; credential cache emptied after a login that wrote the user** (delegated; review fixes approved by the owner, 2026-11-22)

### Consequences

- A site with users not linked to a provider has that provider off until the realignment tool exists and has run.
- An OIDC login no longer links an existing user by email (breaking for 0.10.0 sites that relied on it).
- Meetlot changes after #165: its KeycloakMethod follows the new start and callback.
- The open decisions d-local-user-create and d-reverse-link meet Authlot's model: decide them before Authlot writes anything.
- PR #165 changes: BaseApplication.self\_registration and the client of an application as an extra provider of \_auth give way to the keycloak section of the application in the recipe.
- genropy-keycloak: to verify that the package loader accepts children under a package node of instanceconfig; the rule "the node turns the feature on" replaces its decision d-enable (GNR\_AUTH\_PKG).

### Related documents

- `booklet/desktop-sign-in`
