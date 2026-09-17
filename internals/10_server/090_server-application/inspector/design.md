# Inspector

**Version**: 0.2 · **Last Updated**: 2026-09-07 · **Status**: 🔴 DA REVISIONARE

**The need.** An admin, or a developer chasing a live installation, needs to look INTO the SPA front: which groups exist, who lives where.

The section that answers at `_server/inspector`
(`kajenn_orchestra/inspector_section.py`, landed 2026-08-21 with commit 4097420,
moved out of the server sections on 2026-09-07): what the front's pool holds,
as an admin read surface.

It is mounted at `_server`, and it is NOT one of the server's sections: it
reads a pool, so it lives in the SPA world and the SPA front attaches it on
its own startup, when `GNR_ASGI_INSPECTOR` is set. That is what lets the core
carry no orchestration — a `_server` app that imported the section would
import a commander to look at it.

Interactions: server-application · spa-application · orchestration.
