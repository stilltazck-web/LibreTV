import json
import socket
import struct
import threading
import zlib

import pytest
from PIL import Image, ImageDraw

from s4kit import floorplan, object_spec
from s4kit.blender_client import BlenderClient, BlenderError
from s4kit.dbpf import (COMP_INTERNAL, Package, Resource, ResourceKey, refpack_decompress,
                        refpack_store)
from s4kit.resources import STBL_TYPE, StringTable, fnv32, fnv64, stbl_instance


# ----------------------------------------------------------------- hashing
def test_fnv_known_values():
    # FNV-1 reference values for the empty string and "a"
    assert fnv32("") == 0x811C9DC5
    assert fnv32("a") == 0x050C5D7E
    assert fnv64("") == 0xCBF29CE484222325
    assert fnv64("a") == 0xAF63BD4C8601B7BE
    assert fnv64("A") == fnv64("a")  # Sims hashes are case-insensitive
    assert fnv64("x", high_bit=True) >> 63 == 1


# ----------------------------------------------------------------- DBPF
def test_package_roundtrip(tmp_path):
    pkg = Package()
    k1 = ResourceKey(0xC0DB5AE7, 0, 0x8000000000001234)
    k2 = ResourceKey(0x00B2D882, 0x80000000, 0xDEADBEEFCAFEF00D)
    pkg.put(k1, b"hello" * 100)
    pkg.put(k2, bytes(range(256)) * 4)
    out = tmp_path / "t.package"
    pkg.save(out)
    raw = out.read_bytes()
    assert raw[:4] == b"DBPF" and struct.unpack_from("<II", raw, 4) == (2, 1)
    back = Package.load(out)
    assert len(back) == 2
    assert back.get(k1).data == b"hello" * 100
    assert back.get(k2).data == bytes(range(256)) * 4
    # re-save keeps stored bytes verbatim
    assert Package.from_bytes(back.to_bytes()).get(k1).data == b"hello" * 100


def test_index_with_constant_type_and_refpack():
    """Hand-build a package whose index uses a constant type field and RefPack data."""
    payload = b"The Sims 4 RefPack test payload!!" * 3
    rp = refpack_store(payload)
    header = bytearray(96)
    header[0:4] = b"DBPF"
    struct.pack_into("<II", header, 4, 2, 1)
    body = rp
    idx = struct.pack("<II", 1, 0x220557DA)  # flags: type constant
    idx += struct.pack("<IIIIIIHH", 0, 0x00000001, 0x00000002, 96, len(rp) | 0x80000000,
                       len(payload), COMP_INTERNAL, 1)
    struct.pack_into("<III", header, 36, 1, 0, len(idx))
    struct.pack_into("<I", header, 64, 96 + len(body))
    pkg = Package.from_bytes(bytes(header) + body + idx)
    (res,) = list(pkg)
    assert res.key == ResourceKey(0x220557DA, 0, 0x0000000100000002)
    assert res.data == payload


def test_refpack_backreference():
    # literal "abcd" (0xE0 = 4 literals) then short copy: 2 plain 'ef', copy 5 bytes from offset 6
    b0 = 0x00 | 2 | ((5 - 3) << 2)            # plain=2, copy_len=5, offset high bits 0
    stream = bytes([0x10, 0xFB, 0, 0, 11, 0xE0]) + b"abcd" + bytes([b0, 5]) + b"ef" + bytes([0xFC])
    assert refpack_decompress(stream) == b"abcdefabcde"


def test_zlib_resource_detected_without_extended_flag():
    data = b"x" * 500
    z = zlib.compress(data)
    header = bytearray(96)
    header[0:4] = b"DBPF"
    struct.pack_into("<II", header, 4, 2, 1)
    idx = struct.pack("<I", 0) + struct.pack("<IIIIIII", 1, 2, 3, 4, 96, len(z), len(data))
    struct.pack_into("<III", header, 36, 1, 0, len(idx))
    struct.pack_into("<I", header, 64, 96 + len(z))
    assert list(Package.from_bytes(bytes(header) + z + idx))[0].data == data


def test_resource_key_parse():
    k = ResourceKey.parse("220557DA:80000000:0011223344556677")
    assert str(k) == "220557DA:80000000:0011223344556677"


# ----------------------------------------------------------------- STBL
def test_stbl_roundtrip():
    t = StringTable()
    key = t.set("myname:sofa:name", "Velvet Sofa ✨")
    t.set(0x12345678, "Description")
    back = StringTable.from_bytes(t.to_bytes())
    assert back.entries[key] == "Velvet Sofa ✨"
    assert back.entries[0x12345678] == "Description"
    assert stbl_instance("x", 0x08) >> 56 == 0x08


