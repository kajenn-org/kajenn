# The `kajenn` command

> **Status:** Draft; implementation checked against the development source on 2026-09-08.

## What it does

Installing the package puts a `kajenn` command on your path. It boots a
server without you writing an entry point, and it keeps a small registry of
named servers so you can start, list and stop them from any shell.

```
kajenn serve <source> [--home D] [--host H] [--port P] [--reload] [--name N] [--debug [PARAMETERS]]
    <source> = ./config.py | template=<name> | application=<target> | <site name>
kajenn configure <name> [--home D] [--template T] [--applications T1,T2] [--host H] [--port P]
kajenn sites
kajenn stop <name>
kajenn remove <name>
```

Everything lives in `kajenn/__main__.py`; the server core knows nothing
about it. `python3 -m kajenn ...` is the same command, useful when the
console script is not on the path.

## When to use it

Use it for development and for a container `CMD`: a `config.py` plus
`kajenn serve` is a complete deployment unit, no `main.py` to maintain. Keep
writing your own Python entry point when the process must do something around
the server — build objects the config cannot express, run migrations first, or
embed the server in a larger program. `.serve()` remains the programmatic way in
and the command adds nothing you cannot do by hand.

## Setup

Nothing to arm. The command ships with the package, and uvicorn — which it boots
under — is already a dependency.

## Serving from a `config.py`

The primary form. A source that is an **existing `.py` path** is handed to
`AsgiServer(config=...)`. Two files in one directory — the application and the
recipe that serves it:

```python
# hello.py
from kajenn import RoutedApplication
from genro_routes import route


class Hello(RoutedApplication):
    mount = ""

    @route()
    def greet(self, name: str = "world") -> dict[str, str]:
        return {"hello": name}
```

```python
# config.py
from kajenn.config import AsgiConfigBuilder

from hello import Hello


class ServerConfiguration(AsgiConfigBuilder):
    def main(self, root):
        cfg = root.configuration()
        cfg.server(host="127.0.0.1", port=8123)
        cfg.applications().application(code="hello", app_class=Hello)
```

What a recipe can contain — the sections, the resolvers that keep secrets out of
it, and how the server reads it back — is the subject of
[Configuration](configuration.md).

The command puts the config file's own directory on `sys.path` before loading
it, so `from hello import Hello` resolves regardless of how the command is
invoked or from where.

```
$ kajenn serve ./config.py
kajenn serving http://127.0.0.1:8123
INFO:     Started server process [72897]
INFO:     Application startup complete.
```

The contract on that file is **the config handler's own**, not the command's:
"must define exactly one `ConfigBuilder` subclass" — in a kajenn recipe
that subclass is `AsgiConfigBuilder`. The command ships no loader of its own,
so a recipe error surfaces as the same boot error you get from
`AsgiServer(config=...)` in a script.

## Serving one application, no config

For a quick run there is the `application=` form, which resolves a class and
hands it to `AsgiServer(applications=[...])` — the server instantiates it with
no arguments:

```
$ kajenn serve application=./hello.py:Hello --port 8124
kajenn serving http://127.0.0.1:8124
```

Two spellings are accepted after `application=`:

- `package.module:ClassName` — a plain import, for an installed or importable
  module;
- `path/to/file.py:ClassName` — a single file, loaded directly, no packaging
  needed.

A target without the `:` separator is an error naming both forms.

`--host` and `--port` are **forwarded as `AsgiServer` kwargs**. The server's own
rule does the precedence — an explicit kwarg wins over the configured value,
wholesale per kwarg — and the command computes nothing.

## Serving a ready-made configuration

`template=<name>` serves one of the configurations the package ships complete
and valid, customised by the command's own options:

```
$ kajenn serve template=default --port 8125
kajenn serving http://127.0.0.1:8125
```

`default` is the only name today; an unknown one is an error listing the known
ones. The form is the CLI face of `AsgiServer(config="<name>")`.

## The site home

A site is a **folder**, and inside it every path the site owns is relative and
named:

```
<home>/
    config.py            the configuration recipe (the card names the file)
    static/              the site's static files (the turn before the 404 is later work)
    data/
        frozen_users/    the deposit of the frozen users
        sessions/        the session snapshots
    sockets/             the worker sockets of an orchestrated site
    logs/                the orchestration log and its decisions journal
    run/                 the pidfile
```

The same site is therefore the same thing in development, in classic
production, in a virtualenv, in Docker and in Kubernetes: only the folder moves.
The home is a configuration word — `site(home=...)`, written by the recipe
attribute `site_home` or by the card — and the server hands it out as
`server.site_home`, a `SiteHome` whose properties are the paths above.

The shipped storage layout is **two volumes**, not one:

- **`site:`** is the site's own folder as the configuration declares it — its
  code and its resources — anchored on the deployment directory;
- **`home:`** is the space the site keeps its own things in — `static/`,
  `data/frozen_users`, `data/sessions`, `sockets/`, `logs/` are paths inside it
  — anchored on the folder the card names. With no home declared it is the
  folder of `site:`.

The pool's own path words (`instance_dir`, the frozen-users deposit, the
orchestration log) are **not** derived from the home: they stay the
configuration words they are.

`KAJENN_HOME` is a different thing: the **installation** root, where
kajenn keeps the site cards and the machine defaults layer. One mechanism,
five values — `~/.kajenn` on a developer's machine, the service user's
folder in classic production, `$VIRTUAL_ENV/.kajenn` in a virtualenv, a path
in the image in Docker, a mounted path in Kubernetes.

