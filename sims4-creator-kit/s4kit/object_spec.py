"""Quality targets for 'premium' (Maxis-match-friendly, performance-safe) Sims 4 objects.

These are community best-practice budgets, not EA-published limits. They keep CC
performant on mid-range PCs and consistent with base-game objects.
"""
from __future__ import annotations

# category -> (LOD0 high tris target, hard max, LOD1 ratio, diffuse texture size)
BUDGETS: dict[str, dict] = {
    "small_decor":   {"tris": 600,  "max": 1500,  "lod1": 0.5,  "texture": 256,
                      "examples": "vases, books, clutter, small plants"},
    "medium_decor":  {"tris": 1500, "max": 3000,  "lod1": 0.5,  "texture": 512,
                      "examples": "lamps, wall art, mirrors, pots"},
    "seating":       {"tris": 2500, "max": 5000,  "lod1": 0.45, "texture": 512,
                      "examples": "chairs, armchairs, stools"},
    "large_furniture": {"tris": 4000, "max": 8000, "lod1": 0.4, "texture": 1024,
                      "examples": "sofas, beds, dressers, dining tables"},
    "functional":    {"tris": 3500, "max": 7000,  "lod1": 0.45, "texture": 1024,
                      "examples": "stoves, fridges, showers - clone a matching EA object for interactions"},
    "build_item":    {"tris": 1200, "max": 3000,  "lod1": 0.5,  "texture": 512,
                      "examples": "windows, doors, columns, trims"},
    "large_feature": {"tris": 6000, "max": 12000, "lod1": 0.35, "texture": 1024,
                      "examples": "pools decor, big plants/trees, sculptures"},
}

UV_RULES = [
    "UV channel 0 (uv_0): texture map. Islands inside 0-1, no overlaps unless mirroring is intended, "
    "2-4 px padding at your texture size.",
    "UV channel 1 (uv_1): shadow/lightmap channel. Must exist; no overlaps; S4S expects it. "
    "Lightmap Pack is a good start.",
    "Keep texel density consistent across the object; use a checker texture to verify.",
]

MESH_RULES = [
    "Apply scale & rotation (Ctrl+A) - S4S reads raw vertex data.",
    "Scale: 1 Blender unit = 1 Sims 4 tile (~1 m). Keep the object centred on the origin of its "
    "cloned EA object; the bottom sits at Z=0 for floor objects.",
    "Footprint: object should fit the cloned object's footprint (1x1, 2x1, ...). Mismatches cause "
    "routing problems - pick a clone with the same footprint size.",
    "Triangles only matter for the in-game count; quads are fine in Blender (S4S triangulates).",
    "No n-gons on curved surfaces, no loose verts, no zero-area faces, normals facing out.",
    "Keep the mesh names/cut numbers S4S exported (e.g. s4studio_mesh_1); add meshes by "
    "duplicating an existing group so the custom properties are copied.",
    "Shading: use Auto Smooth / weighted normals for hard-surface edges; S4S keeps custom normals.",
    "Generate LOD1 (Medium/Low detail) at the ratio for its category and a shadow mesh "
    "(very low poly, uv_1 only) - or reuse the clone's shadow if the silhouette is similar.",
]

TEXTURE_RULES = [
    "Diffuse: power-of-two (256/512/1024). Square or 2:1. Save as PNG for import; S4S converts to DST.",
    "Specular: same size or half; use the clone's specular format (S4S 'Import specular').",
    "Normal/bump map: S4S objects use a 'normal map' channel only on shaders that support it - "
    "check the clone's material in the Studio tab before baking one.",
    "Bake AO into the diffuse for Maxis-match look; avoid pure black/white values.",
    "Swatches: each colour variant is a separate texture set in S4S Warehouse/Studio; keep file names consistent.",
]


def check_object(category: str, lod0_tris: int, lod1_tris: int | None = None,
                 texture_size: int | None = None, has_uv1: bool | None = None,
                 dims_m: tuple[float, float, float] | None = None) -> dict:
    b = BUDGETS.get(category)
    if not b:
        return {"ok": False, "error": f"Unknown category; choose one of {list(BUDGETS)}"}
    issues, notes = [], []
    if lod0_tris > b["max"]:
        issues.append(f"LOD0 {lod0_tris} tris exceeds hard max {b['max']} for {category}")
    elif lod0_tris > b["tris"]:
        notes.append(f"LOD0 {lod0_tris} tris is above the target {b['tris']}; fine for hero pieces")
    target_lod1 = int(lod0_tris * b["lod1"])
    if lod1_tris is None:
        notes.append(f"Create a LOD1 of ~{target_lod1} tris")
    elif lod1_tris > lod0_tris * (b["lod1"] + 0.15):
        issues.append(f"LOD1 {lod1_tris} tris too high; aim for ~{target_lod1}")
    if texture_size:
        if texture_size & (texture_size - 1):
            issues.append(f"Texture size {texture_size} is not a power of two")
        elif texture_size > b["texture"] * 2:
            issues.append(f"Texture {texture_size}px is oversized; {b['texture']}px recommended")
    if has_uv1 is False:
        issues.append("Missing UV channel 1 (uv_1) - required for shadows/lighting")
    if dims_m:
        w, d, h = dims_m
        notes.append(f"Footprint about {max(1, round(w))}x{max(1, round(d))} tiles; clone an EA object with that footprint")
        if h > 3.0 and category != "large_feature":
            notes.append("Taller than a medium wall - check it doesn't clip through ceilings")
    return {"ok": not issues, "category": category, "budget": b, "issues": issues,
            "notes": notes, "recommended_lod1_tris": target_lod1}


def guidelines() -> dict:
    return {"budgets": BUDGETS, "uv": UV_RULES, "mesh": MESH_RULES, "textures": TEXTURE_RULES}
