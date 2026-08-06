# Data Channel and Max Creation Graph

Read this reference completely before building, inspecting, editing, compiling, or verifying Data Channel or Max Creation Graph systems.

## Data Channel

### Tool workflow

- Discover with `list_dc_operators` using a narrow query and `include_properties=true` only when exact live properties are needed; list installed presets with `list_dc_presets`.
- Build and inspect with `add_data_channel`, `inspect_data_channel`, and `load_dc_preset`.
- Edit with `set_data_channel_operator` and `manage_data_channel_stack`; visible operator indexes are always 1-based.
- Use `add_dc_script_operator` only with explicit executable authorization and MCP safe mode disabled.

### Runtime and safety rules

- `add_data_channel` reuses the first Data Channel modifier by default; pass `create_new=true` only for a second stack entry.
- Resolve operator class IDs from the live `NumberOperators`/`OperatorID` catalog; do not hardcode them across Max versions.
- Deletion leaves stale entries in `operators`; inspect and mutate the active stack through `operator_order`, whose values are 0-based storage IDs while UI/operator indexes are 1-based positions.
- Validate a complete reorder permutation before assigning `operator_order`; Autodesk's `ReorderStackOperator` does not bounds-check and unsafe input can destabilize Max.
- Max 2027 stores `operator_ops` as 0=unset, 1=Replace, 2=Add through 7=Cross despite older MAXScript help claiming 0..6; a fresh stack's first Input must be explicitly set to 1 or Max leaves the graph invalid and unevaluated.
- Treat Maxscript, Maxscript Process, and Expression operators as executable code; require explicit authorization and fail closed unless MCP safe mode is disabled.

## Max Creation Graph

### Tool workflow

- Discover and fork with `mcg_get_context`, `mcg_list_graphs`, `mcg_inspect_graph`, `mcg_search_operators`, and `mcg_create_graph`.
- Iterate with `mcg_apply_patch`, which owns the hash check, checkpoint, compile, disposable verification, and rollback transaction; use `mcg_compile_graph`, `mcg_test_tool`, and `mcg_restore_checkpoint` for explicit stages.
- Clean session workspaces with `mcg_cleanup_workspace`; `mcg_reload_operators` is an explicit global operation and is not part of the normal loop.

### Runtime and safety rules

- `ProceduralAsset.Validate(StringBuilder)` returns void in Max 2027 and throws on invalid graphs; catch it, then use `CompileGraph(path, StringBuilder, false)` for deterministic compile diagnostics.
- Preserve both the root graph UUID and `graph_version` GUID during edits; regenerate both only when forking because the root UUID determines the generated class ID in Max 2027.
- Source ports are `value=0` and `function=1`; resolve destination input indices by name from the live Viper depot because compound input order follows graph traversal, not XML order.
- Fresh scripted classes may be invisible to native `create_object`; disposable verification resolves the exact generated class from the `.ms` wrapper and instantiates it in MAXScript.
- Fresh scripted modifier classes may be invisible to generic native `add_modifier`; use `mcg_apply_modifier`, which resolves the exact `Class_ID`, verifies `pluginGraph`, and assigns only typed scalar `INode` PB2 parameters before stack insertion.
- `mcg_list_graphs` scope tokens are `session`, `samples`, `installed`, and `all`; `samples` is plural.
- The bundled Autodesk 2017 sample corpus is read-only reference material and contains only `.maxtool` and `.maxcompound` XML; never ship its `Scenes` or `Packages`, and always fork a graph into the temporary workspace before compilation.
- Native `create_object` class lookup may reject `GeoSphere`; use the supported `Sphere` primitive or MAXScript only when a true geosphere is required.
- Treat `EvalMAXScript`, nonblank `<customui>`, and executable compound dependencies as code-execution surfaces; block them by default and never override MCP safe mode.
- Keep a compiled graph and its dependencies alive for the session because generated plug-ins retain the source path; Max 2027 emits `.ms` but normally no `.txt` diagnostic graph.
- Group nodes use `groupnode="..."` with a nested `<nodes>` comma list; nested groups are valid, but cycles and multiple direct memberships are not.
- Do not live-probe a new MCP tool by starting a second server while the active server owns Max's native named pipe; the second process falls through to TCP and can report `BRIDGE_DOWN` even though the active bridge is healthy.
- Max 2027's global refresh API is the parameterless `Viper3dsMaxBridge.Main.ReloadOperators()`; do not invent a diagnostics overload such as `ReloadOperatorsWithMessages`.
- Treat live-catalog `Impure` operators as unsafe, not diagnostic warnings: disposable verification can execute scene-mutating operators such as selection, parenting, or cloning outside the probe object's cleanup boundary.
- Offline `OperatorMetaInfo.xml` does not declare Viper impurity; mark it unknown and fail closed until the live operator depot confirms whether semantic verification is scene-safe.
- Keep patch, compile, verify, and rollback in one serialized transaction and roll back only against that iteration's `after_hash`; recomputing the current hash can overwrite a newer successful edit.
- Require caller `expected_hash` for manual checkpoint restore, and never restore across a hash conflict even when a safety checkpoint exists.
- Reparse the exact serialized bytes before atomic replace and validate node-attribute XML names; ElementTree can otherwise emit invalid XML for bad keys or control characters.
- Terminal Output connections use destination port `0`; numeric-but-nonzero terminal ports are structurally invalid even before Viper type validation.
- In C++ files that include MaxScript headers, use `json::value_t` checks instead of nlohmann `is_array()`/`is_string()` because MaxScript defines colliding function-like macros.
- Preserve native error codes through Python wrappers by parsing bridge response fields and JSON embedded after prefixes such as `MAXScript error:`; otherwise specific guards degrade to `BAD_PARAM`.
- `Extrude` (`QuadMesh from Extruded Points`) treats `direction` as the per-segment offset, not total height; wire `floor_height` into direction and `floors` into `segmentCount`.
- Geometry verification quotes string parameters, so `Parameter: INode` cannot be set through `verification.parameters` name strings; compile with `verify=false`, then assign nodes in MAXScript (`obj.ground_spline = $Footprint`).
- Tool parameter names with spaces become underscore PB2 names in the generated `.ms` (`ground spline` becomes `ground_spline`).
- For facade windows on extruded wall shells, use `ToTriMesh` with hidden edges, then `Mesh Extrude All Polygons` with positive inset and negated depth; assign `materialId` for glass panes on a Multi/Sub.
- Embree operators (`RayTraceScene`, `RayTraceAddGeometry`, face-intersection variants) are `impure: true` and blocked under MCP safe mode even with `allow_executable`.
- Pure scene-node ray hits (`GetRayToSurfaceIntersections`, `UpdatePositionsWithIntersections` / `NodeIntersectsRay`) may compile but fail at evaluate with `-- Type error: Call needs function or class, got: undefined`; prefer `Ray` / `RayFromMatrixAxis` with `CloneMeshAlongRay` / `SampleAlongRayWithSpacing` for safe-mode ray demos.
- A literal placed in a `Constant` node's XML `name` attribute parses as String and fails type-checking; agent-authored graphs should use typed constant operators instead (`FloatZero`, `FloatOne`, `One`, `Pi`, `HalfPi`, macros like `DivideByTwoFloat`).
- Validation requires every exposed `Parameter:` node to reach the terminal, so land parameters and their consumers in the same `mcg_apply_patch` transaction — a parameter staged for a later patch fails the whole patch.
- Higher-order operators (`MapRange`, `Combine`, `SplineFromFunction`) accept a lambda by wiring the chain-end node's function output (`source_port: 1`); the chain's unconnected inputs become the lambda's arguments in order.
