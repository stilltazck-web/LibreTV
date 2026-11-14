"""Floorplans -> Sims 4 build plans.

Two entry points produce the same plan structure:

* ``analyze_image``   - detect walls in a floorplan image (scan, screenshot, drawing).
* ``plan_from_rooms`` - build a plan from room rectangles (what Claude writes after
  looking at a photo of a house, or what you type by hand).

Sims 4 walls only run along the tile grid, so everything is snapped to whole
tiles (1 tile ~= 1 m). Coordinates: x to the right, y downward (image space),
origin at the top-left corner of the house footprint.

Internally walls are stored as *unit edges*:
    ("h", x, y)  edge from (x, y) to (x+1, y)
    ("v", x, y)  edge from (x, y) to (x, y+1)
"""
from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LOT_SIZES = [(20, 15), (20, 20), (30, 20), (30, 30), (40, 30), (40, 40), (50, 50), (64, 64)]

Edge = tuple[str, int, int]


# ======================================================================
# Image analysis
# ======================================================================
def _otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_all = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, thresh = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0 or w_b == total:
            continue
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / (total - w_b)
        between = w_b * (total - w_b) * (m_b - m_f) ** 2
        if between > best:
            best, thresh = between, t
    return thresh


def _run_lengths(mask: np.ndarray, axis: int) -> np.ndarray:
    """For every True pixel, the length of the run of True it belongs to along ``axis``."""
    m = mask if axis == 1 else mask.T
    out = np.zeros(m.shape, dtype=np.int32)
    for i, row in enumerate(m):
        if not row.any():
            continue
        padded = np.concatenate(([0], row.astype(np.int8), [0]))
        d = np.diff(padded)
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        for s, e in zip(starts, ends):
            out[i, s:e] = e - s
    return out if axis == 1 else out.T


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Connected components via row-run union-find. Returns (x0, y0, x1, y1, pixels)."""
    parent: list[int] = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    runs = []  # (row, start, end, id)
    prev: list[tuple[int, int, int]] = []
    for y, row in enumerate(mask):
        cur = []
        if row.any():
            padded = np.concatenate(([0], row.astype(np.int8), [0]))
            d = np.diff(padded)
            for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
                rid = len(parent)
                parent.append(rid)
                for ps, pe, pid in prev:
                    if ps < e and s < pe:
                        ra, rb = find(rid), find(pid)
                        if ra != rb:
                            parent[ra] = rb
                cur.append((s, e, rid))
                runs.append((y, s, e, rid))
        prev = cur
    boxes: dict[int, list[int]] = {}
    for y, s, e, rid in runs:
        r = find(rid)
        b = boxes.setdefault(r, [s, y, e, y + 1, 0])
        b[0], b[1], b[2], b[3] = min(b[0], s), min(b[1], y), max(b[2], e), max(b[3], y + 1)
        b[4] += e - s
    return [tuple(b) for b in boxes.values()]


def analyze_image(image_path: str, meters_across: float | None = None,
                  pixels_per_meter: float | None = None, wall_thickness_px: int | None = None,
                  min_wall_m: float = 0.5, max_opening_tiles: int = 3,
                  invert: bool = False) -> dict:
    """Detect axis-aligned walls in a floorplan image and return a Sims 4 plan.

    Scale (pick one): ``meters_across`` = real width of the outer walls in metres
    (best: read it off the plan's dimension lines), or ``pixels_per_meter``. With
    neither, scale is guessed from wall thickness and flagged in the result.
    """
    img = Image.open(image_path).convert("L")
    scale_down = max(img.size) / 2000 if max(img.size) > 2000 else 1.0
    if scale_down > 1:
        img = img.resize((round(img.width / scale_down), round(img.height / scale_down)))
    gray = np.asarray(img, dtype=np.uint8)
    if invert:
        gray = 255 - gray
    dark = gray < min(_otsu(gray), 160)

    hrun, vrun = _run_lengths(dark, 1), _run_lengths(dark, 0)
    stroke = np.minimum(hrun, vrun)
    if wall_thickness_px is None:
        vals = stroke[dark]
        vals = vals[(vals >= 3) & (vals <= max(gray.shape) // 15)]
        if vals.size == 0:
            raise ValueError("No wall-like strokes found. Try invert=True or wall_thickness_px.")
        wall_thickness_px = int(np.bincount(vals).argmax())
    T = max(2, int(wall_thickness_px))
    walls = dark & (stroke >= max(2, int(T * 0.55)))

    if pixels_per_meter is None and meters_across is None:
        pixels_per_meter = T / 0.2  # assume ~20 cm walls
        scale_estimated = True
    else:
        scale_estimated = False

    min_len_px = T * 2.5
    hmask = walls & (_run_lengths(walls, 1) >= min_len_px)
    vmask = walls & (_run_lengths(walls, 0) >= min_len_px)
    segs = []  # (orient, a0, a1, c, thickness) in px; a = along axis, c = centre across
    for x0, y0, x1, y1, n in _components(hmask):
        if (x1 - x0) >= (y1 - y0):
            segs.append(("h", x0, x1, (y0 + y1) / 2, y1 - y0))
    for y0, x0, y1, x1, n in _components(vmask.T):
        if (y1 - y0) >= (x1 - x0):
            segs.append(("v", y0, y1, (x0 + x1) / 2, x1 - x0))
    if not segs:
        raise ValueError("No walls detected.")

    vcent = [c for o, a0, a1, c, t in segs if o == "v"]
    hcent = [c for o, a0, a1, c, t in segs if o == "h"]
    ox = min(vcent) if vcent else min(a0 for o, a0, a1, c, t in segs if o == "h")
    oy = min(hcent) if hcent else min(a0 for o, a0, a1, c, t in segs if o == "v")
    if meters_across is not None:
        if len(vcent) >= 2:
            span = max(vcent) - min(vcent)
        else:
            span = max(a1 for o, a0, a1, c, t in segs if o == "h") - ox
        pixels_per_meter = span / meters_across
    ppm = float(pixels_per_meter)

    edges: set[Edge] = set()
    lines: dict[tuple[str, int], list[tuple[int, int]]] = {}
    for o, a0, a1, c, t in segs:
        if (a1 - a0) / ppm < min_wall_m:
            continue
        if o == "h":
            ci = round((c - oy) / ppm)
            s, e = round((a0 - ox) / ppm), round((a1 - ox) / ppm)
        else:
            ci = round((c - ox) / ppm)
            s, e = round((a0 - oy) / ppm), round((a1 - oy) / ppm)
        if e > s:
            lines.setdefault((o, ci), []).append((s, e))

    openings = []
    for (o, ci), spans in lines.items():
        spans.sort()
        merged = [list(spans[0])]
        for s, e in spans[1:]:
            last = merged[-1]
            gap = s - last[1]
            if gap <= 0:
                last[1] = max(last[1], e)
            elif gap <= max_opening_tiles:
                openings.append({"orientation": o, "line": ci, "start": last[1], "width": gap})
                last[1] = max(last[1], e)
            else:
                merged.append([s, e])
        for s, e in merged:
            for k in range(s, e):
                edges.add((o, k, ci) if o == "h" else (o, ci, k))

    plan = finalize_plan(edges, _openings_to_list(openings), source={
        "type": "image", "path": str(image_path), "pixels_per_meter": round(ppm, 2),
        "wall_thickness_px": T, "scale_estimated": scale_estimated})
    if scale_estimated:
        plan["warnings"].insert(0, "Scale was GUESSED from wall thickness. Re-run with meters_across "
                                   "(outer wall-to-wall width in metres) for an accurate size.")
    return plan


def _openings_to_list(raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        if r["orientation"] == "h":
            out.append({"x": r["start"], "y": r["line"], "orientation": "h", "width": r["width"]})
        else:
            out.append({"x": r["line"], "y": r["start"], "orientation": "v", "width": r["width"]})
    return out


# ======================================================================
# Plans from room rectangles (photos, sketches, descriptions)
# ======================================================================
def plan_from_rooms(rooms: list[dict], doors: list[dict] | None = None,
                    windows: list[dict] | None = None, wall_height: str = "medium",
                    stories: int = 1, name: str = "House") -> dict:
    """rooms: [{"name": "Kitchen", "x": 0, "y": 0, "w": 4, "h": 5}, ...] (tiles)
    doors:   [{"between": ["Kitchen", "Living"]}, {"room": "Living", "side": "south"},
              {"x": 3, "y": 5, "orientation": "h"}]
    windows: [{"room": "Bedroom", "side": "north", "count": 2, "width": 1}]
    side = north (top, -y) | south | east (+x) | west
    """
    by_name = {}
    edges: set[Edge] = set()
    for r in rooms:
        x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        if w <= 0 or h <= 0:
            raise ValueError(f"Room {r.get('name')} has non-positive size")
        by_name[r.get("name", f"room{len(by_name)}")] = (x, y, w, h)
        for i in range(w):
            edges.add(("h", x + i, y))
            edges.add(("h", x + i, y + h))
        for j in range(h):
            edges.add(("v", x, y + j))
            edges.add(("v", x + w, y + j))

    # normalise so the footprint starts at (0, 0)
    mx = min(e[1] for e in edges)
    my = min(e[2] for e in edges)
    edges = {(o, a - mx, b - my) for o, a, b in edges}
    by_name = {k: (x - mx, y - my, w, h) for k, (x, y, w, h) in by_name.items()}

    openings = []
    warnings = []

    def side_edges(room, side):
        x, y, w, h = by_name[room]
        return {"north": [("h", x + i, y) for i in range(w)],
                "south": [("h", x + i, y + h) for i in range(w)],
                "west": [("v", x, y + j) for j in range(h)],
                "east": [("v", x + w, y + j) for j in range(h)]}[side]

    def centre_pick(cands, width=1):
        cands = sorted(cands, key=lambda e: (e[1], e[2]))
        if len(cands) < width:
            return None
        i = (len(cands) - width) // 2
        return cands[i]

    for d in doors or []:
        kind = d.get("kind", "door")
        width = int(d.get("width", 1))
        if "between" in d:
            a, b = d["between"]
            if a not in by_name or b not in by_name:
                warnings.append(f"Door between unknown rooms {a}/{b}")
                continue
            shared = []
            for side in ("north", "south", "east", "west"):
                for e in side_edges(a, side):
                    for side2 in ("north", "south", "east", "west"):
                        if e in side_edges(b, side2):
                            shared.append(e)
            e = centre_pick(set(shared), width)
            if not e:
                warnings.append(f"Rooms {a} and {b} share no wall wide enough for a door")
                continue
        elif "room" in d:
            e = centre_pick(side_edges(d["room"], d.get("side", "south")), width)
        else:
            e = (d["orientation"], int(d["x"]), int(d["y"]))
        if e:
            openings.append({"x": e[1], "y": e[2], "orientation": e[0], "width": width, "kind": kind})

    for w in windows or []:
        cands = sorted(side_edges(w["room"], w.get("side", "south")), key=lambda e: (e[1], e[2]))
        count, width = int(w.get("count", 1)), int(w.get("width", 1))
        used = {(o["orientation"], o["x"], o["y"]) for o in openings}
        free = [c for c in cands if c not in used]
        if not free:
            continue
        for k in range(count):  # evenly spaced, centred in equal slices of the wall
            idx = int((k + 0.5) * len(free) / count) - (width // 2)
            e = free[max(0, min(len(free) - width, idx))]
            openings.append({"x": e[1], "y": e[2], "orientation": e[0], "width": width, "kind": "window"})

    plan = finalize_plan(edges, openings, source={"type": "rooms", "name": name},
                         room_names=by_name, wall_height=wall_height, stories=stories)
    plan["warnings"] = warnings + plan["warnings"]
    return plan


# ======================================================================
# Shared: rooms, segments, lot, ASCII, rendering, build guide
# ======================================================================
def finalize_plan(edges: set[Edge], openings: list[dict], source: dict,
                  room_names: dict | None = None, wall_height: str = "medium",
                  stories: int = 1) -> dict:
    if not edges:
        raise ValueError("Plan has no walls")
    W = max([e[1] + (1 if e[0] == "h" else 0) for e in edges])
    D = max([e[2] + (1 if e[0] == "v" else 0) for e in edges])

    # flood fill cells; region touching outside = exterior
    region = -np.ones((D, W), dtype=np.int32)
    rooms = []
    rid = 0
    for sy in range(D):
        for sx in range(W):
            if region[sy, sx] >= 0:
                continue
            q = deque([(sx, sy)])
            region[sy, sx] = rid
            cells, outside = [], False
            while q:
                x, y = q.popleft()
                cells.append((x, y))
                for dx, dy, edge in ((1, 0, ("v", x + 1, y)), (-1, 0, ("v", x, y)),
                                     (0, 1, ("h", x, y + 1)), (0, -1, ("h", x, y))):
                    if edge in edges:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < W and 0 <= ny < D):
                        outside = True
                        continue
                    if region[ny, nx] < 0:
                        region[ny, nx] = rid
                        q.append((nx, ny))
            if not outside:
                xs, ys = [c[0] for c in cells], [c[1] for c in cells]
                rooms.append({"id": rid, "_rid": rid, "name": None, "area_tiles": len(cells),
                              "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
                              "centroid": [round(sum(xs) / len(xs) + 0.5, 1),
                                           round(sum(ys) / len(ys) + 0.5, 1)],
                              "floor_rects": _cells_to_rects(cells)})
            rid += 1

    if room_names:
        for name, (x, y, w, h) in room_names.items():
            cx, cy = x + w // 2, y + h // 2
            if 0 <= cy < D and 0 <= cx < W:
                for r in rooms:
                    if r["_rid"] == region[cy, cx] and not r["name"]:
                        r["name"] = name
    for i, r in enumerate(rooms):
        r["id"] = i
        r["name"] = r["name"] or f"Room {i + 1}"

    # classify openings
    perim = _perimeter_edges(edges, region, rooms, W, D)
    warnings = []
    for o in openings:
        o.setdefault("width", 1)
        exterior = (o["orientation"], o["x"], o["y"]) in perim
        o["exterior"] = exterior
        if "kind" not in o:
            if exterior:
                o["kind"] = "window" if o["width"] >= 2 else "door_or_window"
            else:
                o["kind"] = "door" if o["width"] <= 2 else "archway"
        if o["kind"] == "door_or_window":
            warnings.append(f"Opening at ({o['x']},{o['y']}) on an exterior wall - check: door or window?")

    for r in rooms:
        r.pop("_rid", None)
    segments = merge_edges(edges)
    for seg in segments:
        unit = _seg_edges(seg)
        seg["exterior"] = sum(e in perim for e in unit) * 2 >= len(unit)
    total_area = sum(r["area_tiles"] for r in rooms)
    if not rooms:
        warnings.append("No enclosed rooms found - walls may not connect. Check the preview.")
    return {
        "version": 1, "units": "tiles", "tile_m": 1.0,
        "width": W, "depth": D, "stories": stories, "wall_height": wall_height,
        "walls": segments, "openings": openings, "rooms": rooms,
        "interior_area_tiles": total_area,
        "lot": suggest_lot(W, D), "source": source, "warnings": warnings,
        "ascii": render_ascii(edges, openings, W, D),
    }


def _cells_to_rects(cells) -> list[list[int]]:
    """Merge cells into a few [x0, y0, x1, y1] rectangles (row runs stacked vertically)."""
    rows: dict[int, list[int]] = {}
    for x, y in cells:
        rows.setdefault(y, []).append(x)
    runs = []
    for y in sorted(rows):
        xs = sorted(rows[y])
        start = prev = xs[0]
        for x in xs[1:] + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            runs.append((start, prev + 1, y))
            if x is not None:
                start = prev = x
    rects: list[list[int]] = []
    open_: dict[tuple[int, int], list[int]] = {}
    for x0, x1, y in runs:
        r = open_.get((x0, x1))
        if r and r[3] == y:
            r[3] = y + 1
        else:
            r = [x0, y, x1, y + 1]
            rects.append(r)
            open_[(x0, x1)] = r
    return rects


def _perimeter_edges(edges, region, rooms, W, D) -> set[Edge]:
    inside_cells = np.isin(region, [r["_rid"] for r in rooms])

    def cell_in(x, y):
        return 0 <= x < W and 0 <= y < D and inside_cells[y, x]
    perim = set()
    for o, a, b in edges:
        pair = (cell_in(a, b - 1), cell_in(a, b)) if o == "h" else (cell_in(a - 1, b), cell_in(a, b))
        if pair[0] != pair[1]:
            perim.add((o, a, b))
    return perim


def merge_edges(edges: set[Edge]) -> list[dict]:
    segs = []
    for o in ("h", "v"):
        lines: dict[int, list[int]] = {}
        for e in edges:
            if e[0] != o:
                continue
            along, line = (e[1], e[2]) if o == "h" else (e[2], e[1])
            lines.setdefault(line, []).append(along)
        for line, vals in sorted(lines.items()):
            vals.sort()
            start = prev = vals[0]
            for v in vals[1:] + [None]:
                if v is not None and v == prev + 1:
                    prev = v
                    continue
                if o == "h":
                    segs.append({"x0": start, "y0": line, "x1": prev + 1, "y1": line, "length": prev + 1 - start})
                else:
                    segs.append({"x0": line, "y0": start, "x1": line, "y1": prev + 1, "length": prev + 1 - start})
                if v is not None:
                    start = prev = v
    return segs


def suggest_lot(w: int, d: int, margin: int = 3) -> dict:
    for lw, ld in LOT_SIZES:
        for a, b, rot in ((lw, ld, False), (ld, lw, True)):
            if w + 2 * margin <= a and d + 2 * margin <= b:
                return {"size": f"{lw}x{ld}", "rotate_house": rot, "footprint": f"{w}x{d}"}
    return {"size": "64x64", "rotate_house": False, "footprint": f"{w}x{d}",
            "note": "Footprint is larger than recommended for 64x64; consider bb.moveobjects or splitting."}


def render_ascii(edges, openings, W, D) -> str:
    grid = [[" "] * (2 * W + 1) for _ in range(2 * D + 1)]
    for y in range(D + 1):
        for x in range(W + 1):
            grid[2 * y][2 * x] = "·"
    for o, a, b in edges:
        if o == "h":
            grid[2 * b][2 * a + 1] = "─"
            grid[2 * b][2 * a] = grid[2 * b][2 * a + 2] = "+"
        else:
            grid[2 * b + 1][2 * a] = "│"
            grid[2 * b][2 * a] = grid[2 * b + 2][2 * a] = "+"
    for op in openings:
        ch = {"window": "W", "door": "D", "archway": "A"}.get(op.get("kind"), "?")
        for k in range(op.get("width", 1)):
            if op["orientation"] == "h":
                grid[2 * op["y"]][2 * (op["x"] + k) + 1] = ch
            else:
                grid[2 * (op["y"] + k) + 1][2 * op["x"]] = ch
    return "\n".join("".join(r).rstrip() for r in grid)


def render_png(plan: dict, out_path: str, px: int = 32) -> str:
    W, D = plan["width"], plan["depth"]
    m = px
    img = Image.new("RGB", (W * px + 2 * m, D * px + 2 * m), (250, 250, 247))
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", max(10, px // 3))
    except OSError:
        font = ImageFont.load_default()
    for r in plan["rooms"]:
        x0, y0, x1, y1 = r["bbox"]
        dr.rectangle([m + x0 * px, m + y0 * px, m + x1 * px, m + y1 * px], fill=(232, 240, 250))
    for x in range(W + 1):
        dr.line([m + x * px, m, m + x * px, m + D * px], fill=(215, 215, 215))
    for y in range(D + 1):
        dr.line([m, m + y * px, m + W * px, m + y * px], fill=(215, 215, 215))
    for s in plan["walls"]:
        dr.line([m + s["x0"] * px, m + s["y0"] * px, m + s["x1"] * px, m + s["y1"] * px],
                fill=(40, 40, 40), width=max(3, px // 6))
    colors = {"door": (214, 120, 40), "window": (40, 140, 214), "archway": (120, 180, 60)}
    for o in plan["openings"]:
        c = colors.get(o.get("kind"), (200, 40, 40))
        x, y, w = o["x"], o["y"], o.get("width", 1)
        if o["orientation"] == "h":
            dr.line([m + x * px + 2, m + y * px, m + (x + w) * px - 2, m + y * px], fill=c, width=max(5, px // 4))
        else:
            dr.line([m + x * px, m + y * px + 2, m + x * px, m + (y + w) * px - 2], fill=c, width=max(5, px // 4))
    for r in plan["rooms"]:
        cx, cy = r["centroid"]
        label = f"{r['name']}\n{r['area_tiles']} tiles"
        dr.multiline_text((m + cx * px, m + cy * px), label, fill=(30, 50, 90), font=font, anchor="mm", align="center")
    for x in range(0, W + 1, 5):
        dr.text((m + x * px, m // 2), str(x), fill=(120, 120, 120), font=font, anchor="mm")
    for y in range(0, D + 1, 5):
        dr.text((m // 2, m + y * px), str(y), fill=(120, 120, 120), font=font, anchor="mm")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


WALL_HEIGHT_HINT = {"short": "Short walls", "medium": "Medium walls (default)", "tall": "Tall walls"}


def build_guide(plan: dict) -> str:
    """Step-by-step Sims 4 Build Mode instructions for the plan (Markdown)."""
    lot = plan["lot"]
    ext = [s for s in plan["walls"] if s.get("exterior")]
    inner = [s for s in plan["walls"] if not s.get("exterior")]
    lines = [f"# Sims 4 build guide - {plan['source'].get('name', 'floorplan')}",
             "",
             f"* Footprint: **{plan['width']} x {plan['depth']} tiles**, interior "
             f"{plan['interior_area_tiles']} tiles, {len(plan['rooms'])} rooms.",
             f"* Lot: **{lot['size']}**" + (" (rotate the house 90°)" if lot.get("rotate_house") else ""),
             f"* Wall height: {WALL_HEIGHT_HINT.get(plan['wall_height'], plan['wall_height'])}.",
             "* Coordinates are tiles from the house's top-left (north-west) corner. Pick a "
             "corner tile on the lot as (0,0); the wall tool shows the length while you drag.",
             "", "## 1. Exterior walls"]
    for s in ext:
        lines.append(f"- ({s['x0']},{s['y0']}) → ({s['x1']},{s['y1']}) : {s['length']} tiles")
    lines += ["", "## 2. Interior walls"]
    for s in inner:
        lines.append(f"- ({s['x0']},{s['y0']}) → ({s['x1']},{s['y1']}) : {s['length']} tiles")
    lines += ["", "## 3. Doors, windows and arches"]
    for o in plan["openings"]:
        where = "exterior" if o.get("exterior") else "interior"
        axis = "horizontal wall" if o["orientation"] == "h" else "vertical wall"
        lines.append(f"- {o['kind']} ({o.get('width', 1)} tile) on {where} {axis} at tile "
                     f"({o['x']},{o['y']})")
    lines += ["", "## 4. Rooms (floors & lighting)"]
    for r in plan["rooms"]:
        x0, y0, x1, y1 = r["bbox"]
        lines.append(f"- **{r['name']}**: {r['area_tiles']} tiles, spans ({x0},{y0})-({x1},{y1}) "
                     f"— double-click the floor tool inside to fill; one ceiling light per ~12 tiles.")
    lines += ["", "## 5. Finishing",
              "- Foundation: place it before walls if you want one (Build > Foundation), or add after with bb.moveobjects off.",
              "- Roof: Build > Roof > Auto-roof works for rectangles; use hip roofs for L-shapes.",
              "- Useful cheats (Ctrl+Shift+C, then `testingcheats true`): `bb.moveobjects on`, "
              "`bb.showhiddenobjects`, `bb.enablefreebuild`, `bb.ignoregameplayunlocksentitlement`.",
              "- Save the lot to your Library when finished so it lands in Tray (and is backed up by s4kit)."]
    if plan.get("warnings"):
        lines += ["", "## Review notes"] + [f"- {w}" for w in plan["warnings"]]
    lines += ["", "## Map", "```", plan["ascii"], "```"]
    return "\n".join(lines)


def _seg_edges(s) -> list[Edge]:
    if s["y0"] == s["y1"]:
        return [("h", x, s["y0"]) for x in range(s["x0"], s["x1"])]
    return [("v", s["x0"], y) for y in range(s["y0"], s["y1"])]


def save_plan(plan: dict, out_dir: str, stem: str = "plan") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"{stem}.json"
    jp.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    png = render_png(plan, str(out / f"{stem}.png"))
    md = out / f"{stem}_build_guide.md"
    md.write_text(build_guide(plan), encoding="utf-8")
    return {"json": str(jp), "preview_png": png, "build_guide": str(md)}
