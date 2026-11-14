# Sims 4 Creator Guide (The Sims 4 + Sims 4 Studio + Blender)

Working knowledge for building houses from floorplans/photos and making premium custom
objects. Served to Claude by the `creator_guide` tool and the `s4kit://guide` resource.

---

## 1. Units & scale (applies everywhere)

| Thing | Value |
|---|---|
| 1 tile | 1 x 1 m in game ≈ 1 Blender unit |
| Quarter tile | 0.25 m (objects snap here with Alt off; walls never do) |
| Wall heights | Short / Medium (default) / Tall – roughly 2.75 / 3.0 / 3.5 m in our blockouts; measure a cloned wall in Blender if you need exact values |
| Door | 1 tile wide (single), 2 tiles (double / wide arch), ~2.1 m high |
| Window | 1 tile (most), 2–3 tiles for wide/picture windows |
| Lot sizes | 20x15, 20x20, 30x20, 30x30, 40x30, 40x40, 50x50, 64x64 |
| Real → Sims | Real houses get "Sims-ified": round room sizes to whole tiles, add ~1 tile to tight rooms (Sims route poorly in < 2-tile hallways) |

Walls always run along grid lines. Diagonal walls are possible in game but are not produced by
the floorplan tools – add them by hand.

## 2. Houses from floorplans

### Floorplan image (scan, listing image, screenshot)
1. `floorplan_analyze(image, meters_across=<outer width in m>)`. Read the width from the
   plan's dimension text; without it scale is guessed from wall thickness (flagged).
2. Look at the preview (`floorplan_preview`) and the ASCII map. Check:
   - Rooms count & sizes match the plan (± 1 tile is normal from snapping).
   - Openings: `D` door, `W` window, `A` archway, `?` = exterior opening needing a decision.
   - If walls are missing: re-run with `wall_thickness_px` (measure a wall in pixels) or
     `invert=True` for light-on-dark plans.
3. If detection is poor (hand sketches, perspective photos, furniture-heavy plans), switch to
   `floorplan_from_rooms` and describe the rooms yourself.
4. `blender_build_house(plan.json)` for a 3D blockout to check proportions; render a thumbnail.
5. Hand the user `<name>_build_guide.md`: lot size, wall runs with tile lengths, door/window
   positions, rooms to floor.

### Photo of a house / description
1. `view_image` the photo. Estimate the footprint: count windows (≈ 1 per 2–3 m of facade),
   doors (≈ 1 m), storeys (≈ 3 m each), garage door (≈ 2.5–5 m).
2. Decide a believable room layout behind the facade: entry/living at the front door, kitchen
   near the back, bedrooms on the quiet side, a bathroom per 2 bedrooms.
3. Call `floorplan_from_rooms` with integer tile rectangles (rooms may share edges; they must
   not overlap). Use `between` doors for internal doors and `room+side` for exterior.
4. Exterior style tips: match roof pitch (Build > Roof, pitch slider), use foundations for
   raised porches, frieze/trim for Victorian/colonial looks, half-walls + railings for porches.

### In game (Build Mode) tips
- `testingcheats true`, `bb.moveobjects on` (free placement), `bb.showhiddenobjects`,
  `bb.showliveeditobjects`, `bb.enablefreebuild` (build on locked lots),
  `bb.ignoregameplayunlocksentitlement` (career rewards).
- Place foundation first if wanted; walls on foundations follow its height.
- Rooms: double-click floor/wall tools inside a room to paint the whole room; Shift-click a wall
  pattern to paint all walls of the room.
- Save to Library (Tray) – then `sims4_list_tray` / `sims4_backup_tray_item` can back it up.

## 3. Premium custom objects (S4S + Blender)

### Pipeline
1. **Pick a clone** in S4S: *Object → Create 3D Mesh*. Choose an EA object with the **same
   footprint, function and placement** (floor/wall/surface/ceiling) as your design: routing,
   slots and interactions come from it. Save as a new package (`<Creator>_<Item>.package`).
2. **Export mesh**: Meshes tab → *Export Mesh* → `.blend` for each LOD (High detail first).
3. **Model in Blender** (`blender_open` the exported file):
   - Keep the S4S objects, rig and names. Model your mesh then join it into the S4S mesh group
     (or duplicate the group and replace geometry) so its custom properties/cut number survive.
   - Real-world proportions at 1 BU = 1 m. Bottom at Z = 0 for floor objects; wall objects sit
     against the wall plane of the clone.
   - Two UV maps: `uv_0` texture, `uv_1` shadow/light. Use Lightmap Pack for `uv_1`.
4. **Prepare** with `blender_prepare_object(category=...)`, fix issues it reports.
5. **LODs** with `blender_make_lods` (LOD1 ≈ 40–50 %, shadow ≈ 10 %). Import each into the
   matching S4S LOD slot. If the shadow silhouette matches the clone, keep the clone's shadow.
6. **Save `.blend`** and *Import Mesh* in S4S for each LOD.
7. **Textures** (Studio tab): export the clone's diffuse to see layout, import your PNG
   (power-of-two). Import specular; use the clone's material state for glass/metal shaders.
   Add swatches by *Add swatch* and importing a texture per colour.
8. **Catalog** (Warehouse/Studio): name, description, price, catalog tags (function + room +
   style), colour tags per swatch. Strings can also be edited with `package_set_string`.
9. **Thumbnail**: `blender_render_thumbnail` gives a clean transparent render; S4S can also
   regenerate thumbnails in game when `localthumbcache` is cleared.
10. **Test**: `mods_install` → `sims4_clear_cache` → `sims4_enable_mods` → `sims4_launch`.
    Check: catalog search, placement/footprint, all swatches, lighting in day/night, LOD switch
    (zoom out), sims interacting. Read `sims4_read_errors` after the session.

### Quality bar ("premium")
- Clean silhouette with bevels where light catches edges; no wasted loops on flat areas.
- Polygon budgets by category: `object_guidelines` (e.g. small decor ≈ 600 tris,
  large furniture ≈ 4 000 tris, hard max 2x).
- Texture: 512 px for most furniture, 1024 for big hero pieces, 256 for clutter. Baked AO, no
  pure black/white, consistent texel density.
- Swatches: 6–12 well-chosen colours, named in catalog colour tags.
- Ship one object per package (or a set with a clear name); never override EA resources unless
  the user wants a default replacement.

### Common S4S errors
| Symptom | Fix |
|---|---|
| Object invisible in game | LOD0 imported into wrong slot, or no uv_1; re-import High detail |
| Black/flat shading | Missing normals or wrong shader; recalc normals, keep custom split normals |
| Floats/sinks | Bottom not at Z = 0 or clone footprint differs |
| Sims can't use it | Interactions come from the clone – pick a functional EA object of the same type |
| Duplicate with EA object | Clone without "new instance" or edited the EA package – recreate with S4S clone |
| Shows in catalog but "missing" | Thumbnail/string missing – re-save in S4S, `package_strings` to verify |

## 4. Mods folder rules
- `.package` loads up to **5 subfolders** deep; `.ts4script` only **1** deep.
- Options → Game Options → Other: *Enable Custom Content and Mods* and *Script Mods Allowed*
  (or `sims4_enable_mods` with the game closed).
- After every patch the game disables mods – re-enable.
- Clear `localthumbcache.package` after adding/updating CC (`sims4_clear_cache`).
- Conflicts: `mods_find_conflicts` lists resources overridden by more than one package.
