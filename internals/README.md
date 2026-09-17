# Internals — the technical dossier

**Version**: 0.5 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

kajenn's world, read in order. Start from
**[00_overview](00_overview/README.md)** — it explains the documents every entry owns,
the cycle they serve, the rules, and lists everything with one line and a
link.

- [00_overview/](00_overview/README.md) — how to read, the whole building at a glance
- [10_server/](10_server/README.md) — the machine: from BaseServer to configuration, cli and restart

## Reading it as a site

These pages carry diagrams and cross-links, and both read better rendered.
From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[internals]'
python -m mkdocs serve
```

That serves this folder at <http://127.0.0.1:8771/> with navigation, full-text
search and rendered diagrams. It reads this checkout and branch, including
uncommitted changes. Rendering produces a local build; it publishes nothing.
Stop it with **Ctrl-C** in the terminal that started it. The default port stays
fixed so colleagues running their own reader can share deep links.

For a parallel checkout, choose a separate free port:

```bash
python -m mkdocs serve --dev-addr 127.0.0.1:18771
```

Source and test links open numbered local source views. `#L42` selects a line;
a GitHub range such as `#L42-L48` opens at its first line. Source views rebuild
when source files change. Markdown links on disk retain their normal relative
paths for editors and GitHub. Views are restricted to Python files in `src/`
and `tests/`, plus `pyproject.toml`; links outside those boundaries fail the
build. The reader does not provide an arbitrary filesystem browser.

Diagrams need the Mermaid CDN on first load. A palette change redraws them for
light or dark mode. Wide diagrams and source listings scroll within their
panels. See `.mkdocs/README.md` in the checkout for build commands and the full
verification procedure.
