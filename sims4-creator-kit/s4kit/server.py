"""S4Kit MCP server: connects Claude to The Sims 4, Sims 4 Studio and Blender on your PC.

Run:  python -m s4kit.server        (stdio transport, for Claude Desktop / Claude Code)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

try:  # MCP Python SDK v2
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore

from . import apps, floorplan, mods, object_spec
from .blender_client import BlenderClient
from .dbpf import Package, ResourceKey
from .resources import (LANGUAGES, STBL_TYPE, StringTable, fnv32, fnv64, new_instance_id,
                        stbl_instance, type_name)

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge" / "SIMS4_CREATOR_GUIDE.md"
WORK_DIR = Path(os.environ.get("S4KIT_WORK_DIR", Path.home() / "S4Kit"))

INSTRUCTIONS = """S4Kit connects you to The Sims 4, Sims 4 Studio (S4S) and Blender on the user's PC.
Read the resource s4kit://guide (or call creator_guide) before planning a build or a new object.
Workflows:
* House from floorplan image: floorplan_analyze -> review preview & warnings -> blender_build_house
  (3D check) -> give the user the build guide; they place walls in Build Mode.
* House from a photo/description: look at the image yourself, estimate rooms in 1 m tiles,
  call floorplan_from_rooms, then the same steps.
* New object: clone a similar EA object in S4S (s4s_launch), export its mesh .blend, open in
  Blender (blender_open / blender_import), model, blender_prepare_object -> blender_make_lods ->
  blender_render_thumbnail -> save .blend -> import back in S4S -> package_* tools for names/
  strings -> mods_install -> sims4_launch to test.
Always close the game before editing Options.ini, clearing caches or replacing installed packages."""

mcp = _Server("s4kit", instructions=INSTRUCTIONS)
blender = BlenderClient()


def _j(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


# =====================================================================
# Environment / launching
# =====================================================================
@mcp.tool()
def environment_report() -> str:
    """Detect The Sims 4, Sims 4 Studio and Blender installs, user folders, and what's running."""
    rep = apps.environment_report()
    try:
        rep["blender"]["bridge"] = blender.call("ping")
    except Exception as e:  # noqa: BLE001
        rep["blender"]["bridge"] = f"not connected: {e}"
    return _j(rep)


@mcp.tool()
def sims4_launch(method: str = "auto") -> str:
    """Launch The Sims 4. method: auto | exe | steam."""
    return _j(apps.launch_sims4(method))


@mcp.tool()
def sims4_enable_mods(enabled: bool = True, script_mods: bool = True) -> str:
    """Turn custom content and script mods on/off in Options.ini (game must be closed)."""
    return _j(apps.set_mods_enabled(enabled, script_mods))


@mcp.tool()
def s4s_launch(package_path: str = "") -> str:
    """Open Sims 4 Studio, optionally with a .package file loaded."""
    return _j(apps.launch_s4s(package_path or None))


@mcp.tool()
def blender_launch(blend_file: str = "", port: int = 9876) -> str:
    """Start Blender with the S4Kit bridge running so the blender_* tools work."""
    return _j(apps.launch_blender(blend_file or None, True, port))


@mcp.tool()
def blender_run_headless(script: str, blend_file: str = "") -> str:
    """Run a Python script in background Blender (no UI, no bridge needed). Returns output."""
    return _j(apps.run_blender_headless(script, blend_file or None))


# =====================================================================
# Mods, cache, tray, diagnostics
# =====================================================================
@mcp.tool()
def mods_list() -> str:
    """List CC in the Mods folder and flag files that won't load (too deep, archives, etc.)."""
    return _j(mods.list_mods())


@mcp.tool()
def mods_install(source_path: str, subfolder: str = "s4kit") -> str:
    """Install a .package / .ts4script / .zip into Mods/<subfolder>."""
    return _j(mods.install_mod(source_path, subfolder))


@mcp.tool()
def mods_find_conflicts() -> str:
    """Scan every package in Mods for resources overridden by more than one file."""
    return _j(mods.find_conflicts())


@mcp.tool()
def mods_repair_folder() -> str:
    """Create the Mods folder and a correct Resource.cfg if missing."""
    return _j(mods.repair_mods_folder())


@mcp.tool()
def sims4_clear_cache(include_thumbnails: bool = True) -> str:
    """Delete localthumbcache.package and cache folders (do after adding/updating CC)."""
    return _j(mods.clear_cache(include_thumbnails))


@mcp.tool()
def sims4_read_errors(limit: int = 5) -> str:
    """Read the newest lastException/lastUIException logs and the mods they mention."""
    return _j(mods.read_exceptions(limit))


