# Configure a site by example

A site recipe answers three questions. Which applications answer at which URLs? Which files and databases do they use? Which values change between installations? The application supplies the behaviour and its resource files. The recipe imports that application and supplies its deployment settings.

| What you want to run | Start here |
|---|---|
| A regular JSON API beside a SPA, with per-tab state | [Mount two applications](#mount-two-applications) |
| A Gramlot browser page, an HTTP API and an MCP tool in one application | [Serve a Gramlot workspace](#serve-a-gramlot-workspace) |
| A service that writes records and exports files, without Gramlot | [Configure storage and Asqueel together](#configure-storage-and-asqueel-together) |
| A batch job that reads the same database without a server | [Run Asqueel from a job](#run-asqueel-from-a-job) |
| The available sections, their owners and defaults | [Site recipe reference](site-recipe-reference.md) |

The examples use kajenn 0.10.1. Start in a fresh virtual environment with `python -m pip install "kajenn==0.10.1"`. Each example names its additional packages. The source files shown below are also under `examples/configuration/` in the kajenn repository. Work in the indicated example directory, start one server at a time and stop it with Ctrl-C before moving on. These examples serve a local development site at `127.0.0.1:8000`.

## Mount two applications

In `examples/configuration/visits/`, the catalogue is an ordinary routed application. It returns the label chosen by the installation. The desk is a SPA whose users can open several tabs and save a different draft in each. Both are usable from an HTTP client, so you can observe what the browser session contributes.

**applications.py** · {download}`Download the applications <../../examples/configuration/visits/applications.py>`

```{literalinclude} ../../examples/configuration/visits/applications.py
:language: python
```

**config.py** · {download}`Download the site recipe <../../examples/configuration/visits/config.py>`

```{literalinclude} ../../examples/configuration/visits/config.py
:language: python
```

Run `kajenn serve ./config.py --host 127.0.0.1 --port 8000`. Open a second terminal:

```sh
curl -i http://127.0.0.1:8000/                 # 307, Location: /catalog/
curl -L http://127.0.0.1:8000/                 # {"label":"Books"}
curl http://127.0.0.1:8000/catalog/_meta/mcp/tools
curl -c visit.cookies http://127.0.0.1:8000/desk/new_tab
```

The last response contains a new `page` ID and an empty `text`. Copy the ID into `PAGE` and keep the same cookie jar:

```sh
PAGE='paste-the-page-id-here'
curl -b visit.cookies -X POST -G --data-urlencode "page_id=$PAGE" \
  --data-urlencode 'text=First draft' http://127.0.0.1:8000/desk/save
curl -b visit.cookies -G --data-urlencode "page_id=$PAGE" http://127.0.0.1:8000/desk/read
curl -b visit.cookies http://127.0.0.1:8000/desk/new_tab
```

Reading the first tab returns `First draft`; the second starts empty. Repeat the read without the cookie and the server returns 403. The page ID identifies the tab, while the session establishes which connection owns it. The catalogue's `label` tool needs neither a tab nor its ID.

### What survives and who owns it

| Setting or object | Lifetime | What to put there |
|---|---|---|
| `persistent=True`, the application default | A cached Python instance for the live page; it can be evicted | Objects convenient to keep ready between calls |
| `persistent=False` | A new instance on the next request; repeated lookups in one request reuse it | Request-local computations |
| `page.store` in the core SPA | The logical page across requests and instance eviction | Drafts private to one tab |
| `page.state.connection_store` | The visit, across its tabs | Browser-specific working state |
| `page.state.user_store` | The registered user while present in this registry | Live state shared by that user's visits |
| A file mount or a database | The lifetime of the configured backend | Records needed after a restart |

A page subclass can set `persistent` to override the application's choice. A guest can use this example immediately. If the site mounts the [authentication application](authentication.md), a verified session login reconciles the visit on its next operation. Tabs keep their page IDs. An application rebinding shared data must select the new user's authorized data. Timeouts, closing a page, and closing a connection release different parts of the registry. See [SPA applications and page channels](applications.md).

### Add a login to the site

In the visits recipe, keep the two existing applications. Add the following line to
`applications_section` after importing `AuthApplicationBase` from `kajenn`:

```python
apps.application(code="_auth", app_class=AuthApplicationBase)
```

With the default file auth store, also add this method to `Site` and import
`StorageManager` from `genro_storage`. Before starting the site, set
`GENRO_STORAGE_KEY` to a Fernet key and keep it for subsequent boots.
[Storage encryption](storage.md#optional-encryption) shows how to generate a key.

```python
def storage_section(self, cfg):
    """Preserve the encrypted identity records of this installation."""
    mounts = cfg.storage(app=StorageManager, storage_key=EnvResolver("GENRO_STORAGE_KEY"))
    self.storage_mounts(mounts)
```

Provision a user using the [management guide](management.md). Sign in through
`/_auth` using the same browser session that opened the tabs. The authentication
application verifies the identity; the SPA reconciles ownership when it next
looks up the connection. Mounting `_auth` does not make public business routes
private. Declare the permission required by each protected operation as shown
in the [authentication guide](authentication.md).

### Run the SPA in a dedicated process

Leave the recipe as shown to run both applications in the server process. To give the desk one application process, change only its declaration and restart the site:

```python
apps.application(code="desk", app_class=Desk, persistent=False, spawner="subprocess")
```

The front server may already be listening while the child joins its bus. During that interval the desk returns 503. Once it is ready, run the cookie-and-two-tabs exercise again with a fresh visit. The front server owns the browser session and forwards the visit context. The application process owns the registry, the live Python instances and their stores. Restarting it loses that live state. A larger thread pool gives synchronous handlers more execution slots; it does not replicate the registry. See [application placement](applications.md) for the bus transport.

## Choose a layout for the site

**Applications installed from other repositories.** A deployment directory can contain just `config.py` and its dependency manifest. Install the packages into the same virtual environment as `kajenn`. Import their public application classes in the recipe. For example, if the packages export `Catalog` from `shop_catalog` and `Desk` from `shop_desk`, replace the import in the first recipe with:

```python
from shop_catalog import Catalog
from shop_desk import Desk
```

The packages own their routes, their bundled pages and their application grammar. Their installation instructions should name any database alias, storage mount and configuration parameters the site must supply. The operator chooses the actual database and directory or bucket.

**Applications shipped with the site.** A larger single repository might look like this:

```text
shop-site/
  config.py
  applications/
    __init__.py
    catalog/
      __init__.py       # exports Catalog
    desk/
      __init__.py       # exports Desk
      pages/            # resources packaged with Desk
```

Here the imports are `from applications.catalog import Catalog` and `from applications.desk import Desk`. The CLI makes the recipe directory importable. For a `src/shop_site/` package layout, install the project first (for development, `python -m pip install -e .`) and use its absolute package imports. A mixed site can use one import of each kind. The mounting calls, default application and resource declarations use the same grammar in all three layouts.

## Serve a Gramlot workspace

Use `gramlot_py_server.kajennspa.Application` when an application serves Gramlot pages alongside typed routes. The application owns the pages folder. Its `status` method works before a browser has opened a page.

In `examples/configuration/workspace/`, install `python -m pip install "gramlot-py-server[kajennspa]==0.3.32"`. The folder contains two files.

**config.py** · {download}`Download the workspace recipe <../../examples/configuration/workspace/config.py>`

```{literalinclude} ../../examples/configuration/workspace/config.py
:language: python
```

**pages/index.py** · {download}`Download the page <../../examples/configuration/workspace/pages/index.py>`

```{literalinclude} ../../examples/configuration/workspace/pages/index.py
:language: python
```

Run `kajenn serve ./config.py --host 127.0.0.1 --port 8000`. Open `/work/pages/` for the workspace. Compare these entry points:

| Caller | How it reaches the operation |
|---|---|
| Gramlot page | A page RPC named `workspace_status` calls `self.application.status()` in Python. The initial rendering uses the same method. |
| HTTP client | `GET /work/status` returns `{"workspace":"Editorial"}`. Inspect the schema at `/work/_meta/api/schema_json`. |
| MCP client | Send `tools/list` and then `tools/call` to `/work/_meta/mcp`. The published tool is `status`. |

```sh
curl http://127.0.0.1:8000/work/status
curl -H 'Content-Type: application/json' http://127.0.0.1:8000/work/_meta/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"status","arguments":{}}}'
```

Use a typed application route for an operation a client can perform independently of a tab. Keep selection, drafts and page-specific RPC in the page. A call from Python to a route method is an ordinary method call. Channel filtering and route authorization apply at dispatch, so enforce permissions shared by multiple entry points in the operation or at each entry point.

The Gramlot adapter also supplies the live `PageStore` and its WebSocket. A reconnect with the same valid visit and page ID receives a fresh snapshot for each authorized binding. Offline edits can be replaced by that snapshot. For cross-tab writes, explicitly authorize the shared path and enable writing. See [the Gramlot Server guide](https://gramlot-py-server.readthedocs.io/en/latest/505-kajenn.html#gp-505-050) for binding, identity changes and cleanup. The ordinary `gramlot kajenn gallery` command starts the classic adapter.

## Configure storage and Asqueel together

This site keeps note records in SQLite and exports their IDs to a file. The application asks for a database by its logical name and a file by its mount. The same service could run from an installed package: its contract is a default database containing `app.note` and a writable `reports:` mount. The installation chooses both locations.

In `examples/configuration/notes/`, install `python -m pip install "kajenn-db[asqueel]==0.1.0"`. Prepare a fresh data directory. Use absolute paths so the job and server see the same resources regardless of their working directories:

```sh
mkdir -p data/reports
export NOTES_SQLITE="$PWD/data/notes.sqlite"
export NOTES_REPORTS="$PWD/data/reports"
```

**model.py** declares the Asqueel database grammar. Its environment variable identifies the physical SQLite file. · {download}`Download the model <../../examples/configuration/notes/model.py>`

```{literalinclude} ../../examples/configuration/notes/model.py
:language: python
```

**config.py** declares where kajenn mounts that database and the export directory. · {download}`Download the site recipe <../../examples/configuration/notes/config.py>`

```{literalinclude} ../../examples/configuration/notes/config.py
:language: python
```

**service.py** shows the three access patterns. A synchronous route commits the insert. An asynchronous route awaits the query terminal. A synchronous export writes through the storage manager. End-of-request cleanup closes the database session. · {download}`Download the service <../../examples/configuration/notes/service.py>`

```{literalinclude} ../../examples/configuration/notes/service.py
:language: python
```

### Run Asqueel from a job

**job.py** opens the same model directly with Asqueel. It can prepare an empty SQLite database before the first HTTP request and read committed data after the web server stops. The job owns and closes its database and session. It needs the Asqueel package, the model and `NOTES_SQLITE`; it imports neither kajenn nor Gramlot. · {download}`Download the standalone job <../../examples/configuration/notes/job.py>`

```{literalinclude} ../../examples/configuration/notes/job.py
:language: python
```

Run `python job.py` in the prepared directory. An empty database prints `[]`. Start `kajenn serve ./config.py --host 127.0.0.1 --port 8000`. In a second terminal:

```sh
curl -X POST 'http://127.0.0.1:8000/notes/add?key=n1&text=First'
curl http://127.0.0.1:8000/notes/keys
curl -X POST http://127.0.0.1:8000/notes/export
cat data/reports/notes.txt
```

The query returns `{"keys":["n1"]}` and the exported file contains `n1`. Run the job again with the same environment: it prints `['n1']`. Choose a fresh key for each insert. For a deployed database, run reviewed migrations separately from request handling. See the [Asqueel transaction guide](https://asqueel.readthedocs.io/en/latest/guide/transactions.html) and [the kajenn-db handler guide](https://kajenn-db.readthedocs.io/en/latest/guides/asqueel.html) for named sessions, commits and async query terminals.

### Share a database across applications

In the notes recipe the name in the server registry is `notes:default`. To make the database a site-wide default, put the declaration on `cfg` and remove it from the application's node. The following method replaces `applications_section` in the notes recipe; it uses the same imports.

```python
def applications_section(self, cfg):
    """Both the notes service and future consumers use one site database."""
    cfg.databases(handler_class=AsqueelDbHandler).database(
        code="default", source=str(Path(__file__).with_name("model.py"))
    )
    cfg.applications().application(code="notes", app_class=Notes)
```

An explicit database name chooses that registry entry. Without a name, selection uses the application's configured `db_name`, then its own `<code>:default` if present, then the site's `default`. A database declaration can override the handler of its enclosing section. Asqueel, SQLAlchemy and Django have different handler parameters and transaction APIs. A live page can keep a record ID between calls; acquire the database again for each call. [Database lifecycle and selection](databases.md).

### Replace a local mount with a bucket

For deployments that export to S3, keep the logical name `reports` and replace the notes recipe's `storage_section`. Install `python -m pip install "genro-storage[s3]"` and provision the bucket and its scoped credentials first. This fragment also supplies key material for callers that request encrypted writes.

```python
def storage_section(self, cfg):
    """Use the deployment bucket for exports and retain both built-in mounts."""
    mounts = cfg.storage(app=StorageManager, storage_key=EnvResolver("GENRO_STORAGE_KEY"))
    self.storage_mounts(mounts)
    mounts.s3(
        name="reports", bucket="example-notes-reports",
        access_key=EnvResolver("NOTES_S3_ACCESS_KEY"),
        secret_key=EnvResolver("NOTES_S3_SECRET_KEY"),
    )
```

The application still uses `reports:notes.txt`. Configuring a bucket grants no HTTP route to its contents. A storage key enables encryption; use `node.write_text(text, encrypted=True)` on writes that require it. Keep the key available to subsequent readers. The local recipe requires only a writable directory and performs the same export without cloud credentials. [Storage, mounts and encryption](storage.md).

## Diagnose a recipe

| Symptom | What to inspect |
|---|---|
| The root opens the wrong app | A root-mounted application (`mount=""`) handles `/` directly. Otherwise `applications(default="catalog")` sends a 307 to the named application's mount. Put this decision in the recipe. |
| An installation option is missing or has the wrong type | Put `EnvResolver` at the configuration attribute. Give it a default for optional values and a `dtype` for non-strings. For example, `EnvResolver("SHOP_PORT", default=8000, dtype="L")`. Reading an environment variable at module import freezes that value. A running listener still needs a restart to rebind. |
| The application cannot find an option | A constructor keyword such as `persistent` configures the instance. An application setting written with `app.parameters(label=...)` is read through `self.config("parameters.label")`. A structured domain vocabulary belongs in the application's grammar. |
| A datastore or folder is missing | Verify the database alias, model path, migrations and the absolute, existing local mount directories. A relative Asqueel source is resolved from the process working directory. |
| The page refuses an existing ID | Reuse the same session and visit. Check for closure, expiry, restart or identity change. Durable user records belong in storage or the database. |
| HTTP works but an MCP client sees no tool | Inspect the specific application's `/<mount>/_meta/mcp/tools` with the intended identity. Mark the operation for `api,mcp`. A Gramlot `@endpoint` belongs to page RPC. |
