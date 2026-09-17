# Routing system

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/025_routing-system.md` — DIVERGENT, 0 CONVERGE / 1 DIVERGE / 27 SILENT.

A routing class turns a class into a tree, and it does it by reading the class
rather than by being told: a method carrying the route marker becomes a node named
after the method, and the nodes together are the class's routing tree. Nothing is
registered anywhere, and there is no list of paths to keep in step with the code.
Three properties follow. A tree is **assembled from parts** — a routing class
written on its own is attached below a name and its routes hang there. A walk is
**filtered** on three independent axes: the caller's tags, the installation's
capabilities, the channel a request arrived through. And a tree can be **read as
well as walked**, describing its own nodes, parameters and options. A **plugin**
is what produces a filter or a description, armed on the tree without editing the
tree, its handlers, or the server.

Its parts:

- **the tree** — built from the class, grown by attaching others
- **the walk** — how a path finds a node, and the three filters on it
- **the description** — what a tree can say about itself, and who reads it
- **what a plugin is** — the thing that produces a filter or a description
- **where plugins come from** — three sources, one namespace
- **the fixed pair** — what is structure, and what is a choice
- **when a tree is armed** — once, on the first look after installation
- **what a site writes** — the section, and the options beside a route
- **writing one** — the base class, the five hooks, and how it is installed
