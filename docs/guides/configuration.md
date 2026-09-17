# Configuration

> **Status:** Draft; implementation checked against the development source on 2026-09-08.

## What it does

A configuration is a **recipe**: a Python class that writes down what the site is
— the listener, the middleware, the identity surface, the applications — and a
server built from it reads its own values back by path. There is no config file
format to learn: the recipe is code, checked by a grammar that knows which
elements exist and which attributes each one takes.

## When to use it

Use a recipe as soon as the server is more than a demo: it is the one place a
deployment differs, and `kajenn serve ./config.py` turns it into a complete
deployment unit (see [the `kajenn` command](cli.md)). Building the
server by hand — `AsgiServer(applications=[...])` — is the same road written
shorter: the kwargs become a configuration (see below), so a test or a script
declares what a recipe would have declared.

## Setup

Nothing to install. `kajenn.config` ships `AsgiConfigBuilder` (the dialect
you subclass) and `ConfigurationHandler` (the read door the server builds for
itself); `AsgiServer.grammar` is the grammar they validate against.

## At a glance

```{figure} ../_static/diagrams/configuration-flow.svg
:figclass: flow-diagram
:alt: Recipes layer into a configuration handler; explicit constructor arguments override effective server settings.

Recipe reads and effective constructor settings are separate: overriding port does not rewrite server.config.
```

## The recipe

A recipe subclasses `AsgiConfigBuilder` and overrides `main(self, root)`. `main`
opens the `configuration` root and delegates each section to its own method,
which takes the **parent node**:

```python
from kajenn.config import AsgiConfigBuilder

from myshop.app import Application as Shop


class ServerConfiguration(AsgiConfigBuilder):
    def main(self, root):
        cfg = root.configuration()
        self.server_section(cfg)
        cfg.middleware(cors=True)
        self.applications_section(cfg)

    def server_section(self, cfg):
        """The listener and the session TTL."""
        cfg.server(host="127.0.0.1", port=8000).session(ttl=3600)

    def applications_section(self, cfg):
        """The shop answers the site root."""
        cfg.applications(default="shop").application(
            code="shop", mount="", app_class=Shop
        )
```

Sections are one method each because a section then stays short enough to read
at a glance, and because **the method docstring is where the deployment
explains itself**: the grammar documents what CAN be written, a recipe docstring
documents what THIS instance chose and why. `main` reads as a table of contents.

Every section is a singleton, so its label is its tag and every path below it is
stable and hand-writable: `server.host`, `authentication.credentials`,
`applications.<code>.parameters.<name>`.

## Values that come from outside: resolvers in place

A value the recipe must not contain — a secret, a per-host address — is stored
as a **resolver where the value would go**, and it resolves at read time, so the
runtime always sees the environment's current value:

```python
from genro_bag.resolvers import EnvResolver


def server_section(self, cfg):
    """The port belongs to the host, not to the recipe."""
    cfg.server(
        host="127.0.0.1",
        port=EnvResolver("SHOP_PORT", dtype="L"),
    )
```

The environment gives strings, so a value that is not a string needs
`dtype=` — `dtype="L"` above delivers a real `int` for `port`. There are no
`^pointer` strings in this dialect: the resolver object itself sits in the
attribute.

For secrets this is a convention, not a signature rule: every secret-bearing
attribute accepts a literal and should be given a resolver instead —

```python
cfg.storage(app=StorageManager, storage_key=EnvResolver("SHOP_STORAGE_KEY"))
```

— because a recipe is code you commit, and the key material is not.

## Handing it to the server

`AsgiServer(config=...)` accepts five sources: a recipe **class**, a recipe
**instance**, a **path** to a `config.py`, the **name of a ready-made
configuration**, or a ready `ConfigurationHandler`.

```python
server = AsgiServer(config=ServerConfiguration)          # class
server = AsgiServer(config="/srv/shop/config.py")        # path
server = AsgiServer(config="default")                    # template name
```

## The configuration always exists

A server built with kwargs alone has one too. `AsgiServer(applications=[Shop],
port=8000)` is a **shortcut**: it takes the ready-made `default` configuration
(`DefaultConfiguration`, named in `CONFIGURATION_TEMPLATES`), writes the kwargs
it received into a top layer of its own and runs the same road as a recipe.
`server.config` is a `ConfigurationHandler` here as everywhere, and the tree
carries what a recipe would have written — the `server` section (`debug`
included), the `middleware` and `plugins` switches, and one `application` node
per declared class, so every application reads its own options
(`applications.<code>.request`, and whatever its grammar declares) through the
same door. `applications` entries are CLASSES, or `(class, params)` pairs: the
server instantiates them off the tree, here exactly as for a written recipe, and
an instance is refused by the grammar.

