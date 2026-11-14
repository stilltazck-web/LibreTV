"""Mods folder, cache, Tray (saved lots/rooms) and error-log management for The Sims 4."""
from __future__ import annotations

import re
import shutil
import time
import zipfile
from collections import defaultdict
from pathlib import Path

from . import apps
from .dbpf import Package
from .resources import RESOURCE_TYPES

PACKAGE_MAX_DEPTH = 5   # .package files load up to 5 subfolders deep in Mods
SCRIPT_MAX_DEPTH = 1    # .ts4script files load at most 1 subfolder deep

RESOURCE_CFG = """Priority 500
PackedFile *.package
PackedFile */*.package
PackedFile */*/*.package
PackedFile */*/*/*.package
PackedFile */*/*/*/*.package
PackedFile */*/*/*/*/*.package
"""


def mods_dir() -> Path:
    return apps.sims4_docs_dir() / "Mods"


def _depth(p: Path, root: Path) -> int:
    return len(p.relative_to(root).parts) - 1


def list_mods(root: Path | None = None) -> dict:
    root = root or mods_dir()
    if not root.exists():
        return {"mods_dir": str(root), "exists": False}
    items, problems = [], []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".package", ".ts4script", ".zip", ".rar", ".7z"):
            continue
        d = _depth(p, root)
        item = {"path": str(p.relative_to(root)), "size_kb": round(p.stat().st_size / 1024, 1),
                "depth": d, "modified": time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))}
        if p.suffix == ".package" and d > PACKAGE_MAX_DEPTH:
            problems.append(f"{item['path']}: .package deeper than {PACKAGE_MAX_DEPTH} folders - will NOT load")
        if p.suffix == ".ts4script" and d > SCRIPT_MAX_DEPTH:
            problems.append(f"{item['path']}: .ts4script deeper than 1 folder - will NOT load")
        if p.suffix.lower() in (".zip", ".rar", ".7z"):
            problems.append(f"{item['path']}: archive in Mods - extract it (ts4script zips are fine renamed .ts4script)")
        items.append(item)
    if not (root / "Resource.cfg").exists():
        problems.append("Resource.cfg missing from Mods (game recreates it; use repair_mods_folder)")
    return {"mods_dir": str(root), "exists": True, "count": len(items),
            "problems": problems, "items": items[:2000]}


def repair_mods_folder() -> dict:
    root = mods_dir()
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / "Resource.cfg"
    if not cfg.exists():
        cfg.write_text(RESOURCE_CFG, encoding="utf-8")
    return {"ok": True, "resource_cfg": str(cfg)}


