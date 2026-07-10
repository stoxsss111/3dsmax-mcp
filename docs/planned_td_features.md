# Pipeline TD — Grunt-Work Elimination Features

Each entry below is a tool a TD would run dozens of times a week. Scoped to 3ds Max + MCP automation; cross-DCC bridges (USD/Blender) and asset-library work included.

## Cleanup gauntlets (one-click, multi-step)

- **Import janitor**: after FBX/OBJ/USD import — purge orphan materials, collapse zero-faced meshes, unify duplicate materials by hash, fix gamma on normal/roughness/AO maps, normalize bitmap paths to project-relative.
- **Layer auto-sort**: classify scene by class/material/prefix → reorganize into a layer tree following a convention. Today this is hand-dragging in Layer Explorer.
- **Hierarchy flattener**: unlink helpers, dissolve empty groups, reparent to nearest meaningful ancestor, freeze transforms — driven by a rule file.
- **Pivot fixer**: batch-set pivots to base/center/world-zero across selection with rotation reset. Painful one-by-one; trivial in API.

## Material grunt

- **Material dedupe**: hash all materials by texture set + scalar params, merge identical ones, repoint slots. Scene weight drops 30–80% on imported sets.
- **Texmap repointer**: missing bitmaps → fuzzy-find replacements across project root, present diff, accept-all. The single most universal TD chore.
- **Renderer swap**: bulk convert VRayMtl ↔ PhysicalMaterial ↔ Octane (3 classes) ↔ RS_Standard ↔ MaterialX preserving texture wiring. `palette_laydown` Octane plumbing proves it's possible.
- **Gamma/colorspace audit**: scan every bitmap, flag misclassified (normal map tagged sRGB, etc.), fix in place.

## Selection / find work

- **Smart selector**: "all meshes >50k tris", "all using material X", "all with modifier Y above N", "all instanced more than once" — saved as buttons.
- **Find by example**: pick one object, find all similar (by topology hash, material set, naming, bounding ratio).
- **Diff two scenes**: load two `.max` files (or scene + USD), produce a structured diff — added/removed/moved/material-changed nodes. Massive for review and version sanity.

## Naming / metadata

- **Rename engine**: regex + counter + tokens (`{class}_{material}_{lod}_{##}`), preview before apply, undo-safe.
- **Convention linter**: rule file (layers, prefixes, casing, forbidden chars) → report with one-click auto-fix per row.
- **Metadata stamper**: write `userData` (asset id, version, source path, license, snapshot-safety flag) onto every node from a CSV.

## Geometry chores

- **Instance promoter**: detect duplicate meshes by vertex hash, convert copies → instances. Often 10× scene speedup.
- **LOD generator**: select source → produce LOD1/2/3 via decimation + a `userData.lod` tag.
- **UV channel auditor**: report which channels exist on what, find missing lightmap UVs, batch-unwrap channel 2 with consistent padding.
- **Modifier stack collapser** (rule-based): "collapse everything below Skin", "collapse all TurboSmooth on selection", "preserve modifiers in this whitelist".

## File / project plumbing

- **Asset packager**: scene + every dependency (textures, XRefs, IES, HDRIs, proxies) into a self-contained folder with relinked paths. Replaces "Archive…" which is half-broken.
- **Path repointer**: moved your texture root? Bulk-rewrite across all `.max` files in a folder.
- **Project bootstrapper**: new shot → folder tree, naming-locked `.max`, render config, layer scaffold — all in one.
- **Headless batch**: run any of the above across N `.max` files without opening Max UI (3dsmax-batch + MCP).

## Cross-DCC bridging (Max ↔ Blender ↔ USD)

- **USD round-trip**: Max scene → USD stage → Blender (and back), with material translation preserved.
- **Material translation matrix**: a single tool mapping OpenPBR ↔ VRayMtl ↔ Octane ↔ RS_Standard ↔ MaterialX ↔ USD Preview Surface. Drives both renderer-swap and DCC handoff.
- **Animation/skel transport**: USD Skel export from Max, validated against Blender import.

## Asset ingest & library

- **Watch-folder auto-ingest**: drop a texture set / FBX / USD → palette laydown → asset registered with thumbnail.
- **Proxy generator**: high-res mesh in, decimated proxy + KTX2/Draco-compressed variant out.
- **Texture set sniffer**: smarter than current laydown — detect UDIMs, ACES vs sRGB, missing channels, normal map flavor (DX/GL).

## Scene validation / QC

- **Naming + layer convention linter** (rules in JSON, runs over `get_hierarchy`).
- **Missing-asset / external-ref scanner** (textures, XRefs, proxies, IES).
- **Polycount + instancing audit** — flag uninstanced duplicates, surface candidates for instancing.

## Recommended build order

Highest "minutes saved per week" payoff:

1. Texmap repointer
2. Material dedupe
3. Smart selector
4. Import janitor
5. Scene diff

All are `get_*` + `set_*` / `batch_modify` compositions on tools already shipped.