@mcp.tool()
def sims4_list_tray() -> str:
    """List saved lots, rooms and households in the Tray (Library) folder."""
    return _j(mods.list_tray())


@mcp.tool()
def sims4_backup_tray_item(item_id: str, destination: str = "") -> str:
    """Copy all files of one Tray item (e.g. a lot) to a backup folder."""
    dest = destination or str(WORK_DIR / "tray_backups" / item_id)
    return _j(mods.backup_tray_item(item_id, dest))


# =====================================================================
# .package files
# =====================================================================
@mcp.tool()
def package_inspect(package_path: str, type_filter: str = "") -> str:
    """List resources in a .package (type, group, instance, size). type_filter e.g. 'STBL' or '220557DA'."""
    pkg = Package.load(package_path)
    rows = []
    for r in pkg:
        tname = type_name(r.key.type)
        if type_filter and type_filter.upper() not in tname.upper() and \
                type_filter.upper() != f"{r.key.type:08X}":
            continue
        rows.append({"key": str(r.key), "type": tname, "compressed": r.compression != 0,
                     "size": r.mem_size or len(r.data)})
    counts: dict[str, int] = {}
    for r in pkg:
        t = type_name(r.key.type).split(" ")[0]
        counts[t] = counts.get(t, 0) + 1
    return _j({"package": package_path, "resource_count": len(pkg), "by_type": counts,
               "resources": rows[:1000]})


@mcp.tool()
def package_extract(package_path: str, key: str, output_path: str) -> str:
    """Extract one resource (key 'TTTTTTTT:GGGGGGGG:IIIIIIIIIIIIIIII') to a file."""
    res = Package.load(package_path).get(ResourceKey.parse(key))
    if not res:
        return f"Resource {key} not found"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(res.data)
    return _j({"written": output_path, "bytes": len(res.data)})


@mcp.tool()
def package_replace(package_path: str, key: str, input_path: str, output_path: str = "") -> str:
    """Add/replace a resource with a file's bytes. Saves to output_path (default: in place, .bak kept)."""
    p = Path(package_path)
    pkg = Package.load(p)
    pkg.put(ResourceKey.parse(key), Path(input_path).read_bytes())
    out = Path(output_path) if output_path else p
    if out == p:
        p.with_suffix(".package.bak").write_bytes(p.read_bytes())
    pkg.save(out)
    return _j({"saved": str(out), "resources": len(pkg)})


@mcp.tool()
def package_merge(inputs: list[str], output_path: str) -> str:
    """Merge several .package files into one (later files win on duplicate keys)."""
    out = Package()
    for f in inputs:
        out.merge(Package.load(f))
    out.save(output_path)
    return _j({"saved": output_path, "resources": len(out)})


@mcp.tool()
def package_strings(package_path: str) -> str:
    """Show all string tables (catalog names/descriptions) in a package."""
    pkg = Package.load(package_path)
    out = []
    for r in pkg.find(type_id=STBL_TYPE):
        tbl = StringTable.from_bytes(r.data)
        out.append({"key": str(r.key), "language": LANGUAGES.get(r.key.instance >> 56, "?"),
                    "strings": {f"0x{k:08X}": v for k, v in tbl.entries.items()}})
    return _j(out)


@mcp.tool()
def package_set_string(package_path: str, string_key: str, text: str,
                       stbl_key: str = "", all_languages: bool = True) -> str:
    """Set a catalog string. string_key: '0x1234ABCD' (hash from OBJD/COBJ) or plain text to hash.
    Updates every language table by default so non-English players don't see blank names."""
    p = Path(package_path)
    pkg = Package.load(p)
    key = int(string_key, 16) if string_key.lower().startswith("0x") else fnv32(string_key)
    tables = pkg.find(type_id=STBL_TYPE)
    if stbl_key:
        tables = [t for t in tables if str(t.key) == str(ResourceKey.parse(stbl_key))]
    elif not all_languages:
        tables = [t for t in tables if (t.key.instance >> 56) == 0]
    if not tables:
        inst = stbl_instance(p.stem)
        pkg.put(ResourceKey(STBL_TYPE, 0x80000000, inst), StringTable().to_bytes())
        tables = pkg.find(type_id=STBL_TYPE, instance=inst)
    for r in tables:
        tbl = StringTable.from_bytes(r.data)
        tbl.set(key, text)
        r.data = tbl.to_bytes()
    p.with_suffix(".package.bak").write_bytes(p.read_bytes())
    pkg.save(p)
    return _j({"string_key": f"0x{key:08X}", "tables_updated": [str(t.key) for t in tables]})