```python
server = AsgiServer(applications=[(Shop, {"mount": ""})], host="0.0.0.0", port=9000)
server.config("server.port")             # 9000 — written into the tree
server.config("applications.shop.mount") # "" — the instance's own placement
```

The `middleware` and `plugins` elements have an OPEN signature, so a switch for
a class registered in code (`middleware_registry=` / `plugin_registry=`) is
written by its own name like any other.

An explicit constructor kwarg **wins over the configured value, wholesale per
kwarg** — the server computes nothing, it just prefers what you passed:

```python
tuned = AsgiServer(config=ServerConfiguration, port=9000)
tuned.config("server.port")   # 8000 — the recipe still says what it said
tuned.config_port             # 9000 — what the server will bind
```

## Layered defaults: `BaseConfiguration` and `default_config`

A site recipe never stands alone: the handler the server builds layers it over
parent recipes, lowest first, the site always last and winning (attribute by
attribute — a section that sets only `port` inherits everything else).

1. **`BaseConfiguration`** — the package's shipped defaults, as a recipe. It
   declares the default storage layout (the `site:` and `home:` mounts) and
   exposes one hook per concern, so the minimal
   deployment is a subclass that sets what deviates:

   ```python
   from genro_bag.resolvers import EnvResolver
   from kajenn import BaseConfiguration


   class Site(BaseConfiguration):
       storage_key = EnvResolver("STORAGE_KEY")   # everything else inherited
   ```

2. **The defaults file** — a recipe the deployment host owns, layered between
   the package defaults and the site. Where it comes from is declared by the
   site recipe itself, through the `default_config` class attribute:

   | `default_config` | meaning |
   |---|---|
   | unset (or `True`) | `<home>/config.py`, layered only when the file exists |
   | `False` | no defaults file — the site sits straight on `BaseConfiguration` |
   | a path | THAT file; a missing path is a loud `ConfigError` at boot |

3. **The site recipe** — always the top layer.

`<home>` here is kajenn's own INSTALLATION directory — the site cards, the
pids, the defaults file, not a site's own home folder — and it resolves as: explicit `base_dir` argument → the **`KAJENN_HOME`**
environment variable → `~/.kajenn`. The variable is how a container or a
virtualenv gets an isolated home (`KAJENN_HOME=$VIRTUAL_ENV/.kajenn`);
nothing is inferred from the environment beyond it. The test suite pins it to
an empty per-test directory, so tests never read a developer's real home.

## Reading it back

The handler is `server.config` and it is **callable by path**:

```python
server.config("server.host")                  # '127.0.0.1'
server.config("server.session.ttl")           # 3600
server.config("openapi.title", default="Shop API")
```

Each read walks four layers, in order:

1. the **written value** — what the recipe put there;
2. the **element's signature default** — `page_size: int = 20` answers 20 even
   when the recipe never wrote it;
3. the **call-site `default=`** — your fallback for a value nobody declared;
4. a **noisy `KeyError`** naming the path and saying which layers were empty:

```
missing config value 'server.nonexistent' (source: 'configuration.server?nonexistent'):
not written by the recipe, no signature default, no call-site default
```

A server built bare — no `config=` — has `server.config is None`.

## What an application reads

An application holds an **address** in the tree, never a slice of it:
`app.config(path)` prefixes `applications.<code>.` and delegates to the same
door, so the two reads below are the same read.

```python
class Themed(RoutedApplication):
    mount = ""

    @route()
    def theme(self) -> dict[str, str]:
        return {"theme": self.config("parameters.theme", default="light")}
```

```python
site.config("parameters.theme")                          # 'dark'
server.config("applications.site.parameters.theme")      # 'dark'
```

Every application inherits one element, `parameters`, whose kwargs are free —
enough for a handful of options. An app with a real vocabulary of its own
declares a **grammar**, and `application(app_class=...)` mounts it for that
node's children: the site dialect never validates an app's internal words, the
app itself declares them.

