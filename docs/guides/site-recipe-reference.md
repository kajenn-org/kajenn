# Site recipe reference

Use this map when adding a line to a recipe: find the parent in the tree, identify the component that owns the child, then check its settings below. For complete files to run, see [Configure a site by example](configuration-by-example.md).

## The configuration tree

Each of the eight top-level sections may appear at most once. Collections use stable names: applications, databases and plugins use `code`; storage mounts use `name`.

```text
configuration
├── site
├── server
│   ├── session
│   ├── tasks
│   ├── websocket
│   └── kbus
├── middleware
├── storage                      StorageManager.grammar
│   └── local, memory, s3, …     one child per named mount
├── databases
│   └── database
├── applications
│   └── application              app_class.grammar
│       ├── parameters
│       ├── request
│       ├── databases → database
│       ├── plugin_configuration → configure
│       └── application-specific children
├── plugins → plugin
└── openapi
```

`DefaultConfiguration.main` calls four hooks: `site_section`, `server_section`, `storage_section` and `applications_section`. Defining a method called `databases_section` by itself has no effect. You can add a top-level database in `applications_section`, since its argument is the configuration root. Alternatively, override `main` and call all the sections you want to keep:

```python
def main(self, root):
    cfg = root.configuration()
    self.site_section(cfg)
    self.server_section(cfg)
    self.storage_section(cfg)
    self.databases_section(cfg)       # method supplied by this recipe
    self.applications_section(cfg)
```

Give each section a docstring explaining the installation choice. A recipe file passed to `kajenn serve` defines exactly one configuration subclass; imported application classes and an imported base configuration are fine. The package defaults, an optional defaults recipe, and the site recipe form successive layers. A later value wins for the same attribute. See [layering and reading values](configuration.md) for `default_config`, constructor shortcuts and resolver evaluation.

## Identity, listener and sessions

| Element | Settings | Effect when omitted or changed |
|---|---|---|
| `site` | `name`, `home` | The recipe's `site_name` and `site_home` attributes can supply them. A named site can be filed for subsequent CLI launches. The default home mount follows the deployment directory when no separate home is set. |
| `server` | `host`, `port`, `external_url` | `kajenn serve` uses command-line overrides, then the recipe address, then loopback and port 0 (OS-assigned). `--port 8000` gives a predictable URL. The external URL is the public address used for links and callbacks. The default template reads `KAJENN_EXTERNAL_URL`. |
| `server` | `max_threads`, `shutdown_timeout_seconds`, `debug`, `abort_on_fatal_boot_error` | The sync pool uses the executor default unless sized. Shutdown waits 5 seconds by default. Debug defaults off and may carry application-specific modes. A fatal startup error normally aborts; with the flag false the management app remains available and readiness fails. |
| `server.session` | `ttl`, `store_class`, extra store parameters, `save_path` | The standard in-memory store defaults to 3600 seconds. A configured snapshot path can retain sessions on orderly shutdown. It does not serialize SPA page instances or their stores. |
| `server.tasks` | `enabled=True`, `tick_seconds`, `mount` | The default scheduler tick is 30 seconds. The task store uses the `site` mount unless configured otherwise. |
| `server.websocket` | `origins`, `max_concurrent` | By default browser origins must match the host. The default limit is 16 concurrently handled messages per connection. It does not set the number of users or tabs. |
| `server.kbus` | `address`, `certfile`, `keyfile`, `cafile`, `max_frame`, `max_meta`, `max_route`, `max_pending`, `stream_window`, `write_buffer` | A subprocess application uses a private local socket by default. Configure TLS material for a network bus; omitting limits retains the transport defaults. |

See [CLI and site homes](cli.md), [session stores](sessions.md), [tasks](tasks.md), [WebSockets](websockets.md) and [bus transport](../design/kbus-protocol.md).

## Applications, middleware and routes

| Element | Settings | What the runtime uses |
|---|---|---|
| `applications` | `default` | Names the application for the root redirect if no application handles the root. |
| `application` | `app_class`, `code`, `mount`, constructor keywords | Supply the imported class. Use a stable identifier for the code. The mount defaults to the code; an empty mount claims the site root. Class or deployment options include `db_name` and, for a SPA, `persistent`. Set `spawner="subprocess"` on the individual application for dedicated placement. |
| `application.parameters` | Any application-defined keywords | Read using `self.config("parameters.name")`. To validate a structured vocabulary, extend `ApplicationGrammar` and assign it to the class. |
| `application.request` | `body="decoded"` or `"raw"`; `error_codes="strict"` or `"fastapi"` | The default decodes the payload and returns 400 on validation errors. Raw mode passes `body_raw`; FastAPI-style validation returns 422. A `kajennspa` Gramlot dispatcher selects raw bodies for its pages branch while typed sibling routes retain normal decoding. |
| `application.plugin_configuration` → `configure` | Parent `plugin`; child `route` plus plugin options | Configure relative route paths (or comma-separated paths). Applies installation-specific channel or authorization policy to routes. |
| `middleware` | `security_headers`, `errors`, `logging`, `cors`, `session`, registered names | A Boolean switches a layer; a dict enables it with those options. SPA visits require the session layer. |
| `plugins` → `plugin` | Collection switches or `plugin(code=..., enabled=True, **options)` | Arms router plugins. OpenAPI and Pydantic on a routed app are fixed core plugins. See the [configuration guide](configuration.md) for install-time overrides. |
| `openapi` | `title`, `version`, `description` | Accepted by the site grammar. Set the routed application's `openapi_info` to change its generated schema. |

