# Applications

**Version**: 0.1 · **Last Updated**: 2026-08-24 · **Status**: 🔴 DA REVISIONARE

Verification: `kajenn-meta/verification/10_server/020_applications.md` — DIVERGENT, 0 CONVERGE / 8 DIVERGE / 43 SILENT.

An application is what sits on the other side of the server's handover: it
receives a request the server has already decided is its own, and produces the
answer. Written from the inside it is a class you subclass — its handlers are its
methods, its address is a class attribute, its configuration words are its own.
Nothing about it is registered in a central table. There are two classes to
subclass: `BaseApplication` is the contract and nothing else, which suits
something that is not a site at all; `RoutedApplication` adds the routing tree, and
that is what an installation normally hosts.

Its parts:

- **the contract** — four things the server requires, four an application declares
- **a routing class** — an application's marked methods are its addressable behaviour
- **the dispatch** — the seven steps between the handover and the answer
- **the request** — what a handler is given
- **the answer** — what a handler returns, and how it becomes bytes
- **the long answer** — when the body does not fit in memory
- **the failures** — the four ways a request does not reach its handler
- **the faces** — one tree, several protocols reading it
