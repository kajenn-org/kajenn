<!-- generated from live-applications.xml by xmldocs: do not edit, edit the XML -->

## Applications added, switched off and removed on a running server
*Proposal · proposal/live-applications · 2026-11-08 · accepted*

### Problem

The set of applications is fixed when the server is built. To intervene on a running server, for example to mount a debug application, use it and take it away, the server must be restarted.

The design of the server asks for the opposite: installing and removing an application are operations on a running machine, not reasons to restart it. The configuration is meant as a live tree that notifies whoever watches it. Neither exists in the code (kajenn-org/kajenn#75).

### Current state

- `READ` `src/kajenn/server.py:253` · 2026-11-08 — `register_application` is called only while the server is built. Its docstring says the set of applications is fixed at construction. No method removes an application.
- `READ` `src/kajenn/application.py:236` · 2026-11-08 — An application accepts its owning server once; a second assignment raises `RuntimeError`.
- `READ` `src/kajenn/config/handler.py:80` · 2026-11-08 — `ConfigurationHandler` only reads the configuration tree. Nothing subscribes to it.

### Proposed solution

#### Server and application: who does what

The server keeps the state of each application and answers for it. The application is responsible for behaving well: it talks to the server and tells it what it needs to know.

- The server holds the state of each application, disabled or enabled, answers for a disabled one, and takes the signals the applications send.
- The application retries what it needs (an external service, a database), says when it is ready, closes its own calls in progress when switched off, and removes itself when it gives up.

#### Two states: disabled and enabled

An application is either disabled or enabled. There is no error state. An application that fails removes itself, and the reason stays in the log.

*What a client receives*

| Situation | Answer |
|---|---|
| Being mounted, not ready yet | 503 with `Retry-After` |
| Switched off | 503 without `Retry-After` |
| Being removed | 503 without `Retry-After` |
| Removed | 404 |
| Enabled | The application answers |

#### Mounting: disabled first, enabled on ready

1. The `application` node is written into the live configuration.
2. The server, subscribed to the `applications` branch, builds the application and registers it disabled.
3. The application reaches its working state, retrying on its own if something it needs is missing, and sends `ready`.
4. The server enables it. An application that gives up removes itself.

Without an override, the base class sends `ready` as soon as the activation of the application ends. An application that must wait for something overrides it.

Every application follows this road: those added while the server runs and those declared in the recipe at boot. The boot is the same mounting, once per declared application.

#### Switching off and removing

- Switching off is an operation of its own. The application becomes disabled, new requests receive 503, the calls in progress reach their end, and the instance stays registered.
- Switching on makes the same instance enabled again, without building it again.
- Removing first switches the application off, then takes it away when the application says it has finished. After that the mount answers 404.
- An application is never moved to another mount. It can be switched off and on, or removed and mounted again.

#### Every signal travels on the KajennBus

An application may run in the process of the server or in a process of its own (`spawner="subprocess"`). Every signal between an application and the server is therefore a KajennBus `call` to a route of `_server`, whatever process the application runs in. A `call`, not a `send`: the application receives the answer of the server, including a refusal.

#### The applications section of _server

*Routes under /_server/applications/*

| Route | Called by | Effect |
|---|---|---|
| `add` | administrator | Writes the node and builds the application disabled |
| `ready` | the application | Enables it |
| `disable` | administrator | Switches it off |
| `enable` | administrator | Switches it on again |
| `remove` | administrator, or the application itself | Switches it off, waits for the application to finish, removes it |
| `list` | administrator | The applications, with their state and the capabilities they bring |

`add` receives what the `application` node of a recipe holds: the import path of the application class, its `code`, and the extra parameters handed to its constructor. What is added is always an application class.

Every route of the section requires the administrator role. The rule is applied through the route plugins, with no mechanism of its own.

#### Mandatory applications and capabilities

- The `application` node takes the attribute `mandatory`. A mandatory application is one without which the server can do none of its work. When it fails, the server logs it and stays up for `_server` only: every other application answers 503, and the readiness probe `/readyz` answers not ready. The administrator intervenes through `_server`.
- An application declares the capabilities it brings. While it is not enabled, those capabilities are missing.
- A route requiring a missing capability answers 503, as the routing register already records. What to do about it is the logic of each application, case by case. The server takes no action on a missing capability.

#### Not in this proposal

- Persistence. Whoever adds an application will choose whether the change survives a restart. The persistent case needs an extra configuration file holding the updates, and is designed later. Until then a hot change lasts until the restart.

### Decisions

- **d-validate** `decided` — When is a hot-added application checked?
   1. **Before the node is written**
   2. **After the node is written, inside the subscription**
   3. **Mounted disabled, enabled when it works, removed otherwise**
   → Choice: **Mounted disabled, enabled when it works, removed otherwise** (GP, 2026-11-08) — The application is built and registered disabled; it is enabled once it is in working state, and removed otherwise.
- **d-answers** `decided` — What does a client receive from an application that is not enabled?
   1. **503, with Retry-After only while it is being mounted; 404 once removed**
   2. **404 in every case**
   → Choice: **503, with Retry-After only while it is being mounted; 404 once removed** (GP, 2026-11-08) — A client tells an application on its way in from one on its way out, and both from one that is gone.
- **d-ready-who** `decided` — Which applications send ready?
   1. **Every application, hot-added or declared at boot**
   2. **Only those that declare they need it**
   → Choice: **Every application, hot-added or declared at boot** (GP, 2026-11-08) — One rule for every application; the boot is the same mounting.
- **d-signal-road** `decided` — How does an application signal the server?
   1. **A Python call on the server**
   2. **A KajennBus call to a route of _server**
   → Choice: **A KajennBus call to a route of _server** (GP, 2026-11-08) — An application can run in a process of its own; everything goes through the KajennBus.
- **d-retry** `decided` — Who retries when something an application needs is missing?
   1. **The application**
   2. **The server, through a readiness check of the application**
   → Choice: **The application** (GP, 2026-11-08) — The server only waits for the signal.
- **d-give-up** `decided` — What does an application do when it stops retrying?
   1. **It removes itself**
   2. **It goes into an error state and stays registered**
   → Choice: **It removes itself** (GP, 2026-11-08) — No error state is kept; the reason stays in the log.
- **d-mandatory** `decided` — What marks an application whose failure matters?
   1. **The attribute mandatory on the application node, alongside the capabilities**
   2. **Only the capabilities other applications require**
   → Choice: **The attribute mandatory on the application node, alongside the capabilities** (GP, 2026-11-08) — The administrator can mark an application mandatory even when no other application needs it.
- **d-mandatory-fails** `decided` — What does the server do when a mandatory application fails?
   1. **It stops**
   2. **It stays up**
   3. **It stays up for _server only: the public part answers 503 and the server reports not ready**
   → Choice: **It stays up for _server only: the public part answers 503 and the server reports not ready** (GP, 2026-11-09) — Without a mandatory application the server can do none of its work; it stays alive only so that \_server can answer. Refines the choice of 2026-11-08, It stays up.
- **d-capabilities** `decided` — Who acts when a capability is missing?
   1. **The server, by not enabling the applications that need it**
   2. **Nobody: the routes requiring it are unavailable, and each application decides**
   → Choice: **Nobody: the routes requiring it are unavailable, and each application decides** (GP, 2026-11-08) — Its routes are simply not available; the logic of the application decides case by case.
- **d-switch-off** `decided` — Is switching off an operation of its own?
   1. **Yes, and it is the first step of removing**
   2. **No: switching off means removing**
   → Choice: **Yes, and it is the first step of removing** (GP, 2026-11-08) — Removing passes through switching off, which lets the calls end and blocks new ones.
- **d-move** `decided` — Can an application be moved to another mount?
   1. **No**
   2. **Yes, as an operation of its own**
   → Choice: **No** (GP, 2026-11-08) — It can be switched off and on, not moved.
- **d-persist** `decided` — Does a hot change survive a restart?
   1. **Whoever adds the application decides**
   2. **Never**
   3. **Always**
   → Choice: **Whoever adds the application decides** (GP, 2026-11-08) — The persistent case needs an extra configuration file with the updates; it is designed later.
- **d-routes** `decided` — Which routes does \_server expose?
   1. **Section applications: add, ready, disable, enable, remove, list**
   → Choice: **Section applications: add, ready, disable, enable, remove, list** (GP, 2026-11-08) — One section for the whole life of an application.
- **d-auth** `decided` — Who may call the routes of the applications section?
   1. **One rule: the administrator role on every route**
   2. **Two rules: an application may call ready and remove on its own code**
   → Choice: **One rule: the administrator role on every route** (GP, 2026-11-08) — One rule for now, applied through the route plugins.
- **d-add-payload** `decided` — What does add receive?
   1. **The class path, the code and the extra parameters, as the application node of a recipe**
   → Choice: **The class path, the code and the extra parameters, as the application node of a recipe** (GP, 2026-11-08) — It says what, under which name and with which extra parameters; what is always an application class.
- **d-ready-base** `decided` — Does the base class send ready at the end of the startup of an application that adds nothing?
   1. **Yes: an application overrides it only when it needs more**
   2. **No: every application writes its own call**
   → Choice: **Yes: an application overrides it only when it needs more** (GP, 2026-11-08) — Without an override the application sends ready as soon as its activation ends.

### Consequences

- The set of applications is no longer fixed at construction. The server gains a road to remove an application.
- The server subscribes to the `applications` branch of the live configuration. It is the first subscriber of that tree.
- Every application, at boot too, answers 503 until it sends `ready`.
- The design of the server stops listing the move of an application to another prefix among the operations on a running server.

### Related documents

- `proposal/channels-meta-and-server-gui` — The _meta segment and the faces of every routed application, released together
