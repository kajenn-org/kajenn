# Inspector

**Version**: 0.2 · **Last Updated**: 2026-09-07 · **Status**: 🔴 DA REVISIONARE

**The need.** An admin, or a developer chasing a live installation, needs to
look INTO an application: what it holds right now, and how that changes while
they watch.

The answer is a section mounted at `_server/<name>` by the application that
owns the state, through `ServerApplication.attach_section`. The section is a
`RoutingClass` like any other; its endpoints live at `/_server/<name>/...` and
it is enumerable in the `index` descriptor.

It is mounted at `_server` and it is NOT one of the server's own sections.
Whoever reads a runtime state supplies the section that reads it, because a
`_server` app that carried such a section would have to import the machinery
it inspects. The attach point is the whole contract, and the core keeps no
list of the sections that can arrive through it.

The mounting is the gate. An attached section declares no `auth_rule`, so the
decision to install it — an environment variable, a configuration word, a
build — is what admits its reader. Per-route rules on such a section are an
open direction, not part of this seam.

Interactions: server-application · authentication.
