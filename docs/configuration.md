# Configuration is part of the application

> **Version:** 1.0 · **Last updated:** 2026-09-18 · **Status:** Draft, pending review.

The [quick start](getting-started.md) builds a server with a few constructor
arguments. For a larger site, kajenn uses a **configuration recipe**: Python
that builds a structured configuration tree, checked against a grammar.
The server and its components then read their settings from that tree.

This matters beyond the listener's host and port. Storage, applications and
internal capabilities can own a configuration vocabulary. A site recipe brings
those vocabularies together without putting every component's settings into one
central schema.

## Why a recipe instead of YAML?

YAML is a data format; it can describe nested settings well, and a framework can
add schema validation, defaults and environment substitution around it. kajenn
chooses Python recipes so configuration can use the same composition tools as
the components being configured: imports, class references, methods and reusable
sections.

| Concern | YAML with a configuration loader | kajenn recipe |
| --- | --- | --- |
| Structure | Mappings and lists, interpreted by the loader | Named elements and allowed children declared by grammars |
| Validation | Supplied by a schema or the consuming code | Grammar checks for declared elements and attributes; open parameters remain the component's responsibility |
| Reuse | Loader-specific includes, templates or application code | Python imports, helper methods and recipe inheritance |
| Component selection | Usually a name or import path resolved by the loader | A class reference, such as `app_class=Shop` |
| Deployment values | Loader-specific environment support | Resolvers such as `EnvResolver`, including value conversion |
| Extension | Extend the schema or loader conventions | Compose a component grammar or mount it at an extension point |

The benefit is a configuration language that grows with the application,
while retaining one tree to inspect and read. Grammar signatures also declare
defaults and document the supported vocabulary next to its owner.

A recipe executes Python: use trusted deployment code. It is not a format for
accepting arbitrary configuration programs from untrusted users.

## From calls to a configuration tree

In a recipe, `cfg.server(port=8000).session(ttl=3600)` declares a `server`
element with a `session` child. It does not start a listener or create a session.
`AsgiServer(config=ServerConfiguration)` consumes the resulting configuration to
assemble the server.

The configuration can then be read by path:
`server.config("server.session.ttl")`. An application reads relative to its own
subtree, for example `shop.config("catalog.page_size")`. Reads use an explicitly
configured value first, then the grammar's default, then a call-site default if
provided. Even the quick start's constructor shortcut produces a configuration
handler; it is not a separate configuration mechanism.

## A site with several components

This example combines a listener, sessions, tasks, middleware, two storage
mounts, a database and an application with its own catalog grammar.

`ShopDatabase` is a **project-provided database class**, imported from your own
package; it is not included in kajenn. The example assumes that its constructor
accepts `dsn` and `password`. Use the class and parameters required by your actual
database integration. The core registers and wraps the instance; it does not
provide a database driver or create tables.

```python
from myshop.database import ShopDatabase

from genro_bag.resolvers import EnvResolver
from genro_builders.builder import element
from genro_routes import route
from genro_storage import StorageManager
from kajenn import AsgiServer, RoutedApplication
from kajenn.application import ApplicationGrammar
from kajenn.config import AsgiConfigBuilder


class ShopGrammar(ApplicationGrammar):
    @element(node_label="catalog")
    def catalog(self, title: str = None, page_size: int = 20) -> None:
        """Settings owned by the shop's catalog."""


class Shop(RoutedApplication):
    grammar = ShopGrammar

    @route()
    def index(self) -> dict:
        return {
            "catalog": self.config("catalog.title"),
            "page_size": self.config("catalog.page_size"),
        }


class ServerConfiguration(AsgiConfigBuilder):
    # Make this example independent of ~/.kajenn/config.py.
    default_config = False

    def main(self, root):
        cfg = root.configuration()
        server = cfg.server(
            host="127.0.0.1",
            port=EnvResolver("SHOP_PORT", dtype="L"),
        )
        server.session(ttl=3600)
        server.tasks(enabled=True, tick_seconds=1.0)
        cfg.middleware(logging=True)

        storage = cfg.storage(app=StorageManager)
        storage.local(name="media", base_path=EnvResolver("SHOP_MEDIA"))
        storage.local(name="exports", base_path=EnvResolver("SHOP_EXPORTS"))

        cfg.databases().database(
            code="main",
            db_class=ShopDatabase,
            dsn=EnvResolver("SHOP_DB_DSN"),
            password=EnvResolver("SHOP_DB_PASSWORD"),
        )

        shop = cfg.applications().application(
            code="shop", mount="", app_class=Shop,
        )
        shop.parameters(currency="EUR")
        shop.catalog(title="Seasonal selection")


server = AsgiServer(config=ServerConfiguration)
```

Before constructing the server, install your project's database integration,
create the two local storage directories and set `SHOP_MEDIA` and `SHOP_EXPORTS`
to their absolute paths. Export `SHOP_PORT` (for example `8000`), `SHOP_DB_DSN`
and `SHOP_DB_PASSWORD` with values for your deployment. Keep credentials outside
the recipe. The storage mounts here are ordinary local mounts, without encryption.

The omitted `page_size` is supplied by `ShopGrammar`: reading
`server.applications["shop"].config("catalog.page_size")` returns `20`.
The `media` and `exports` mounts are available to the server's storage manager;
merely declaring a mount does not expose its files over HTTP.

## Each component can own its vocabulary

Three distinct mechanisms are visible in the example:

- **Composed grammar:** the server grammar incorporates `TaskGrammar`, which
  declares the `tasks` element consumed by the internal task capability.
- **Mounted grammar:** `storage(app=StorageManager)` uses the storage manager's
  grammar for its children, including `local`. Likewise, `app_class=Shop` mounts
  `ShopGrammar`, making `catalog` a valid child of this application. Another
  application can define a different vocabulary without changing the core.
- **Constructor parameters:** `database(db_class=ShopDatabase, ...)` passes its
  additional parameters to the database class. This element does **not** mount
  a database grammar automatically; the database integration owns validation of
  those parameters.

A configurable internal object can therefore have a companion grammar, but
simply adding a `grammar` attribute to an arbitrary object does not register it.
The enclosing grammar must compose it or provide a mounting point. That explicit
boundary keeps extensions discoverable and gives each component ownership of its
settings. An undeclared child such as `shop.warehouse(...)` fails unless the
application grammar declares it.

Adding a new catalog setting means extending `ShopGrammar` and consuming that
setting in `Shop`; the server's grammar need not change. The same pattern lets
other components contribute their own structured sections where the parent
supports them.

## Where to go next

See the [configuration guide](guides/configuration.md) for recipe loading,
layered defaults, resolvers, constructor overrides and extension details.
A value being readable through configuration does not imply it can be changed
live: reconfiguration depends on how the consuming component uses that value.
Then continue with [core concepts](concepts.md) for the server and application
model.