@mcp.tool()
def hash_tool(text: str, bits: int = 64, custom_content_bit: bool = True) -> str:
    """FNV hash like S4S's Hash Generator (for instance ids, string keys, tuning names)."""
    v = fnv64(text, custom_content_bit) if bits == 64 else fnv32(text, custom_content_bit)
    return _j({"text": text, "bits": bits, "hex": f"0x{v:0{bits // 4}X}", "decimal": v})


@mcp.tool()
def new_object_ids(creator: str, item_name: str) -> str:
    """Deterministic instance ids/keys for a new object (stable across rebuilds)."""
    base = f"{creator}:{item_name}"
    return _j({"instance_id": f"0x{new_instance_id(creator, item_name):016X}",
               "name_string_key": f"0x{fnv32(base + ':name'):08X}",
               "description_string_key": f"0x{fnv32(base + ':desc'):08X}",
               "tuning_name": f"{creator}:{item_name}".replace(" ", "_")})


# =====================================================================
# Floorplans -> houses
# =====================================================================
def _plan_out(plan: dict, out_dir: str, stem: str) -> str:
    files = floorplan.save_plan(plan, out_dir or str(WORK_DIR / "plans" / stem), stem)
    summary = {k: plan[k] for k in ("width", "depth", "interior_area_tiles", "lot", "warnings")}
    summary["rooms"] = [{"name": r["name"], "area_tiles": r["area_tiles"], "bbox": r["bbox"]}
                        for r in plan["rooms"]]
    summary["walls"] = len(plan["walls"])
    summary["openings"] = plan["openings"]
    summary["files"] = files
    summary["ascii_map"] = plan["ascii"]
    return _j(summary)


@mcp.tool()
def floorplan_analyze(image_path: str, meters_across: float = 0, pixels_per_meter: float = 0,
                      wall_thickness_px: int = 0, invert: bool = False, out_dir: str = "",
                      name: str = "") -> str:
    """Detect walls/doors/rooms in a floorplan image and convert to a Sims 4 tile plan.
    Give meters_across (outer wall width in m) for correct scale. Writes plan JSON,
    preview PNG and a Markdown build guide; returns summary + ASCII map."""
    plan = floorplan.analyze_image(image_path, meters_across or None, pixels_per_meter or None,
                                   wall_thickness_px or None, invert=invert)
    stem = name or Path(image_path).stem
    plan["source"]["name"] = stem
    return _plan_out(plan, out_dir, stem)


@mcp.tool()
def floorplan_from_rooms(rooms: list[dict], doors: list[dict] | None = None,
                         windows: list[dict] | None = None, name: str = "House",
                         wall_height: str = "medium", stories: int = 1, out_dir: str = "") -> str:
    """Build a plan from room rectangles in tiles (use after studying a house photo or sketch).
    rooms:   [{"name":"Living","x":0,"y":0,"w":6,"h":5}, ...]  (y grows southward)
    doors:   [{"between":["Living","Kitchen"]}, {"room":"Living","side":"south"}]
    windows: [{"room":"Living","side":"north","count":2,"width":1}]"""
    plan = floorplan.plan_from_rooms(rooms, doors, windows, wall_height, stories, name)
    return _plan_out(plan, out_dir, name.replace(" ", "_"))


@mcp.tool()
def floorplan_preview(plan_json_path: str) -> Image:
    """Return the rendered plan preview so you can visually check it."""
    png = Path(plan_json_path).with_suffix(".png")
    if not png.exists():
        floorplan.render_png(json.loads(Path(plan_json_path).read_text()), str(png))
    return Image(path=str(png))


@mcp.tool()
def view_image(image_path: str) -> Image:
    """Look at any local image (floorplan, house photo, texture, thumbnail)."""
    return Image(path=image_path)


@mcp.tool()
def floorplan_build_guide(plan_json_path: str) -> str:
    """Step-by-step Build Mode instructions (walls, doors, windows, rooms, lot) for a plan."""
    return floorplan.build_guide(json.loads(Path(plan_json_path).read_text()))


# =====================================================================
# Blender (live bridge)
# =====================================================================
@mcp.tool()
def blender_scene_info() -> str:
    """Objects, triangle counts, UV maps, materials and custom props in the open Blender scene."""
    return _j(blender.call("scene_info"))


@mcp.tool()
def blender_exec(code: str) -> str:
    """Run Python inside Blender (bpy, bmesh available). Set `result = ...` to return data."""
    return _j(blender.call("exec_python", code=code))