The management application `_server` is built in. Its grammar can configure `security_txt` and `robots`; see [well-known documents](hidden-paths.md). Identity is supplied by the optional `_auth` application. In kajenn 0.10.1 its configuration includes `cache_ttl` (default 300 seconds), `store(store_class=..., **params)`, `login(max_attempts=..., backoff=...)`, `oidc().provider(...)` and `self_registration(status=..., tags=...)`. The default file auth store uses encrypted records under `site:auth`. Configure storage key material before using it. See [authentication](authentication.md) for the current login flows, provider credentials and permission rules.

## File storage

| Element | Required or typical settings | Default and ownership |
|---|---|---|
| `storage` | `app=StorageManager`, optional `storage_key` | kajenn mounts the genro-storage grammar. A comma-separated Fernet key list encrypts with the first key and can decrypt with the listed keys. |
| `local` | `name`, absolute existing `base_path`; optional `base_url`, `permissions`, `default_encrypted` | The standard recipe supplies `site:` (process working directory) and `home:` (site home or working directory). Use `self.storage_mounts(section)` to keep them when adding mounts. |
| `s3` | `name`, `bucket`; optional `base_path`, `region`, `anon`, `access_key`, `secret_key`, `endpoint_url`, `version_aware`, `permissions`, `default_encrypted` | The backend comes from `genro-storage[s3]`. Supply credentials through the deployment; use the endpoint option for a compatible service. |
| Other protocols | Protocol-specific attributes on the mount element | genro-storage supplies in-memory, remote, archive and relative mounts. Their requirements and dependencies belong to that library. |

Application code opens a named path with `self.server.storage.node("reports:notes.txt")`. A synchronous handler can use the node directly. In async code, move blocking file work into a synchronous helper and use `await self.server.run_sync(helper)`. A mount may define the encryption default, and each write can select it explicitly. A mount supplies a file API; serving those bytes over HTTP requires an application route. [Storage operations and encryption](storage.md).

### Other file backends

The following are the other mount elements in the genro-storage 0.8.5 grammar. Every row also takes a required `name`. Where declared, `default_encrypted` defaults to `False` and `permissions` to `None`. Fields in the last column default to `None` unless a value is shown. A protocol may require a separate backend package or delegated mount.

| Element | Required besides `name` | Additional options |
|---|---|---|
| `memory` | — | `base_path` |
| `base64` | — | — |
| `gcs` | `bucket` | `base_path`, `token`, `project`, `endpoint_url` |
| `azure` | `container`, `account_name` | `account_key`, `sas_token`, `connection_string` |
| `http` | `base_path` | — |
| `smb` | `host`, `share` | `base_path`, `username`, `password`, `domain`, `port` |
| `sftp` | `host`, `username` | `base_path`, `password`, `port`, `key_filename`, `passphrase`, `timeout` |
| `webdav` | `url` | `username`, `password`, `token`, `cert`, `verify_ssl` |
| `zip` | `file` | `mode`, `target_protocol`, `target_options` |
| `tar` | `file` | `compression`, `target_protocol`, `target_options` |
| `libarchive` | `file` | `target_protocol`, `target_options` |
| `git` | `base_path` | `ref`, `fo` |
| `github` | `org`, `repo` | `sha`, `username`, `token` |
| `relative` | `path` | — |

## Database registration

| Element | Settings | Runtime meaning |
|---|---|---|
| `cfg.databases` | `handler_class` | Default handler for site databases; may also supply the handler for application declarations. |
| `app.databases` | Optional `handler_class` | Local default handler for that application. A database registered here gets the qualified name `<app-code>:<db-code>`. |
| `database` at either level | `code`, optional `handler_class`, backend parameters | The individual handler takes precedence. kajenn passes the remaining parameters to it. Use a registry name with `_request.db(name)` or `_request.async_db(name)`. |
| Asqueel handler | `source`, optional `parents` and `db_class` | Use a supported Asqueel recipe such as an absolute path to a Python model. The model owns connection, schema, packages and tables. Provision the schema separately. |
| SQLAlchemy or Django handler | SQLAlchemy URL or Django alias, handler-specific options | Install the matching kajenn-db extra. Let the chosen handler own session creation, async support and cleanup. |

When several mounts use the same handler, selection still happens per database and per call. A successful response does not commit a transaction implicitly. A live SPA, a scheduled import and a regular HTTP service can use the same durable database; only the web request obtains a request-scoped session. See the [standalone job and service](configuration-by-example.md#run-asqueel-from-a-job), [selection and cleanup](databases.md) and the [configuration API](../api/config.rst).
