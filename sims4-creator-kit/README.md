# S4Kit: Claude connectors for The Sims 4, Sims 4 Studio & Blender

S4Kit is a local **MCP server** plus a **Blender add-on**. Together they let Claude (Desktop or
Claude Code) work with the three tools on your PC:

| Area | What Claude can do |
|---|---|
| **The Sims 4** | Find the install and user folders, launch the game (EA app/Steam), enable mods and script mods, install CC, check the Mods folder for files that won't load, find conflicting mods, clear caches, read `lastException` logs, list and back up saved lots and rooms (Tray) |
| **Sims 4 Studio** | Launch S4S with a package loaded. A built-in `.package` (DBPF 2.1) engine inspects, extracts, replaces and merges resources and edits catalog strings (STBL) in every language. It also includes S4S-style FNV hashing and stable instance IDs |
| **Blender** | Live bridge: inspect the scene, run Python, build 3D house blockouts from plans, add reference images, clean and validate meshes for S4S, generate LOD and shadow meshes, render catalog thumbnails, import/export/save |
| **Houses** | Floorplan image → walls, doors, windows and rooms snapped to the Sims tile grid. Photo or description → room layout. Outputs a plan JSON, a preview PNG, a lot-size suggestion and a step-by-step Build Mode guide |
| **Premium objects** | Polygon, LOD, texture and UV budgets per object category, plus an expert workflow guide (`knowledge/SIMS4_CREATOR_GUIDE.md`) |

```
Claude ⇄ (MCP stdio) ⇄ s4kit server ─┬─ files: Mods, Tray, Options.ini, .package files
                                     ├─ launches TS4_x64.exe / S4Studio.exe / blender.exe
                                     └─ TCP 127.0.0.1:9876 ⇄ Blender add-on (s4kit_bridge.py)
```

## Install (Windows)

**Quickest:** open PowerShell, paste the whole of `bootstrap_windows.ps1`, and press Enter.
It installs Git and Python if they're missing, downloads S4Kit to `%USERPROFILE%\S4Kit`, and
installs and tests it. It also connects it to Claude Desktop and Claude Code (keeping your other
connectors), installs and enables the Blender add-on (the bridge auto-starts when Blender
opens), and reports what it found. It's safe to run again to update.

**Manual install:**

Requirements: Python 3.10+, The Sims 4, Sims 4 Studio, and Blender (the version your S4S release supports).

```powershell
cd sims4-creator-kit
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

Then connect Claude:

* **Claude Code**: `claude mcp add s4kit -- "C:\path\to\sims4-creator-kit\.venv\Scripts\s4kit-mcp.exe"`
* **Claude Desktop**: merge `examples/claude_desktop_config.json` into
  `%APPDATA%\Claude\claude_desktop_config.json` (fix the path), then restart Claude.

Install the Blender add-on: **Edit → Preferences → Add-ons → Install…** → choose
`blender_addon/s4kit_bridge.py` → enable it. The bridge starts automatically when Blender
opens (set `S4KIT_AUTOSTART=0` to turn that off). You can also use the button under
**3D View sidebar (N) → S4Kit**.
Alternatively, the `blender_launch` tool starts Blender with the bridge already running.

### If something isn't found automatically
Set any of these environment variables, for example in the `env` block of the Claude config:

| Variable | Example |
|---|---|
| `S4KIT_SIMS4_DIR` | `D:\Games\The Sims 4` |
| `S4KIT_SIMS4_DOCS` | `C:\Users\you\OneDrive\Documents\Electronic Arts\The Sims 4` |
| `S4KIT_S4S_EXE` | `C:\Program Files\Sims 4 Studio\S4Studio.exe` |
| `S4KIT_BLENDER_EXE` | `C:\Program Files\Blender Foundation\Blender 4.2\blender.exe` |
| `S4KIT_BLENDER_PORT` | `9876` |
| `S4KIT_TOKEN` | Optional shared secret. Set the same value for Blender and the server |
| `S4KIT_WORK_DIR` | Where plans and thumbnails are written (default `~/S4Kit`) |

Ask Claude to run `environment_report` to check that everything was detected.

## Things to ask Claude

* "Here's the floorplan of my grandma's house (`C:\plans\grandma.jpg`, it's 14 m wide). Turn it
  into a Sims 4 build, show me the 3D blockout, and give me the build steps."
* "Look at this photo of a Craftsman bungalow and design a Sims 4 version on a 30x20 lot."
* "I exported the mesh of a cloned EA lamp from S4S to `lamp.blend`. Open it, check it against
  premium budgets, make LODs, render a thumbnail and save it."
* "Rename my sofa in `Me_VelvetSofa.package` to 'Velvet Cloud Sofa' in all languages, install it
  and launch the game."
* "My game threw an error. Read the last exception and tell me which mod caused it."

## Tools (41)

`environment_report`, `sims4_launch`, `sims4_enable_mods`, `s4s_launch`, `blender_launch`,
`blender_run_headless`, `mods_list`, `mods_install`, `mods_find_conflicts`,
`mods_repair_folder`, `sims4_clear_cache`, `sims4_read_errors`, `sims4_list_tray`,
`sims4_backup_tray_item`, `package_inspect`, `package_extract`, `package_replace`,
`package_merge`, `package_strings`, `package_set_string`, `hash_tool`, `new_object_ids`,
`floorplan_analyze`, `floorplan_from_rooms`, `floorplan_preview`, `floorplan_build_guide`,
`view_image`, `blender_scene_info`, `blender_exec`, `blender_build_house`,
`blender_add_reference_image`, `blender_prepare_object`, `blender_make_lods`,
`blender_render_thumbnail`, `blender_open`, `blender_import`, `blender_save`,
`blender_export`, `object_guidelines`, `object_check`, `creator_guide`

## Honest limits

* **No in-game automation.** EA provides no API for placing walls or objects in a running game.
  Houses come out as an exact build guide plus a 3D blockout, and you place the walls yourself
  in Build Mode (the guide gives every wall's start and end tile and length). Generating Tray
  `.blueprint` files directly isn't supported because the format is undocumented.
* **Sims 4 Studio has no scripting API.** S4Kit launches S4S and handles `.package` files
  directly (strings, resources, merging). Cloning objects and importing meshes and textures are
  still clicks inside S4S; the guide covers each step.
* **Floorplan detection** handles axis-aligned walls on clean plans: scans, listing images and
  CAD exports. Walls are snapped to whole tiles. For sketches or photos, Claude writes the room
  layout instead (`floorplan_from_rooms`).
* The polygon and texture budgets are community best practice, not EA-published limits.

## Development

```bash
pip install -e .[test] && pytest -q
```

`tests/` covers DBPF read/write (zlib, RefPack, constant-field indexes), STBL, FNV hashes,
floorplan detection on a synthetic plan (including ignoring text and dimension lines), room
layouts, budgets, the Blender wire protocol and MCP tool registration. The Blender add-on was
also exercised end-to-end against the real `bpy` module for Blender 4.2 and 5.0: house
build, object prep, LODs, Cycles thumbnail, glTF export and .blend save.
