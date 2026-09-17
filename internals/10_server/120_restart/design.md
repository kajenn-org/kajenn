# Soft and hard restart

**Version**: 0.2 · **Last Updated**: 2026-09-08 · **Status**: 🔴 DA REVISIONARE

**The need.** The server must be restartable — urgently or gently — without betraying the people working on it at that moment.

Stopping and restarting a living server without losing what must
survive: the restart liturgy (hard/soft at the decider's choice, notice via
notify_user, delegation to ServerApplication, execv) and `dump`/`restore`
across a full server restart.

Interactions: sessions (their snapshot exists already) · applications, through the shutdown and startup hooks.

## What restart owns, and what it delegates

Server restart owns stop/serve, execv and the soft boot of the base. An
application that holds live state of its own saves it in `on_shutdown` and
reads it back in `on_startup`. The server carries the hooks and the reason it
is stopping; what survives is the application's decision, entry by entry.

That division is what keeps the restart liturgy one mechanism. A recovery that
lives above the server — a supervisor rebuilding a branch, a scheduler
replacing a process — adds its own layer on top of this one and changes
nothing here.
