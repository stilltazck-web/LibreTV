"""Sims 4 resource type IDs, FNV hashing and String Table (STBL) support."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Commonly used Sims 4 resource types (type id -> (tag, description))
RESOURCE_TYPES: dict[int, tuple[str, str]] = {
    0xC0DB5AE7: ("OBJD", "Object Definition"),
    0x319E4F1D: ("COBJ", "Catalog Object (price, tags, catalog info)"),
    0x01661233: ("MODL", "Model (LOD list)"),
    0x01D10F34: ("MLOD", "Model LOD (geometry container)"),
    0x01D0E75D: ("MATD", "Material Definition"),
    0x02019972: ("MTST", "Material Set (swatches/states)"),
    0xD382BF57: ("FTPT", "Footprint"),
    0xD3044521: ("RSLT", "Slot (routing/placement slots)"),
    0x03B4C61D: ("LITE", "Light"),
    0x00B2D882: ("DST", "DXT Image (diffuse/spec textures)"),
    0x3453CF95: ("RLE2", "RLE2 Image"),
    0xBA856C78: ("RLES", "RLES Image (specular)"),
    0x2F7D0004: ("PNG", "PNG Image"),
    0x3C1AF1F2: ("THUM", "Object Thumbnail (PNG/JFIF)"),
    0x5B282D45: ("THUM", "Thumbnail"),
    0xCD9DE247: ("PNG", "PNG Image"),
    0x220557DA: ("STBL", "String Table"),
    0x0333406C: ("XML", "Tuning XML"),
    0x62E94D38: ("XML", "Combined Tuning"),
    0x545AC67A: ("DATA", "SimData"),
    0x034AEECB: ("CASP", "CAS Part"),
    0x015A1849: ("GEOM", "Body Geometry (CAS)"),
    0x0354796A: ("TONE", "Skin Tone"),
    0x9D1AB874: ("SCRIPT", "Python script module"),
    0x73E93EEB: ("MANIFEST", "Package manifest"),
    0x8EAF13DE: ("RIG", "Rig / skeleton"),
    0xD5F0F921: ("CWAL", "Catalog Wall Pattern"),
    0xB4F762C9: ("CFLR", "Catalog Floor Pattern"),
    0x9151E6BC: ("CWST", "Catalog Wall Style"),
    0xA057811C: ("CFNC", "Catalog Fence"),
    0x1C1CF1F7: ("CRAL", "Catalog Railing"),
    0x0418FE2A: ("CFEN", "Catalog Fence"),
    0x2FAE983E: ("CFND", "Catalog Foundation"),
    0x91EDBD3E: ("CRMT", "Catalog Roof Trim"),
    0xF1EDBD86: ("CRPT", "Catalog Roof Pattern"),
    0x3F0C529A: ("CSPN", "Catalog Spandrel"),
    0x9A20CD1C: ("CSTR", "Catalog Stairs"),
    0x07936CE0: ("CTPT", "Catalog Terrain Paint"),
}

STBL_TYPE = 0x220557DA
OBJD_TYPE = 0xC0DB5AE7
COBJ_TYPE = 0x319E4F1D

# High byte of an STBL instance id = language
LANGUAGES = {
    0x00: "ENG_US", 0x02: "CHI_CN", 0x03: "CHI_TW", 0x04: "CZE_CZ", 0x05: "DAN_DK",
    0x06: "DUT_NL", 0x07: "FIN_FI", 0x08: "FRE_FR", 0x09: "GER_DE", 0x0B: "ITA_IT",
    0x0C: "JPN_JP", 0x0D: "KOR_KR", 0x0E: "NOR_NO", 0x0F: "POL_PL", 0x11: "POR_BR",
    0x12: "RUS_RU", 0x13: "SPA_ES", 0x15: "SPA_MX", 0x16: "SWE_SE",
}


def type_name(type_id: int) -> str:
    tag, desc = RESOURCE_TYPES.get(type_id, ("?", "Unknown"))
    return f"{tag} ({desc})"


# ------------------------------------------------------------------ hashing
def fnv32(text: str, high_bit: bool = False) -> int:
    """FNV-1 32-bit over the lower-cased UTF-8 string (Sims 4 convention)."""
    h = 0x811C9DC5
    for b in text.lower().encode("utf-8"):
        h = (h * 0x01000193) & 0xFFFFFFFF
        h ^= b
    return h | 0x80000000 if high_bit else h


def fnv64(text: str, high_bit: bool = False) -> int:
    """FNV-1 64-bit. ``high_bit=True`` marks it as custom content (S4S does this)."""
    h = 0xCBF29CE484222325
    for b in text.lower().encode("utf-8"):
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        h ^= b
    return h | 0x8000000000000000 if high_bit else h


def new_instance_id(creator: str, item: str) -> int:
    """Deterministic, collision-resistant instance id for new CC: fnv64('creator:item') + CC bit."""
    return fnv64(f"{creator}:{item}", high_bit=True)


# ------------------------------------------------------------------ STBL
@dataclass
class StringTable:
    entries: dict[int, str] = field(default_factory=dict)
    flags: dict[int, int] = field(default_factory=dict)
    version: int = 5

    @classmethod
    def from_bytes(cls, data: bytes) -> "StringTable":
        if data[:4] != b"STBL":
            raise ValueError("Not a STBL resource")
        version, _compressed, count = struct.unpack_from("<HBQ", data, 4)
        pos = 4 + 2 + 1 + 8 + 2 + 4  # magic, ver, compressed, count, reserved, strlen
        tbl = cls(version=version)
        for _ in range(count):
            key, flags, length = struct.unpack_from("<IBH", data, pos)
            pos += 7
            tbl.entries[key] = data[pos:pos + length].decode("utf-8", errors="replace")
            tbl.flags[key] = flags
            pos += length
        return tbl

    def to_bytes(self) -> bytes:
        body = bytearray()
        total = 0
        for key, text in self.entries.items():
            raw = text.encode("utf-8")
            body += struct.pack("<IBH", key, self.flags.get(key, 0), len(raw)) + raw
            total += len(raw) + 1
        head = b"STBL" + struct.pack("<HBQ", self.version, 0, len(self.entries))
        head += b"\x00\x00" + struct.pack("<I", total)
        return head + bytes(body)

    def set(self, text_or_key: str | int, value: str) -> int:
        key = text_or_key if isinstance(text_or_key, int) else fnv32(text_or_key)
        self.entries[key] = value
        return key


def stbl_instance(base_name: str, language: int = 0x00) -> int:
    """STBL instance id: language in the top byte, 56-bit hash below (CC bit kept off)."""
    return (language << 56) | (fnv64(base_name) & 0x00FFFFFFFFFFFFFF)
