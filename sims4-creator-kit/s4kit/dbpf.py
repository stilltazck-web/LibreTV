"""Read/write The Sims 4 ``.package`` files (DBPF 2.1).

Pure-python, no dependencies. Supports the compression types the game uses:
uncompressed, zlib (0x5A42) and EA RefPack / "internal" (0xFFFF) for reading;
writes uncompressed or zlib, which the game and Sims 4 Studio both accept.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

HEADER_SIZE = 96

COMP_NONE = 0x0000
COMP_STREAMABLE = 0xFFFE
COMP_INTERNAL = 0xFFFF  # RefPack
COMP_DELETED = 0xFFE0
COMP_ZLIB = 0x5A42


@dataclass(frozen=True, order=True)
class ResourceKey:
    type: int
    group: int
    instance: int

    def __str__(self) -> str:  # S4S / s4pe style "T:G:I"
        return f"{self.type:08X}:{self.group:08X}:{self.instance:016X}"

    @classmethod
    def parse(cls, text: str) -> "ResourceKey":
        parts = text.replace("-", ":").replace("!", ":").split(":")
        if len(parts) != 3:
            raise ValueError(f"Expected TYPE:GROUP:INSTANCE hex, got {text!r}")
        return cls(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16))


@dataclass
class Resource:
    key: ResourceKey
    _raw: bytes = b""          # bytes exactly as stored in the file
    compression: int = COMP_NONE
    mem_size: int = 0
    _data: bytes | None = None  # decompressed cache

    @property
    def data(self) -> bytes:
        if self._data is None:
            self._data = decompress(self._raw, self.compression, self.mem_size)
        return self._data

    @data.setter
    def data(self, value: bytes) -> None:
        self._data = bytes(value)
        self._raw = b""
        self.compression = COMP_NONE
        self.mem_size = len(value)


@dataclass
class Package:
    resources: dict[ResourceKey, Resource] = field(default_factory=dict)
    major: int = 2
    minor: int = 1

    # ---------------------------------------------------------------- reading
    @classmethod
    def load(cls, path: str | Path) -> "Package":
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, buf: bytes) -> "Package":
        if len(buf) < HEADER_SIZE or buf[:4] != b"DBPF":
            raise ValueError("Not a DBPF package (bad magic)")
        major, minor = struct.unpack_from("<II", buf, 4)
        if major != 2:
            raise ValueError(f"Unsupported DBPF version {major}.{minor} (Sims 4 uses 2.1)")
        count = struct.unpack_from("<I", buf, 36)[0]
        index_pos = struct.unpack_from("<I", buf, 64)[0] or struct.unpack_from("<I", buf, 40)[0]
        pkg = cls(major=major, minor=minor)
        if count == 0:
            return pkg
        pos = index_pos
        flags = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
        const = {}
        for bit, name in ((1, "type"), (2, "group"), (4, "ihi")):
            if flags & bit:
                const[name] = struct.unpack_from("<I", buf, pos)[0]
                pos += 4
        for _ in range(count):
            vals = {}
            for name in ("type", "group", "ihi"):
                if name in const:
                    vals[name] = const[name]
                else:
                    vals[name] = struct.unpack_from("<I", buf, pos)[0]
                    pos += 4
            ilo, offset, fsize, msize = struct.unpack_from("<IIII", buf, pos)
            pos += 16
            comp = COMP_NONE
            if fsize & 0x80000000:
                comp, _committed = struct.unpack_from("<HH", buf, pos)
                pos += 4
                fsize &= 0x7FFFFFFF
            elif fsize != msize:
                comp = COMP_ZLIB
            if comp == COMP_DELETED:
                continue
            key = ResourceKey(vals["type"], vals["group"], (vals["ihi"] << 32) | ilo)
            pkg.resources[key] = Resource(key, buf[offset:offset + fsize], comp, msize)
        return pkg

    # ---------------------------------------------------------------- editing
    def __iter__(self) -> Iterator[Resource]:
        return iter(sorted(self.resources.values(), key=lambda r: r.key))

    def __len__(self) -> int:
        return len(self.resources)

    def get(self, key: ResourceKey) -> Resource | None:
        return self.resources.get(key)

    def find(self, type_id: int | None = None, group: int | None = None,
             instance: int | None = None) -> list[Resource]:
        return [r for r in self if (type_id is None or r.key.type == type_id)
                and (group is None or r.key.group == group)
                and (instance is None or r.key.instance == instance)]

    def put(self, key: ResourceKey, data: bytes) -> Resource:
        res = Resource(key)
        res.data = data
        self.resources[key] = res
        return res

    def remove(self, key: ResourceKey) -> bool:
        return self.resources.pop(key, None) is not None

    def merge(self, other: "Package", overwrite: bool = True) -> int:
        added = 0
        for res in other:
            if overwrite or res.key not in self.resources:
                self.resources[res.key] = res
                added += 1
        return added

    # ---------------------------------------------------------------- writing
    def to_bytes(self, compress: bool = True) -> bytes:
        body = bytearray()
        entries = []
        for res in self:
            if res._raw and res.compression in (COMP_NONE, COMP_ZLIB, COMP_INTERNAL):
                raw, comp, msize = res._raw, res.compression, res.mem_size
            else:
                data = res.data
                comp, raw, msize = COMP_NONE, data, len(data)
                if compress and len(data) > 64:
                    z = zlib.compress(data, 9)
                    if len(z) < len(data):
                        comp, raw = COMP_ZLIB, z
            entries.append((res.key, HEADER_SIZE + len(body), len(raw), msize, comp))
            body += raw
        index = bytearray(struct.pack("<I", 0))  # no constant fields
        for key, off, fsize, msize, comp in entries:
            index += struct.pack("<IIIIIIIHH", key.type, key.group, key.instance >> 32,
                                 key.instance & 0xFFFFFFFF, off, fsize | 0x80000000,
                                 msize, comp, 1)
        index_pos = HEADER_SIZE + len(body)
        header = bytearray(HEADER_SIZE)
        header[0:4] = b"DBPF"
        struct.pack_into("<II", header, 4, self.major, self.minor)
        struct.pack_into("<III", header, 36, len(entries), 0, len(index))
        struct.pack_into("<I", header, 60, 3)          # index minor version
        struct.pack_into("<I", header, 64, index_pos)
        return bytes(header + body + index)

    def save(self, path: str | Path, compress: bool = True) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(self.to_bytes(compress))
        tmp.replace(path)
        return path


# -------------------------------------------------------------- compression
def decompress(raw: bytes, comp: int, mem_size: int = 0) -> bytes:
    if comp == COMP_NONE:
        return bytes(raw)
    if comp == COMP_ZLIB:
        return zlib.decompress(raw)
    if comp == COMP_INTERNAL:
        return refpack_decompress(raw)
    if comp == COMP_STREAMABLE:
        raise NotImplementedError("Streamable compression (0xFFFE) is not used by TS4 CC")
    raise ValueError(f"Unknown compression type 0x{comp:04X}")


def refpack_decompress(data: bytes) -> bytes:
    """EA RefPack (QFS) decoder, as used for 0xFFFF 'internal' compression."""
    if len(data) < 2 or data[1] != 0xFB:
        raise ValueError("Not RefPack data")
    flags = data[0]
    if flags & 0x80:
        size = int.from_bytes(data[2:6], "big")
        pos = 6
    else:
        size = int.from_bytes(data[2:5], "big")
        pos = 5
    out = bytearray()
    n = len(data)
    while pos < n:
        b0 = data[pos]
        stop = False
        if b0 < 0x80:
            b1 = data[pos + 1]
            pos += 2
            plain = b0 & 0x03
            copy_len = ((b0 & 0x1C) >> 2) + 3
            copy_off = ((b0 & 0x60) << 3) + b1 + 1
        elif b0 < 0xC0:
            b1, b2 = data[pos + 1], data[pos + 2]
            pos += 3
            plain = (b1 >> 6) & 0x03
            copy_len = (b0 & 0x3F) + 4
            copy_off = ((b1 & 0x3F) << 8) + b2 + 1
        elif b0 < 0xE0:
            b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
            pos += 4
            plain = b0 & 0x03
            copy_len = ((b0 & 0x0C) << 6) + b3 + 5
            copy_off = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1
        elif b0 < 0xFC:
            pos += 1
            plain = ((b0 & 0x1F) << 2) + 4
            copy_len = copy_off = 0
        else:
            pos += 1
            plain = b0 & 0x03
            copy_len = copy_off = 0
            stop = True
        out += data[pos:pos + plain]
        pos += plain
        if copy_len:
            start = len(out) - copy_off
            if start < 0:
                raise ValueError("Corrupt RefPack stream (bad back-reference)")
            for i in range(copy_len):  # byte-wise: ranges may overlap
                out.append(out[start + i])
        if stop:
            break
    if size and len(out) != size:
        raise ValueError(f"RefPack size mismatch: expected {size}, got {len(out)}")
    return bytes(out)


def refpack_store(data: bytes) -> bytes:
    """Encode as literal-only RefPack (valid, uncompressed). Mainly for tests/interop."""
    out = bytearray([0x10, 0xFB]) + len(data).to_bytes(3, "big")
    pos = 0
    while len(data) - pos >= 4:
        chunk = min(112, (len(data) - pos) // 4 * 4)
        out.append(0xE0 + (chunk - 4) // 4)
        out += data[pos:pos + chunk]
        pos += chunk
    rest = len(data) - pos
    out.append(0xFC + rest)
    out += data[pos:]
    return bytes(out)


def iter_packages(root: str | Path) -> Iterable[Path]:
    return sorted(Path(root).rglob("*.package"))
