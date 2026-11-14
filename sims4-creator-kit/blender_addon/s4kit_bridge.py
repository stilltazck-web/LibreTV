bl_info = {
    "name": "S4Kit Bridge (Sims 4 creator connector)",
    "author": "s4kit",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > S4Kit",
    "description": "Lets the s4kit MCP server (Claude) drive Blender: build houses from "
                   "floorplans, prep/LOD/thumbnail Sims 4 objects, run scripts.",
    "category": "Import-Export",
}

"""Install: Edit > Preferences > Add-ons > Install... > pick this file, enable it, then
press "Start bridge" in the S4Kit sidebar tab. Or launch Blender with
    blender --python s4kit_bridge.py -- --s4kit-port=9876
which registers the add-on and starts the bridge immediately.

Protocol: newline-delimited JSON over TCP on 127.0.0.1 only.
    -> {"cmd": "scene_info", "params": {}, "token": "..."}
    <- {"ok": true, "result": {...}}  |  {"ok": false, "error": "..."}
Commands execute on Blender's main thread (via bpy.app.timers), so they are safe.
"""

import contextlib
import io
import json
import math
import os
import queue
import socket
import sys
import threading
import traceback

import bmesh
import bpy
from mathutils import Matrix, Vector

DEFAULT_PORT = 9876
TOKEN = os.environ.get("S4KIT_TOKEN", "")
WALL_HEIGHTS_M = {"short": 2.75, "medium": 3.0, "tall": 3.5}  # approximate in-game heights

_server = None
_jobs: "queue.Queue" = queue.Queue()


# =====================================================================
# Command implementations (main thread only)
# =====================================================================
def _tris(mesh):
    return sum(len(p.vertices) - 2 for p in mesh.polygons)


def _obj_info(o):
    info = {"name": o.name, "type": o.type, "location": [round(v, 4) for v in o.location],
            "dimensions": [round(v, 4) for v in o.dimensions],
            "parent": o.parent.name if o.parent else None,
            "collection": o.users_collection[0].name if o.users_collection else None}
    if o.type == "MESH":
        me = o.data
        info.update(verts=len(me.vertices), tris=_tris(me),
                    uv_layers=[uv.name for uv in me.uv_layers],
                    materials=[m.name for m in me.materials if m],
                    custom_props={k: str(o[k]) for k in o.keys() if not k.startswith("_")})
    return info


def cmd_ping(p):
    return {"pong": True, "blender": bpy.app.version_string, "file": bpy.data.filepath}


def cmd_scene_info(p):
    objs = [_obj_info(o) for o in bpy.context.scene.objects]
    return {"blender": bpy.app.version_string, "file": bpy.data.filepath,
            "unit_scale": bpy.context.scene.unit_settings.scale_length,
            "selected": [o.name for o in bpy.context.selected_objects],
            "objects": objs[:500], "total_tris": sum(o.get("tris", 0) for o in objs)}


def cmd_exec_python(p):
    """Run arbitrary Python. Assign to `result` to return a JSON-able value."""
    buf = io.StringIO()
    ns = {"bpy": bpy, "bmesh": bmesh, "Vector": Vector, "Matrix": Matrix, "math": math,
          "result": None}
    with contextlib.redirect_stdout(buf):
        exec(compile(p["code"], "<s4kit>", "exec"), ns)
    res = ns.get("result")
    try:
        json.dumps(res)
    except TypeError:
        res = repr(res)
    return {"stdout": buf.getvalue()[-20000:], "result": res}


def _collection(name, clear=False):
    col = bpy.data.collections.get(name)
    if col and clear:
        for o in list(col.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    if not col:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def _material(name, rgba):
    m = bpy.data.materials.get(name)
    if not m:
        m = bpy.data.materials.new(name)
        m.diffuse_color = rgba
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = rgba
    return m


def _add_box(bm, x0, y0, z0, x1, y1, z1):
    vs = [bm.verts.new(v) for v in (
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))]
    for f in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        bm.faces.new([vs[i] for i in f])