@mcp.tool()
def blender_build_house(plan_json_path: str, wall_height_m: float = 0, stories: int = 0,
                        wall_thickness: float = 0.15, clear: bool = True) -> str:
    """Build a 3D blockout (walls with door/window cut-outs, floors, labels) from a plan."""
    plan = json.loads(Path(plan_json_path).read_text())
    params = {"plan": plan, "wall_thickness": wall_thickness, "clear": clear}
    if wall_height_m:
        params["wall_height_m"] = wall_height_m
    if stories:
        params["stories"] = stories
    return _j(blender.call("build_house", **params))


@mcp.tool()
def blender_add_reference_image(image_path: str, width_m: float = 0, pixels_per_meter: float = 0) -> str:
    """Lay an image (floorplan, photo, concept art) on the ground plane at real scale for tracing."""
    params = {"path": str(Path(image_path).resolve())}
    if width_m:
        params["width_m"] = width_m
    if pixels_per_meter:
        params["pixels_per_meter"] = pixels_per_meter
    return _j(blender.call("add_reference_image", **params))


@mcp.tool()
def blender_prepare_object(objects: list[str] | None = None, category: str = "medium_decor",
                           create_uv1: bool = True) -> str:
    """Clean meshes for S4S (merge doubles, normals, transforms, uv_1) and check against the
    premium budget for the category (see object_guidelines)."""
    rep = blender.call("prepare_object", objects=objects or [], create_uv1=create_uv1)
    for o in rep["objects"]:
        o["check"] = object_spec.check_object(category, o["tris"], has_uv1=len(o["uv_layers"]) >= 2,
                                              dims_m=tuple(o["dimensions_m"]))
    return _j(rep)


@mcp.tool()
def blender_make_lods(objects: list[str] | None = None, ratios: list[float] | None = None,
                      shadow_ratio: float = 0.1) -> str:
    """Generate decimated LOD meshes (and a shadow mesh) for the given/selected objects."""
    return _j(blender.call("make_lods", objects=objects or [], ratios=ratios or [0.5],
                           shadow_ratio=shadow_ratio))


@mcp.tool()
def blender_render_thumbnail(output_path: str, objects: list[str] | None = None, size: int = 512,
                             engine: str = "") -> Image:
    """Render a transparent 3/4 catalog thumbnail and return it for review.
    engine: "" = Eevee (fast), "CYCLES" = best quality."""
    path = str(Path(output_path).resolve())
    params = {"path": path, "objects": objects or [], "size": size}
    if engine:
        params["engine"] = engine.upper()
    blender.call("render_thumbnail", **params)
    return Image(path=path)


@mcp.tool()
def blender_open(blend_path: str) -> str:
    """Open a .blend (e.g. a mesh exported from Sims 4 Studio) in the running Blender."""
    return _j(blender.call("open", path=str(Path(blend_path).resolve())))


@mcp.tool()
def blender_import(path: str) -> str:
    """Import .obj/.fbx/.glb, or append objects from a .blend, into the current scene."""
    return _j(blender.call("import", path=str(Path(path).resolve())))


@mcp.tool()
def blender_save(path: str = "", copy: bool = False) -> str:
    """Save the current .blend (S4S imports .blend files exported from its own mesh export)."""
    return _j(blender.call("save", path=str(Path(path).resolve()) if path else "", copy=copy))


@mcp.tool()
def blender_export(path: str, objects: list[str] | None = None) -> str:
    """Export objects/scene as .obj, .fbx or .glb."""
    return _j(blender.call("export", path=str(Path(path).resolve()), objects=objects or []))


# =====================================================================
# Knowledge
# =====================================================================
@mcp.tool()
def object_guidelines(category: str = "") -> str:
    """Premium-object budgets (tris/LOD/texture) and mesh, UV and texture rules."""
    g = object_spec.guidelines()
    if category:
        g["budgets"] = {category: g["budgets"].get(category)}
    return _j(g)


@mcp.tool()
def object_check(category: str, lod0_tris: int, lod1_tris: int = 0, texture_size: int = 0,
                 has_uv1: bool = True) -> str:
    """Check numbers for an object against the premium budget for its category."""
    return _j(object_spec.check_object(category, lod0_tris, lod1_tris or None,
                                       texture_size or None, has_uv1))


@mcp.tool()
def creator_guide() -> str:
    """Expert workflow guide for The Sims 4 + Sims 4 Studio + Blender (houses and objects)."""
    return KNOWLEDGE.read_text(encoding="utf-8")


try:
    @mcp.resource("s4kit://guide")
    def guide_resource() -> str:
        """Expert creator guide."""
        return KNOWLEDGE.read_text(encoding="utf-8")
except Exception:  # noqa: BLE001 - resources are optional
    pass


def main() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    mcp.run()


if __name__ == "__main__":
    main()