```python
from kajenn.application import ApplicationGrammar
from genro_builders.builder import element


class ShopGrammar(ApplicationGrammar):
    """The shop's own vocabulary, on top of the inherited ``parameters``."""

    @element(node_label="catalog")
    def catalog(self, title: str = None, page_size: int = 20) -> None:
        """Read back as ``applications.<code>.catalog.<attr>``."""


class Shop(RoutedApplication):
    grammar = ShopGrammar
```

The attributes of the `application` envelope itself (`code`, `mount`,
`app_class`, plus the app's constructor kwargs) belong to the site grammar; only
the children live in the mounted one. An undeclared child is a boot error.

## The sections

One line each; the deep dives live in their own guides.

- **`site`** — the site's identity: `name` (the card it is filed under) and
  `home` (the folder it owns, inside which every path is relative and named).
  A recipe usually declares them as the `site_name` / `site_home` class
  attributes, which write this section; the home anchors the default `home:`
  storage volume, beside `site:`. A configuration that carries the name gets its
  card written on the first `serve <path>`. See
  [the site home](cli.md#the-site-home).
- **`server`** — `host`, `port`, `external_url` (the PUBLIC address, not the
  listener), `max_threads`, `shutdown_timeout_seconds` (default 5.0), plus `websocket`
  (`origins`, `max_concurrent`), `session` (its `ttl`) and `tasks` (see [Background tasks](tasks.md)).
- **`middleware`** — one `{name: bool | dict}` switch per middleware; a dict
  enables it and becomes its options (see [Middleware](middleware.md)).
- **`authentication`** — the whole identity surface in one section:
  the `users`/`tokens` stores, the `login` lockout policy, the
  `oidc` providers and the header `credentials`. The grammar of each is in
  [Authentication](authentication.md).
- **`storage`** — the mount point of [genro-storage](https://pypi.org/project/genro-storage/)'s
  own grammar: `storage_key` plus one child per mount, written in genro-storage's
  words (see [The storage section](#the-storage-section)).
- **`applications`** — the app collection keyed by `code` (an identifier:
  letters, digits, underscore, not empty; the grammar refuses anything else), with the optional
  `default` naming who `/` redirects to.
- **`databases`** — one descriptor per database: `db_class` and its connection
  kwargs; the core never imports a driver.
- **`plugins`** — the router plugins armed on every routed app.
- **`openapi`** — accepted by the grammar but not consumed by the core. Set
  schema title, version and description with the `OpenApiApplication`
  `openapi_info` class attribute instead (see [OpenAPI & Swagger](openapi.md)).
The SPA pool belongs to an application's `orchestration` subtree; it is not a
root section. See [The pool subtree](#the-pool-subtree-orchestration-its-commander-and-its-groups);
the pool itself is documented in `kajenn-orchestra`.

## The storage section

The server's storage is a `genro_storage.StorageManager`, and this dialect
declares **no storage vocabulary of its own**: `storage` is a mount point for
genro-storage's grammar. `app=StorageManager` carries that grammar (required —
the subbuilder reference reads the call site, so it cannot be defaulted), and
the mounts hang directly under the section, one element per protocol, the tag
being the protocol:

```python
from genro_bag.resolvers import EnvResolver
from genro_storage import StorageManager


def storage_section(self, cfg):
    """One local tree for the site, one bucket for uploads."""
    s = cfg.storage(app=StorageManager, storage_key=EnvResolver("SHOP_STORAGE_KEY"))
    s.local(name="site", base_path="/srv/shop")
    s.s3(name="uploads", bucket="shop-media", default_encrypted="shopspa")
```

Omit the section entirely and the server builds its default manager: **two**
mounts, `site:` and `home:`. `site:` is the site's own folder as the
configuration declares it — its code and its resources — on the deployment
directory (the process cwd); `home:` is the space the site keeps its own things
in, on the folder the [site home](cli.md#the-site-home) names, and with no home
declared on the folder of `site:`. Both must already exist — a recipe naming a
missing directory is a boot error.

`site:` is where the server's own state lands, all in one tree: `site:users` and
`site:api_keys` (written `encrypted=True`), `site:sessions`, `site:tasks` and
`site:batches` (plain). Encryption is declared per **write**, not per mount, and
what lands on disk is self-describing — an envelope whose first line starts
`#GNRE1:` — so reads declare nothing.

Outside a recipe the same shapes reach the constructor as `storage=`: `None` for
the default layout, or genro-storage's own `list[dict]` of mount configurations
(which override the shipped ones one by one, so a single declared mount leaves
`home:` standing under it).

## The pool subtree: `orchestration`, its `commander` and its `groups`

A site whose pages live in worker processes declares the whole thing under ONE
node of the front that owns it: `orchestration`. It is not a section of the site
dialect — it belongs to the SPA application's own grammar — so it is written on
the `application` element, never on `cfg`:

    applications → application → orchestration → commander → groups → group

Three rungs carry words: `orchestration` (the profiles and the control surface),
the `commander` (the vertex: one per front) and one `group` per family of
workers. **Nothing in it says how many processes there are**: the group brings
its reception into being at boot, then grows on demand and shrinks when capacity
is spare, so the count is something you read in the log, never something you set.

```python
from kajenn_orchestra.spa_app import SpaApplication


def applications_section(self, cfg):
    """The front, its orchestration, one vertex, two groups on two interpreters."""
    front = cfg.applications().application(
        app_class=SpaApplication, code="shop", mount="",
    )
    orchestration = front.orchestration(
        control_enabled=False,  # runtime profile operations require one group
    )
    commander = orchestration.commander(
        frozen_users_path="/var/lib/shop/frozen_users",
        instance_dir="/var/run/shop",
        orchestration_log_path="/var/log/shop/orchestration.log",
        memory_max_percent=80.0,             # what this server may hold of the machine
        machine_memory_alarm_percent=90.0,   # past this, nothing grows
        user_expiry_hours=720.0,             # a frozen person is kept a month
        guest_expiry_hours=24.0,             # a frozen browser, a day
    )
    groups = commander.groups()
    groups.group(name="stable", worker_memory_admission_percent=80.0,
                 user_idle_freeze_minutes=60.0,
                 cpu_admission_reopen_percent=30.0,   # below this a worker admits again
                 cpu_admission_close_percent=50.0,         # above this it stops taking new users
                 cpu_offload_percent=75.0,      # above this it cedes one user per beat
                 cpu_heating_seconds=1.0,       # the temperature filter, going up
                 cpu_cooling_seconds=5.0,       # and going down: slower on purpose
                 entry_module="kajenn_orchestra.orchestration.worker_entry",
                 worker_class="myshop.app:ShopWorker",
                 worker_kwargs={"site_path": "/srv/shop"})
    groups.group(name="canary", executable="/srv/shop/.venvs/next/bin/python",
                 entry_module="kajenn_orchestra.orchestration.worker_entry",
                 worker_class="myshop.app:ShopWorker")
```

Named profiles, environment overrides and runtime `apply`/`reload`/`status`
currently require exactly one group. The two-group template above uses recipe
settings directly, without a named profile or environment overrides.

**The node is required, and so is the commander under it.** A spa front IS its
pool: one declared without `orchestration` would answer every request with a
raise, so the server does not start and the recipe is asked for the node. Wanting
no pool means declaring no spa front, not declaring one and leaving it hollow.
The same holds one rung down: the node MUST carry a `commander`, because a
profile and a control surface with no pool to act on address nothing. Either way
the boot fails loudly instead of starting half-configured.

**The profiles and the control surface are the node's own.** `profiles_path` is
the folder the stored profiles are read from — the same one the `_sysop` archive
writes — and `profile_name` the profile the boot must find and put in force:
named without a folder, or named and not there, or there and invalid, and the
server does not start. `control_enabled` opens `apply`, `reload` and `status`
under the front's `_orchestration` root; off, that root is never claimed and the
path belongs to the hosted site. The effective configuration of the one group is
composed as **defaults ⊕ recipe ⊕ profile ⊕ env** — `env_settings` being a plain
constructor kwarg of the application, a dict the Python recipe builds out of the
environment, and no word of any grammar.

**The two paths are the installation's**, so they are declared once, on
`commander`: `frozen_users_path` (the freezer — one root for the whole machine,
because the vertex reads back what a worker wrote there) and `instance_dir` (the
sockets). Every group is handed both.

### Group memory percentages

**The memory is a cascade of percentages, and only the machine is measured in
bytes.** `memory_max_percent` on `commander` is the server's concession on the
machine; `memory_max_percent` on a `group` is that group's share of the
concession; `worker_memory_max_percent` is what ONE of its workers may hold of
the group's share. The same word on each rung is deliberate — it always means
"my share of the rung above". The machine's total is read off the platform
itself, so the cascade is always anchored; a machine that does not say how much
of it is IN USE (a `/proc/meminfo` capability) simply alarms nobody.

**The memory keys are a veto, never a choice.** `worker_memory_admission_percent`
(default 80) is the share of its ceiling past which a worker takes no new user,
whatever its CPU says; `restart_occupancy_max_percent` (default 95) is where a
process is replaced rather than kept. Neither picks a worker: the CPU does.

**The CPU picks the worker.** A newcomer goes to the hottest CPU-open worker
that admits him — the group consolidates while a worker still has room under the
close threshold — and a worker that admitted somebody less than
`worker_admission_interval_seconds` ago (default 1) is skipped, so its load shows
in the temperature before the next one lands. When every open worker is in its
window the hottest that admits takes him anyway: the interval orders the walk,
it refuses nobody and births nobody. Nobody estimates what a user will cost: the
gate is the CPU admission, the heads and the memory veto.

### CPU admission thresholds

**The CPU keys are the soft admission, and its brake.** `cpu_admission_close_percent`
(experimental, off when omitted) is the smoothed CPU above which a worker stops
taking NEW users; it reopens below `cpu_admission_reopen_percent`, and between the two
it keeps the state it had — the band is hysteresis. A CPU sample never forks a
process: capacity is created by a concrete arrival that no open worker can
admit. `cpu_retirement_quiet_seconds` (default 60) is the other half: how long
the CPU must stay SILENT — nobody blocked, nobody reopened — before the closure
judge resumes. It is the quiet of the whole GROUP, not the age of one worker
(that is `worker_min_life_seconds`), and every CPU event restarts it whole.
Without it, closing the emptiest worker while demand still stands hands its
users back to the hot one, which regrows seconds later. With the CPU policy off
the brake does not exist at all.

### `cpu_heating_seconds` and `cpu_cooling_seconds`

**The temperature the CPU keys read is filtered.** The commander samples each
worker's CPU every 100 ms; a saturated process reads 0% or 100% on such a short
window, so no judge reads the raw sample. `cpu_heating_seconds` (default 1) and
`cpu_cooling_seconds` (default 5) are the time constants of a first-order filter
the sample goes through: the temperature moves towards the sample by
`1 - exp(-dt/tau)`, with the shorter constant when the sample is hotter and the
longer one when it is colder. A worker heats up in about a second and needs
several seconds of real silence to reopen, so a user it just ceded does not come
back on the next request. The raw sample stays visible in the pool census as
`cpu_temperature_sample_percent`, beside the filtered `cpu_temperature_percent`.

### `cpu_close_percent` retirement

**`cpu_close_percent` is where the pool shrinks.** Past the CPU quiet, the coldest
worker is closed when its temperature, shared by the survivors, keeps every one
of them under this key (unset, the reopen threshold itself; set while the CPU
admission is on, never above `cpu_admission_reopen_percent`),
and its memory, shared the same way, keeps every survivor under
`worker_memory_admission_percent`. A worker with no temperature yet suspends the
judgment. Its users go to the freezer and wake where their next request lands.

### `cpu_offload_percent` user selection

**`cpu_offload_percent` is what makes a hot worker slim down.** Closing the
admission protects the workers to come; it does nothing for the users already
placed on a process that is burning CPU. This key (nullable, `None` by default —
omitted, no user is ever offloaded) is the smoothed CPU above which the group
takes at most ONE user per heartbeat off that worker and puts him in the
freezer. It requires `cpu_admission_close_percent`, and the thresholds are ordered:

    cpu_admission_reopen_percent < cpu_admission_close_percent < cpu_offload_percent <= 100

An offload declared without the admission key, or out of order, is refused at
boot — the ordering is not decoration: the worker must already be closed to new
users, or the ordinary placement could put the offloaded user straight back on
it.

WHO leaves is judged against the interval itself, so there is no absolute
threshold to tune. Over the users with activity in the last interval, with `S`
their summed recent service time and `N` their count, a **material contributor**
is one holding at least half the fair share (`s >= S/(2N)`) or having a request
in flight. Users that are idle or whose activity is negligible against the
window are never candidates — the idle ones belong to `user_idle_freeze_minutes`
instead. Among the material contributors, the one ceded is the least busy of
those with NO request in flight: a user mid-call is never transferred. His next
request goes through the ordinary placement, which skips CPU-closed workers and
creates capacity when no open one can take him. **CPU pressure never restarts a
process** — that remains memory's business alone
(`restart_occupancy_max_percent`).

Two standing conditions are recorded instead of acted on: when only one material
contributor is left the worker is de facto dedicated to him and the group logs
`single_user_overload` rather than moving him; when every material contributor
has requests in flight the cession is postponed to the next heartbeat and logged
as `cpu_offload_deferred_pending_calls`.

**What each user costs is measured, and it is observation only.** Every worker
keeps three cumulative counters per user — `served_call_count` and
`service_seconds`, both stamped whatever the request did (a call that failed or
ran long is exactly the one worth counting), plus `pending_call_count`, the
requests open right now — and puts them in its photo. The group derives from two
consecutive photos the recent deltas the offload judgment reads. These numbers
serve observability and the pool's decisions; they are **not** part of a user's
frozen application state, so a user parked in the freezer and woken elsewhere
carries his store and his connections, never his counters.

**The ages are the vertex's.** `user_expiry_hours` / `guest_expiry_hours` on
`commander` are how long a FROZEN user is kept before the machine forgets him
whole — the vertex holds them because a frozen user lives in no process, and a
group could not notice him. `user_idle_freeze_minutes` (group) is the silence
past which a worker parks a user in the freezer: his state survives on disk, and
his next request brings him back wherever the pool then puts him.

**The identity of the child** is the group's too: `entry_module` (what `python
-m` runs), `executable` (its interpreter — two groups on two venvs is how two
versions of a site serve side by side), `worker_class` (the `module:Class` the
child loads), `main_threadpool_size` / `aux_threadpool_size`, and
`worker_kwargs`, the grammar that class is built with. The group's name and its
`user_idle_freeze_minutes` are added to those kwargs on the way down, so you
write each policy once, on the rung it belongs to.

**What is NOT a key.** No worker count and no maximum. No policy for the
freezer's disk: under a tenth of it free the orchestration log says so and the
server asks its environment for more. And no clocks — the beat, the patience of
a departure and the cadences are module constants, because an installation tunes
policies, not timings.

### Orchestration decision journal

**The account of what the pool does** is `orchestration_log_path` (with
`orchestration_log_max_bytes` and `orchestration_log_backup_count`): one row per
order, saying who decided, what, on whom, with which numbers in front of them and
how it ended.

```
decided_by=std order=start_worker subject=std_0002 numbers={'workers': 2} outcome=None
decided_by=std order=close_worker subject=std_0002 numbers={'occupancy_percent': 7.0, 'workers': 2} outcome=None
decided_by=std order=drop_worker subject=std_0002 numbers=None outcome=quitted
decided_by=vertex order=drop_user subject=mario numbers={'had_state': False} outcome=process_aborted
```

Omit the path and the rows stay on the `kajenn.orchestration.orders` logger,
which is what a test wants.

Beside that human log the commander writes a machine-readable **decision
journal**, `<stem>.decisions.jsonl`: one JSON row per judgment, carrying a stable
reason code and the numbers the judge had in front of it. The offload adds its
own codes, and reading them in order is enough to reconstruct why a user moved
or why none did:

| Reason code | What it says |
|---|---|
| `cpu_offload_threshold` | this worker is past `cpu_offload_percent` and a cession was decided |
| `cpu_offload_user_selected` | who is leaving, with his recent deltas |
| `cpu_offload_completed` | the freeze confirmed; he is in the deposit |
| `cpu_offload_refused` | the ordered departure did not happen; he stays where he was |
| `cpu_offload_no_active_candidate` | nobody on that worker contributes materially |
| `cpu_offload_deferred_pending_calls` | every material contributor has a request in flight; the next heartbeat tries again |
| `single_user_overload` | one material contributor left; the worker is dedicated to him and nobody is moved |

Each row carries the worker's CPU, the window's summed service time and the
number of active users, the computed material threshold, and how many
contributors were material and cedible. The two standing conditions are written
once when they begin, not at every heartbeat.

The pool snippet above is an installation template: it needs your `ShopWorker`,
existing storage paths and interpreters. It is not
a standalone hello-world. The core recipe below has different prerequisites.

## A complete recipe

Server, middleware, an environment secret, and one application with a grammar of
its own. Use this self-contained block as your `config.py`; do not concatenate
the earlier examples, which declare alternative recipe classes. The storage
directory and environment prerequisites are described below.

```python
from kajenn import AsgiServer, RoutedApplication
from kajenn.application import ApplicationGrammar
from kajenn.config import AsgiConfigBuilder
from genro_bag.resolvers import EnvResolver
from genro_builders.builder import element
from genro_routes import route
from genro_storage import StorageManager


class ShopGrammar(ApplicationGrammar):
    @element(node_label="catalog")
    def catalog(self, title: str = None, page_size: int = 20) -> None:
        """The catalog title and page size."""


class Shop(RoutedApplication):
    grammar = ShopGrammar

    @route()
    def index(self) -> dict:
        return {"catalog": self.config("catalog.title")}


class ServerConfiguration(AsgiConfigBuilder):
    def main(self, root):
        cfg = root.configuration()
        self.server_section(cfg)
        cfg.middleware(cors=True, logging=True)
        self.authentication_section(cfg)
        self.storage_section(cfg)
        self.applications_section(cfg)

    def server_section(self, cfg):
        """Bind locally; the public address is what a third party is handed."""
        cfg.server(
            host="127.0.0.1",
            port=EnvResolver("SHOP_PORT", dtype="L"),
            external_url="https://shop.example.com",
        ).session(ttl=3600)

    def storage_section(self, cfg):
        """The site tree, and the key that unlocks what is encrypted in it."""
        cfg.storage(
            app=StorageManager,
            storage_key=EnvResolver("SHOP_STORAGE_KEY"),
        ).local(name="site", base_path="/srv/shop")

    def authentication_section(self, cfg):
        """The identity store: where the records live, and who keeps them."""
        cfg.authentication().users(mount="site", prefix="users")

    def applications_section(self, cfg):
        """One app on the site root, declaring its own catalog block."""
        app = cfg.applications(default="shop").application(
            code="shop", mount="", app_class=Shop
        )
        app.parameters(currency="EUR")
        app.catalog(title="Outlet")
```

## How to verify it

First create the storage anchors — the recipe names `/srv/shop`, and a local
mount whose directory does not exist is a boot error (the rule stated in the
storage section above), so the recipe fails before any read without this step:

```bash
mkdir -p /srv/shop
```

Then, with `SHOP_PORT=8123` and `SHOP_STORAGE_KEY` (a Fernet key) exported,
build the server and read it back through both doors:

```python
>>> server = AsgiServer(config=ServerConfiguration)
>>> server.config("server.host")
'127.0.0.1'
>>> server.config("server.port")            # resolved, dtype="L" → int
8123
>>> server.config("server.session.ttl")
3600
>>> server.config("middleware.cors")
True
>>> server.config("applications.shop.catalog.page_size")   # signature default
20
>>> shop = server.applications["shop"]
>>> shop.config("parameters.currency")
'EUR'
>>> shop.config("catalog.title")
'Outlet'
>>> shop.config("catalog.locale", default="it")            # call-site default
'it'
```

## Gotchas

- **A secret is a resolver, not a string.** The secret-bearing attributes
  (`storage_key`, `client_secret`, `password`, `token`, `secret`) accept a
  literal, and should not get one — a recipe is code you commit.
- **`dtype=` or you get a string.** `port=EnvResolver("SHOP_PORT")` without
  `dtype="L"` hands the server `"8123"`.
- **The identity store needs a key, not just somewhere to write.** Its records
  land under `site:users` written `encrypted=True`, so a recipe declaring
  `authentication.users(...)` and no `storage_key` fails at the first write with
  genro-storage's `Cannot encrypt for encryption domain '': it requires
  installed key material`.
- **`storage_key` lives on `storage`, not on `server`.** It is meaningless
  without the mounts it unlocks; a recipe still passing it to `cfg.server(...)`
  is a boot error naming the attribute.
- **`mount=""` is the site root, and it is not the same as `mount=None`.**
  Omitted, the mount defaults to the `code`; empty, the app answers `/` and every
  unclaimed path.
- **`applications.default` elects nobody.** It names who `/` **redirects to**
  (307) when no application claims the root; naming a code that does not exist is
  a boot error.
- **The recipe is read, not frozen.** A resolver resolves on every read, so
  changing the environment changes what the runtime sees — a configured value
  that looks stale usually means it was copied into a local variable at boot.
- **One recipe class per `config.py`.** The handler's contract is "exactly one
  `ConfigBuilder` subclass in that file"; a second one is an error naming both.