def cmd_build_house(p):
    """Build a 3D blockout of a plan (from s4kit floorplan tools). 1 tile = 1 BU."""
    plan = p["plan"]
    tile = float(plan.get("tile_m", 1.0))
    h = float(p.get("wall_height_m") or WALL_HEIGHTS_M.get(plan.get("wall_height"), 3.0))
    t = float(p.get("wall_thickness", 0.15)) / 2
    door_h, sill, win_top = float(p.get("door_height", 2.1)), float(p.get("window_sill", 0.9)), \
        float(p.get("window_top", 2.2))
    stories = int(p.get("stories", plan.get("stories", 1)))
    col = _collection(p.get("collection", "S4Kit_House"), clear=p.get("clear", True))

    openings = {}
    for o in plan.get("openings", []):
        for k in range(int(o.get("width", 1))):
            key = (o["orientation"], o["x"] + (k if o["orientation"] == "h" else 0),
                   o["y"] + (k if o["orientation"] == "v" else 0))
            openings[key] = o.get("kind", "door")

    wall_mat = _material("S4K_Wall", (0.92, 0.9, 0.85, 1))
    floor_mat = _material("S4K_Floor", (0.55, 0.42, 0.3, 1))
    created = []
    for story in range(stories):
        z = story * h
        bm = bmesh.new()
        for s in plan["walls"]:
            horiz = s["y0"] == s["y1"]
            n = int(s["length"])
            for i in range(n):
                if horiz:
                    x, y = s["x0"] + i, s["y0"]
                    key = ("h", x, y)
                    box = lambda z0, z1: _add_box(bm, x * tile - (t if i == 0 else 0), -y * tile - t, z + z0,
                                                  (x + 1) * tile + (t if i == n - 1 else 0), -y * tile + t, z + z1)
                else:
                    x, y = s["x0"], s["y0"] + i
                    key = ("v", x, y)
                    box = lambda z0, z1: _add_box(bm, x * tile - t, -(y + 1) * tile, z + z0,
                                                  x * tile + t, -y * tile, z + z1)
                kind = openings.get(key) if story == 0 else (
                    "window" if openings.get(key) in ("window", "door_or_window") else None)
                if kind in ("door", "door_or_window"):
                    box(door_h, h)
                elif kind == "window":
                    box(0, sill)
                    box(win_top, h)
                elif kind == "archway":
                    box(h - 0.4, h)
                else:
                    box(0, h)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        me = bpy.data.meshes.new(f"Walls_L{story}")
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(f"Walls_L{story}", me)
        ob.data.materials.append(wall_mat)
        col.objects.link(ob)
        created.append(ob.name)

        for r in plan.get("rooms", []):
            bm = bmesh.new()
            for x0, y0, x1, y1 in r.get("floor_rects") or [r["bbox"]]:
                vs = [bm.verts.new((x0 * tile, -y0 * tile, z)), bm.verts.new((x1 * tile, -y0 * tile, z)),
                      bm.verts.new((x1 * tile, -y1 * tile, z)), bm.verts.new((x0 * tile, -y1 * tile, z))]
                bm.faces.new(vs[::-1])  # counter-clockwise from above -> normal up
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
            me = bpy.data.meshes.new(f"Floor_{r['name']}_L{story}")
            bm.to_mesh(me)
            bm.free()
            ob = bpy.data.objects.new(me.name, me)
            ob.data.materials.append(floor_mat)
            ob["s4kit_room"] = r["name"]
            ob["s4kit_area_tiles"] = r["area_tiles"]
            col.objects.link(ob)
            created.append(ob.name)

    # room labels for orientation
    for r in plan.get("rooms", []):
        cu = bpy.data.curves.new(f"Label_{r['name']}", "FONT")
        cu.body = f"{r['name']} ({r['area_tiles']})"
        cu.size = 0.35
        cu.align_x, cu.align_y = "CENTER", "CENTER"
        ob = bpy.data.objects.new(cu.name, cu)
        ob.location = (r["centroid"][0] * tile, -r["centroid"][1] * tile, 0.02)
        col.objects.link(ob)
    return {"created": created, "collection": col.name, "wall_height_m": h,
            "footprint_tiles": [plan["width"], plan["depth"]]}