## The cards of the configured sites

One card per site, `<KAJENN_HOME>/sites/<name>.json`: the home folder, the
source and the options. Resolving a name means reading its card — no search
order, no precedence.

`configure` writes one. Every question it asks can be given as an option
instead, and with all of them given it runs without a prompt, which is what
Docker and Kubernetes need:

```
$ kajenn configure demo --home /srv/demo --template default \
      --applications myshop.app:Shop --host 0.0.0.0 --port 8080
demo: configured in /srv/demo
```

It lays the home out, writes its `config.py` from the named template, and files
the card. Then the name is a source of its own:

```
$ kajenn serve demo
$ kajenn sites
demo                 running (pid 72897)  0.0.0.0:8080   /srv/demo    config.py
$ kajenn stop demo
demo: stopped (pid 72897)
$ kajenn remove demo
demo: removed
```

An unknown name is an error listing the names that do exist. `--name` on `serve`
still files a card for a server started any other way, and so does the
**configuration itself**: `kajenn serve ./config.py` on a recipe that names
its site (`site_name`, which writes `site(name=...)`) files that site's card, so
the next boot is `kajenn serve <name>`. A configuration that names no site
runs anonymous and files nothing.

A card's relative source is read **inside the home**, so `serve demo` runs
`/srv/demo/config.py` whatever directory you start from.

**`serve` creates nothing.** A name with no card stops with
`shop is not a configured site, run 'kajenn configure shop'`, and a card
whose home is not on disk stops the same way. Laying a home out is `configure`'s
job and nobody else's — a container's mounted volume is prepared by an init step
that runs `configure`, not by the boot.

**Naming an instance also arms the session snapshot**: the sessions of
`--name demo` are pickled to `<home>/data/sessions/demo.pickle` — or
`~/.kajenn/sessions/demo.pickle` when the site has no home — at shutdown
and reloaded at the next boot (expired ones filtered out by their TTL). A
nameless serve stays volatile. This is a development convenience — production
deployments will bring their own persistence. See the
[sessions guide](sessions.md) for details.

The store is `~/.kajenn`: `sites/<name>.json` holds the **card** (the home,
the source string and the options you gave), `run/<name>.pid` the pid of the
running process. It never copies your application, so relaunching by name always runs the
current code. `stop` sends `SIGTERM`; `remove` refuses to drop a registration
while it is running and tells you to stop it first.

**A pidfile is never trusted.** Missing, unreadable, or naming a process that no
longer exists — all three read the same way: not running. A crashed server
therefore shows as `stopped` rather than as a phantom, and `stop` cleans the
stale file up.

## Reloading on source changes

```
$ kajenn serve ./config.py --reload --name demo
```

uvicorn's reload supervisor accepts **only an import string**, never a built
server instance: it starts a fresh process on every restart, and nothing of the
parent survives into it. So the command passes it
`kajenn.__main__:factory` and sends the description of the server across the
process boundary in one environment variable, **`KAJENN_LAUNCHER`** — a JSON
object carrying one source key (`config` or `application`, always an absolute
path), the site's name and home when it has them, plus `host`/`port` *only when
you gave them explicitly*, so an absent key
still lets the config's own value apply. `factory()` reads it and rebuilds the
very same server each time.

You never set `KAJENN_LAUNCHER` yourself; called outside the launcher,
`factory()` says so and stops. The watched directory is the one holding the
source file (a dotted target has no file to anchor on, so the working directory
is watched instead).

The pidfile written under `--name` records the **supervisor**, which is the
process `stop` must signal — the supervisor honours `SIGTERM` and takes its
child down with it, so `stop` behaves identically with and without `--reload`.

## How to verify it

With the `hello.py` above — a `RoutedApplication` with a `greet` route — in the
current directory:

```
$ kajenn serve application=./hello.py:Hello --port 8124 --name quick
kajenn serving http://127.0.0.1:8124

$ curl -s 'http://127.0.0.1:8124/greet?name=cli'
{"hello":"cli"}

$ kajenn sites
quick                running (pid 75171)  -:8124        -          application=./hello.py:Hello

$ kajenn stop quick
quick: stopped (pid 75171)
```

## Gotchas

- **`sites` shows the options you gave, not the address in use.** The registry
  stores the command line, so a host or port that came from the `config.py`
  prints as `-`. The line the server prints on boot
  (`kajenn serving http://...`) is the address it actually bound.
- **A relative source with no home is resolved against the shell you serve
  from.** It is stored on the card as you typed it, so `kajenn serve demo`
  from a different directory will not find a relative `./config.py`. Give the
  site a home (the card's source is then read inside it) or register an
  absolute path.
- **`configure` mounts importable targets only** (`package.module:ClassName`):
  the recipe it writes is a Python file that imports what it mounts, and a
  single-file target has no import line to write.
- **No `--workers`.** The CLI starts one server process (plus a reload supervisor when requested).
  A multiworker SPA, documented in `kajenn-orchestra`, starts its own configured pool. `--debug` declares a usage mode (optionally a comma-separated parameter list);
  the core does not branch on it.
- **Exit codes:** `0` success, `2` argparse usage errors, `1` runtime errors —
  reported as one line on stderr.
- **`--reload` is a development tool.** It costs a supervisor process and a file
  watcher; do not ship it in a container image.