# ----------------------------------------------------------------- floorplans
def _draw_plan(path, scale=40, T=8):
    """10m x 6m house: wall at x=6 splits it; door gap in the divider; window gap on north wall."""
    W, D = 10, 6
    img = Image.new("L", (W * scale + 80, D * scale + 80), 255)
    d = ImageDraw.Draw(img)
    o = 40

    def wall(x0, y0, x1, y1):
        d.rectangle([o + x0 * scale - T // 2, o + y0 * scale - T // 2,
                     o + x1 * scale + T // 2, o + y1 * scale + T // 2], fill=0)
    wall(0, 0, 2, 0); wall(4, 0, 10, 0)            # north wall with 2 m window gap
    wall(0, 6, 10, 6)                              # south
    wall(0, 0, 0, 6); wall(10, 0, 10, 6)           # west / east
    wall(6, 0, 6, 2); wall(6, 3, 6, 6)             # divider with 1 m door gap
    d.text((o + 50, o + 100), "LIVING 6x6", fill=0)  # thin text must be ignored
    d.line([o, o + D * scale + 20, o + W * scale, o + D * scale + 20], fill=0, width=1)  # dimension line
    img.save(path)


def test_analyze_floorplan_image(tmp_path):
    p = tmp_path / "plan.png"
    _draw_plan(p)
    plan = floorplan.analyze_image(str(p), meters_across=10)
    assert (plan["width"], plan["depth"]) == (10, 6)
    assert sorted(r["area_tiles"] for r in plan["rooms"]) == [24, 36]
    kinds = sorted((o["kind"], o["width"], o["exterior"]) for o in plan["openings"])
    assert kinds == [("door", 1, False), ("window", 2, True)]
    assert sum(s["exterior"] for s in plan["walls"]) == 4
    files = floorplan.save_plan(plan, str(tmp_path / "out"), "t")
    guide = (tmp_path / "out" / "t_build_guide.md").read_text()
    assert "Exterior walls" in guide and "20x15" in guide
    assert Image.open(files["preview_png"]).size[0] > 100


def test_analyze_without_scale_warns(tmp_path):
    p = tmp_path / "plan.png"
    _draw_plan(p)
    plan = floorplan.analyze_image(str(p))
    assert plan["source"]["scale_estimated"] and "GUESSED" in plan["warnings"][0]


def test_plan_from_rooms_l_shape():
    plan = floorplan.plan_from_rooms(
        rooms=[{"name": "Living", "x": 0, "y": 0, "w": 6, "h": 5},
               {"name": "Kitchen", "x": 6, "y": 0, "w": 4, "h": 5},
               {"name": "Bedroom", "x": 0, "y": 5, "w": 5, "h": 4}],
        doors=[{"between": ["Living", "Kitchen"]}, {"between": ["Living", "Bedroom"]},
               {"room": "Living", "side": "north"}],
        windows=[{"room": "Bedroom", "side": "west", "count": 2}])
    names = {r["name"]: r["area_tiles"] for r in plan["rooms"]}
    assert names == {"Living": 30, "Kitchen": 20, "Bedroom": 20}
    kinds = [o["kind"] for o in plan["openings"]]
    assert kinds.count("door") == 3 and kinds.count("window") == 2
    front = [o for o in plan["openings"] if o["kind"] == "door" and o["exterior"]]
    assert len(front) == 1 and front[0]["y"] == 0
    # L-shape: the wall at y=5 from x=5..10 is exterior, x=0..5 is interior
    assert plan["lot"]["size"] == "20x15"
    assert "D" in plan["ascii"] and "W" in plan["ascii"]


def test_cells_to_rects_covers_l_room():
    cells = [(x, y) for y in range(3) for x in range(2)] + [(2, 2), (3, 2)]
    rects = floorplan._cells_to_rects(cells)
    assert sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects) == len(cells)


# ----------------------------------------------------------------- object spec
def test_object_check():
    bad = object_spec.check_object("small_decor", 4000, has_uv1=False, texture_size=1000)
    assert not bad["ok"] and len(bad["issues"]) == 3
    good = object_spec.check_object("large_furniture", 3500, 1500, 1024, True)
    assert good["ok"]


# ----------------------------------------------------------------- Blender bridge protocol
def test_blender_client_against_fake_bridge():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        for _ in range(2):  # the client opens one connection per call
            conn, _ = srv.accept()
            with conn, conn.makefile("rwb") as f:
                for line in f:
                    req = json.loads(line)
                    resp = ({"ok": True, "result": {"pong": True, "params": req["params"]}}
                            if req["cmd"] == "ping" else {"ok": False, "error": "nope"})
                    f.write((json.dumps(resp) + "\n").encode())
                    f.flush()
    threading.Thread(target=serve, daemon=True).start()
    c = BlenderClient(port=port)
    assert c.call("ping", a=1) == {"pong": True, "params": {"a": 1}}
    with pytest.raises(BlenderError):
        c.call("other")
    srv.close()


def test_blender_client_unreachable():
    with pytest.raises(BlenderError, match="Start Blender"):
        BlenderClient(port=1).call("ping")


# ----------------------------------------------------------------- MCP server
def test_server_registers_tools():
    import asyncio
    from s4kit import server
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    for n in ("environment_report", "floorplan_analyze", "floorplan_from_rooms",
              "blender_build_house", "package_set_string", "mods_install", "sims4_launch"):
        assert n in names
    assert len(names) >= 35


def test_package_set_string_tool(tmp_path):
    from s4kit import server
    p = tmp_path / "obj.package"
    pkg = Package()
    pkg.put(ResourceKey(STBL_TYPE, 0x80000000, stbl_instance("obj")), StringTable().to_bytes())
    pkg.put(ResourceKey(STBL_TYPE, 0x80000000, stbl_instance("obj", 0x08)), StringTable().to_bytes())
    pkg.save(p)
    res = json.loads(server.package_set_string(str(p), "0x0000BEEF", "Luxe Lamp"))
    assert len(res["tables_updated"]) == 2
    for r in Package.load(p).find(type_id=STBL_TYPE):
        assert StringTable.from_bytes(r.data).entries[0xBEEF] == "Luxe Lamp"