def cmd_add_reference_image(p):
    """Add a floorplan/photo as a flat reference plane, scaled to metres, for tracing."""
    img = bpy.data.images.load(p["path"], check_existing=True)
    w_m = float(p.get("width_m") or img.size[0] / float(p.get("pixels_per_meter", 50)))
    d_m = w_m * img.size[1] / img.size[0]
    me = bpy.data.meshes.new("S4K_Reference")
    bm = bmesh.new()
    vs = [bm.verts.new(v) for v in ((0, -d_m, -0.01), (w_m, -d_m, -0.01), (w_m, 0, -0.01), (0, 0, -0.01))]
    f = bm.faces.new(vs)
    uv = bm.loops.layers.uv.new("uv_0")
    for loop, co in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop[uv].uv = co
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new("S4K_Reference")
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    me.materials.append(mat)
    ob = bpy.data.objects.new("S4K_Reference", me)
    ob.location = (float(p.get("offset_x", 0)), float(p.get("offset_y", 0)), 0)
    _collection("S4Kit_Reference").objects.link(ob)
    return {"object": ob.name, "size_m": [round(w_m, 3), round(d_m, 3)]}


def _targets(p):
    names = p.get("objects")
    if names:
        return [bpy.data.objects[n] for n in names]
    return [o for o in bpy.context.selected_objects if o.type == "MESH"]


def cmd_prepare_object(p):
    """Clean + validate meshes for Sims 4 Studio import. Non-destructive to names/props."""
    report = []
    merge_dist = float(p.get("merge_distance", 0.0001))
    for o in _targets(p):
        if o.type != "MESH":
            continue
        me = o.data
        notes = []
        if p.get("apply_transforms", True) and o.parent is None:
            if any(abs(s - 1) > 1e-6 for s in o.scale) or any(abs(r) > 1e-6 for r in o.rotation_euler):
                basis = o.matrix_basis.copy()
                loc = basis.to_translation()
                me.transform(Matrix.Translation(-loc) @ basis)
                o.matrix_basis = Matrix.Translation(loc)
                notes.append("applied rotation/scale")
        elif o.parent is not None:
            notes.append(f"parented to {o.parent.name}: transforms left untouched (S4S rig)")
        bm = bmesh.new()
        bm.from_mesh(me)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_dist)
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        degenerate = [f for f in bm.faces if f.calc_area() < 1e-10]
        bmesh.ops.delete(bm, geom=degenerate, context="FACES")
        if p.get("recalc_normals", True):
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
        bm.to_mesh(me)
        bm.free()
        me.update()
        removed = before - len(me.vertices)
        if removed:
            notes.append(f"merged/removed {removed} verts")
        if ngons:
            notes.append(f"{ngons} n-gons (triangulate or fix curved areas)")
        uvs = [uv.name for uv in me.uv_layers]
        if not uvs:
            notes.append("NO UV maps - unwrap uv_0 before import")
        elif len(uvs) == 1:
            if p.get("create_uv1", True):
                me.uv_layers.active = me.uv_layers[0]
                new = me.uv_layers.new(name="uv_1")
                notes.append(f"created {new.name} as a copy of {uvs[0]} - run Lightmap Pack on it if uv_0 overlaps")
            else:
                notes.append("missing uv_1 (shadow/lightmap UVs)")
        bpy.context.view_layer.update()  # refresh bounds after editing mesh data
        dims = [round(v, 3) for v in o.dimensions]
        min_z = min((o.matrix_world @ v.co).z for v in me.vertices) if len(me.vertices) else 0.0
        if abs(min_z) > 0.005 and o.parent is None:
            notes.append(f"lowest point is at Z={min_z:.3f}; floor objects should rest on Z=0")
        report.append({"object": o.name, "tris": _tris(me), "dimensions_m": dims,
                       "footprint_tiles": [max(1, round(dims[0])), max(1, round(dims[1]))],
                       "uv_layers": [uv.name for uv in me.uv_layers],
                       "min_z": round(min_z, 4),
                       "notes": notes})
    return {"objects": report}


def cmd_make_lods(p):
    """Create decimated LOD copies (and an optional shadow mesh) next to each target."""
    ratios = p.get("ratios", [0.5])
    shadow_ratio = p.get("shadow_ratio", 0.1)
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for o in _targets(p):
        made = []
        jobs = [(f"{o.name}_LOD{i + 1}", r) for i, r in enumerate(ratios)]
        if shadow_ratio:
            jobs.append((f"{o.name}_SHADOW", shadow_ratio))
        for name, ratio in jobs:
            mod = o.modifiers.new("s4k_decimate", "DECIMATE")
            mod.ratio = float(ratio)
            dg.update()
            me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
            o.modifiers.remove(mod)
            me.name = name
            old = bpy.data.objects.get(name)
            if old:
                bpy.data.objects.remove(old, do_unlink=True)
            lod = bpy.data.objects.new(name, me)
            lod.matrix_world = o.matrix_world.copy()
            for k in o.keys():
                with contextlib.suppress(Exception):
                    lod[k] = o[k]
            for c in o.users_collection:
                c.objects.link(lod)
            lod.hide_set(True)
            made.append({"name": name, "tris": _tris(me), "ratio": ratio})
        out.append({"source": o.name, "source_tris": _tris(o.data), "lods": made})
    return {"objects": out}