def install_mod(source: str, subfolder: str = "s4kit") -> dict:
    """Copy a .package/.ts4script (or extract a .zip of them) into Mods/<subfolder>."""
    src = Path(source)
    if not src.exists():
        return {"ok": False, "error": f"{src} not found"}
    dest_dir = mods_dir() / subfolder
    dest_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                n = Path(name)
                if n.suffix.lower() in (".package", ".ts4script") and ".." not in n.parts:
                    target = dest_dir / n.name
                    with z.open(name) as fsrc, open(target, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    installed.append(str(target))
    elif src.suffix.lower() in (".package", ".ts4script"):
        if src.suffix == ".package":
            Package.load(src)  # validate before copying
        target = dest_dir / src.name
        shutil.copy2(src, target)
        installed.append(str(target))
    else:
        return {"ok": False, "error": "Only .package, .ts4script or .zip supported"}
    return {"ok": True, "installed": installed,
            "note": "Restart the game (or exit to main menu and reload) to pick up changes."}


def clear_cache(include_thumbs: bool = True) -> dict:
    """Delete caches the game rebuilds. Safe; recommended after adding/updating CC."""
    if apps.is_running("TS4_x64.exe"):
        return {"ok": False, "error": "Close The Sims 4 first."}
    docs = apps.sims4_docs_dir()
    removed = []
    targets = [docs / "localthumbcache.package", docs / "avatarcache.package"] if include_thumbs else []
    for t in targets:
        if t.exists():
            t.unlink()
            removed.append(str(t))
    for folder in ("cache", "cachestr", "onlinethumbnailcache", "lotcachedata"):
        f = docs / folder
        if f.is_dir():
            for child in f.iterdir():
                if child.is_file():
                    child.unlink()
                    removed.append(str(child))
    return {"ok": True, "removed": removed}


def read_exceptions(limit: int = 5) -> dict:
    """Return the newest lastException / lastUIException logs (trimmed) for diagnosis."""
    docs = apps.sims4_docs_dir()
    files = sorted(list(docs.glob("lastException*.txt")) + list(docs.glob("lastUIException*.txt"))
                   + list(docs.glob("lastCrash*.txt")), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        text = f.read_text(encoding="utf-8", errors="replace")
        descs = re.findall(r"<desc>(.*?)</desc>", text, re.S)
        mods_mentioned = sorted(set(re.findall(r"Mods[\\/][^\s'\"<>]+", text)))
        out.append({"file": f.name,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                    "mods_mentioned": mods_mentioned[:30],
                    "excerpt": (descs[0] if descs else text)[:4000]})
    return {"docs_dir": str(docs), "logs": out}


def list_tray() -> dict:
    """Group Tray files (your saved lots, rooms and households) by tray item id."""
    tray = apps.sims4_docs_dir() / "Tray"
    if not tray.exists():
        return {"tray_dir": str(tray), "exists": False}
    groups: dict[str, dict] = defaultdict(lambda: {"files": [], "kind": "unknown"})
    for f in tray.iterdir():
        m = re.match(r"0x[0-9a-fA-F]+!(0x[0-9a-fA-F]+)\.(\w+)$", f.name)
        if not m:
            continue
        g = groups[m.group(1)]
        g["files"].append(f.name)
        ext = m.group(2).lower()
        if ext == "blueprint":
            g["kind"] = "lot"
        elif ext == "room":
            g["kind"] = "room"
        elif ext in ("householdbinary", "hhi") and g["kind"] == "unknown":
            g["kind"] = "household"
        if ext == "trayitem":
            g["modified"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            g["name_guess"] = _guess_tray_name(f.read_bytes())
    return {"tray_dir": str(tray), "items": dict(groups)}


def _guess_tray_name(blob: bytes) -> str:
    # .trayitem is protobuf; the item name is the first printable UTF-8 string field
    for m in re.finditer(rb"[\x20-\x7e\xc2-\xf4][\x20-\x7e\x80-\xbf\xc2-\xf4]{2,60}", blob[8:400]):
        s = m.group().decode("utf-8", "ignore").strip()
        if len(s) >= 3:
            return s
    return ""


def backup_tray_item(item_id: str, dest: str) -> dict:
    tray = apps.sims4_docs_dir() / "Tray"
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    copied = [shutil.copy2(f, out / f.name) for f in tray.glob(f"*!{item_id}.*")]
    return {"ok": bool(copied), "copied": [str(c) for c in copied]}


def find_conflicts(root: Path | None = None) -> dict:
    """Detect resources overridden by more than one package in Mods (common cause of bugs)."""
    root = root or mods_dir()
    owners: dict[str, list[str]] = defaultdict(list)
    errors = []
    for p in sorted(root.rglob("*.package")):
        try:
            for res in Package.load(p):
                if res.key.type in (0x220557DA, 0x73E93EEB):  # string tables & manifests overlap harmlessly
                    continue
                owners[str(res.key)].append(str(p.relative_to(root)))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p.name}: {e}")
    conflicts = {k: v for k, v in owners.items() if len(set(v)) > 1}
    by_type = defaultdict(int)
    for k in conflicts:
        by_type[RESOURCE_TYPES.get(int(k.split(":")[0], 16), ("?",))[0]] += 1
    return {"conflict_count": len(conflicts), "by_type": dict(by_type),
            "conflicts": dict(list(conflicts.items())[:200]), "unreadable": errors}
