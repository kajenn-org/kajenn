# Inspector — decisions

**Version**: 0.3 · **Last Updated**: 2026-11-03 · **Status**: 🔴 DA REVISIONARE

Everything this feature SHOULD be when finished — the target, not the code.
To be filled by the documentation audit and ratified by the owner.

No ratified decision. See [open_decisions.md](open_decisions.md).

## Still open

**S1 [extension pages] — proposal "Channel faces on every routed application, one _meta segment, and the server GUI as a separate component".**
The proposal `xmldocs/proposal/channels-meta-and-server-gui.md` is dated
2026-10-28 with status `review`. It is on the branch
`docs/channels-meta-server-gui-proposal`, not on develop. Its decision
d-ext-pages keeps `attach_section(section, name)` for the REST routes of an
extension. It adds GUI pages registered on the GUI component, only when the
component exists; `ServerApplication` exposes the component, or `None`. gramlot
is an optional extra of the extension, for example `kajenn-orchestra[gui]`.
design.md describes the attach point and no GUI page. The owner decides
whether the inspector's design names the second registration.