def cmd_render_thumbnail(p):
    """Render a transparent 3/4-view catalog-style thumbnail of the targets.
    engine: default Eevee (fast, GPU); pass "CYCLES" for higher quality or GPU-less machines."""
    scene = bpy.context.scene
    targets = _targets(p) or [o for o in scene.objects if o.type == "MESH" and o.visible_get()]
    if not targets:
        raise ValueError("Nothing to render")
    pts = [o.matrix_world @ Vector(c) for o in targets for c in o.bound_box]
    lo = Vector((min(v.x for v in pts), min(v.y for v in pts), min(v.z for v in pts)))
    hi = Vector((max(v.x for v in pts), max(v.y for v in pts), max(v.z for v in pts)))
    centre, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.1)
    cam_data = bpy.data.cameras.new("S4K_ThumbCam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("S4K_ThumbCam", cam_data)
    scene.collection.objects.link(cam)
    direction = Vector((1.0, -1.2, 0.8)).normalized()
    cam.location = centre + direction * radius * 3.2
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    sun_data = bpy.data.lights.new("S4K_Sun", "SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("S4K_Sun", sun_data)
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(sun)
    old_cam, old_engine = scene.camera, scene.render.engine
    old_path, old_film = scene.render.filepath, scene.render.film_transparent
    old_res = (scene.render.resolution_x, scene.render.resolution_y)
    try:
        scene.camera = cam
        for eng in (p.get("engine"), "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
            if not eng:
                continue
            try:
                scene.render.engine = eng
                break
            except TypeError:
                continue
        if scene.render.engine == "CYCLES":
            scene.cycles.samples = int(p.get("samples", 64))
            scene.cycles.device = "CPU" if p.get("cpu") else scene.cycles.device
        size = int(p.get("size", 512))
        scene.render.resolution_x = scene.render.resolution_y = size
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.filepath = p["path"]
        bpy.ops.render.render(write_still=True)
    finally:
        scene.camera, scene.render.engine = old_cam, old_engine
        scene.render.filepath, scene.render.film_transparent = old_path, old_film
        scene.render.resolution_x, scene.render.resolution_y = old_res
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.objects.remove(sun, do_unlink=True)
    return {"path": p["path"]}


def cmd_save(p):
    path = p.get("path") or bpy.data.filepath
    if not path:
        raise ValueError("No path given and file never saved")
    if p.get("copy", False):
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    else:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    return {"saved": path}


def cmd_open(p):
    bpy.ops.wm.open_mainfile(filepath=p["path"])
    return {"opened": p["path"], "objects": len(bpy.data.objects)}


def cmd_export(p):
    """Export targets (or whole scene) as obj / fbx / glb for other tools."""
    path, fmt = p["path"], p.get("format", os.path.splitext(p["path"])[1][1:].lower())
    sel_only = bool(p.get("objects"))
    if sel_only:
        for o in bpy.context.scene.objects:
            o.select_set(o.name in p["objects"])
    if fmt == "obj":
        if hasattr(bpy.ops.wm, "obj_export"):
            bpy.ops.wm.obj_export(filepath=path, export_selected_objects=sel_only)
        else:
            bpy.ops.export_scene.obj(filepath=path, use_selection=sel_only)
    elif fmt == "fbx":
        bpy.ops.export_scene.fbx(filepath=path, use_selection=sel_only)
    elif fmt in ("glb", "gltf"):
        bpy.ops.export_scene.gltf(filepath=path, use_selection=sel_only)
    else:
        raise ValueError("format must be obj, fbx or glb (save .blend with the save command)")
    return {"exported": path}


def cmd_import(p):
    path = p["path"]
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".blend":  # append all objects, e.g. from an S4S mesh export
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = src.objects
        for o in dst.objects:
            if o:
                bpy.context.scene.collection.objects.link(o)
    else:
        raise ValueError(f"Unsupported import format {ext}")
    return {"imported": [o.name for o in set(bpy.data.objects) - before]}


COMMANDS = {k[4:]: v for k, v in globals().items() if k.startswith("cmd_")}


# =====================================================================
# Networking
# =====================================================================
def _process_jobs():
    while True:
        try:
            payload, box, done = _jobs.get_nowait()
        except queue.Empty:
            return 0.05
        try:
            fn = COMMANDS.get(payload.get("cmd"))
            if not fn:
                raise ValueError(f"Unknown command {payload.get('cmd')!r}; have {sorted(COMMANDS)}")
            box["resp"] = {"ok": True, "result": fn(payload.get("params") or {})}
        except Exception as e:  # noqa: BLE001
            box["resp"] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                           "traceback": traceback.format_exc()[-4000:]}
        done.set()


class BridgeServer:
    def __init__(self, port):
        self.port = port
        self.sock = None
        self.running = False

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(4)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()
        if not bpy.app.timers.is_registered(_process_jobs):
            bpy.app.timers.register(_process_jobs, persistent=True)
        print(f"[s4kit] bridge listening on 127.0.0.1:{self.port}")

    def stop(self):
        self.running = False
        with contextlib.suppress(Exception):
            self.sock.close()
        if bpy.app.timers.is_registered(_process_jobs):
            bpy.app.timers.unregister(_process_jobs)

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        with conn, conn.makefile("rwb") as f:
            for line in f:
                try:
                    payload = json.loads(line)
                    if TOKEN and payload.get("token") != TOKEN:
                        resp = {"ok": False, "error": "bad token"}
                    else:
                        box, done = {}, threading.Event()
                        _jobs.put((payload, box, done))
                        if not done.wait(float(payload.get("timeout", 600))):
                            resp = {"ok": False, "error": "timed out waiting for Blender main thread"}
                        else:
                            resp = box["resp"]
                except Exception as e:  # noqa: BLE001
                    resp = {"ok": False, "error": f"bad request: {e}"}
                f.write((json.dumps(resp, default=str) + "\n").encode())
                f.flush()


def start_bridge(port=DEFAULT_PORT):
    global _server
    if _server and _server.running:
        return
    _server = BridgeServer(port)
    _server.start()


def stop_bridge():
    global _server
    if _server:
        _server.stop()
        _server = None


# =====================================================================
# UI
# =====================================================================
class S4K_OT_start(bpy.types.Operator):
    bl_idname = "s4kit.start_bridge"
    bl_label = "Start bridge"

    def execute(self, context):
        start_bridge(context.scene.s4kit_port)
        return {"FINISHED"}


class S4K_OT_stop(bpy.types.Operator):
    bl_idname = "s4kit.stop_bridge"
    bl_label = "Stop bridge"

    def execute(self, context):
        stop_bridge()
        return {"FINISHED"}


class S4K_PT_panel(bpy.types.Panel):
    bl_label = "S4Kit Bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "S4Kit"

    def draw(self, context):
        col = self.layout.column()
        running = bool(_server and _server.running)
        col.label(text=f"Status: {'listening' if running else 'stopped'}")
        col.prop(context.scene, "s4kit_port")
        col.operator("s4kit.stop_bridge" if running else "s4kit.start_bridge")


_classes = (S4K_OT_start, S4K_OT_stop, S4K_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.s4kit_port = bpy.props.IntProperty(name="Port", default=DEFAULT_PORT,
                                                       min=1024, max=65535)
    # Auto-start the bridge when Blender opens with its UI (not in background/batch runs).
    # Set S4KIT_AUTOSTART=0 to disable.
    if not bpy.app.background and os.environ.get("S4KIT_AUTOSTART", "1") != "0":
        def _autostart():
            with contextlib.suppress(OSError):  # port busy -> another Blender has it
                start_bridge(int(os.environ.get("S4KIT_BLENDER_PORT", DEFAULT_PORT)))
            return None
        bpy.app.timers.register(_autostart, first_interval=1.0)


def unregister():
    stop_bridge()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.s4kit_port


if __name__ == "__main__":  # launched via: blender --python s4kit_bridge.py -- --s4kit-port=N
    port = DEFAULT_PORT
    for arg in sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []:
        if arg.startswith("--s4kit-port="):
            port = int(arg.split("=", 1)[1])
    with contextlib.suppress(ValueError):
        register()
    start_bridge(port)
