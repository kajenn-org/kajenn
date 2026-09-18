# kajenn brand assets

## Current logos

| Asset | Preview | Use |
| --- | --- | --- |
| [Standalone logo](kajenn-mark.png) | ![kajenn symbol](kajenn-mark.png) | Symbol without lettering; 1254 × 1254 transparent PNG |
| [Logo with wordmark](kajenn-logo.png) | ![kajenn logo with wordmark](kajenn-logo.png) | Symbol and selected lowercase lettering; 1145 × 1374 transparent PNG |

The logo direction and lighter lettering were selected by the project owner on 2026-09-18. Preserve proportions, colors and transparent margins. The complete wordmark is intended for light backgrounds.

## Shared graphic coordination

- [Visual identity and application theme](theme-guide.md): the authoritative location for the shared design proposal, in English.
- [Visual reference sheet](theme-reference.html): an offline reference with logos, palette, component samples and the complete guide.
- [Theme tokens](theme-tokens.json): proposed reusable values, not an installed runtime theme.
- [Contrast checks](contrast-check.json): checks of the listed opaque color pairs.

Extensions reference this guide rather than duplicating its palette and component rules. The brand assets are approved; interface colors, typography and geometry remain proposals to validate on the first working screen.

## Asset constraints

These are raster assets, not SVG masters. The wordmark is lettering rather than an identified font. Its visible dark-pixel bounds measure 615 × 168 px on the 1145 × 1374 canvas; matching between related raster variants is approximate, not pixel-identical. A common lettering master, small-size favicon and monochrome exports remain to be prepared.

`references/kajenn-logo-original.png` preserves the superseded heavier wordmark; it is not the current logo. Files in `proposals/` are exploratory material, not canonical assets.

## Light and dark backgrounds

The root README uses `<picture>` with `prefers-color-scheme: dark` to select
[`kajenn-logo-dark.png`](kajenn-logo-dark.png). The existing
[`kajenn-logo.png`](kajenn-logo.png) remains the light-mode fallback.

The dark variant uses light lettering on an opaque charcoal background intended
for GitHub's default dark theme. It is not a transparent export: other dark themes
may show a visible rectangular background. These raster variants share the same
1145 × 1374 canvas; they are visually matched, not pixel-identical masters.
The standalone symbol asset is unchanged.
