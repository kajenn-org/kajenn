# Configuration

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/015_configuration.md` — DIVERGENT, 1 CONVERGE / 6 DIVERGE / 36 SILENT.

An installation is described once, in one place: which applications are installed
and where, which databases exist, where files live, who is allowed in and how,
and how much of the machine the server may use. Given the description, the
software assembles itself. The description is more than a settings file on three
counts. It has a **grammar** — every word is declared by the part of the system
that consumes it, and an undeclared word is refused when the description is read.
It is **layered** — written at three levels, package, machine and site, with the
site winning per value. And it is **alive** — it stays as a tree the running
system watches, and changing the tree changes the installation.

Its parts:

- **the recipe** — what you write: a class, one method, calls into a grammar
- **the grammar** — which words exist, declared by whoever consumes them
- **the three layers** — package, machine, site — the site wins, per value
- **the read door** — one call answers any address, falling back four times
- **the live tree** — it can be written while running, and it notifies
- **the sections** — what the server itself declares
