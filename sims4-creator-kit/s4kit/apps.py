"""PC connectors: locate and launch The Sims 4, Sims 4 Studio and Blender.

Every path can be overridden with an environment variable, which is the most
reliable option on machines with custom install locations or OneDrive Documents:

    S4KIT_SIMS4_DIR   game install folder (contains Game/Bin/TS4_x64.exe)
    S4KIT_SIMS4_DOCS  "Documents/Electronic Arts/The Sims 4" user folder
    S4KIT_S4S_EXE     path to S4Studio.exe
    S4KIT_BLENDER_EXE path to blender.exe
"""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
STEAM_APP_ID = "1222670"


# ---------------------------------------------------------------- helpers
def _first_existing(paths) -> Path | None:
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return None


def _win_documents() -> Path:
    if IS_WIN:
        try:  # honours OneDrive / relocated Documents
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def _win_registry(path: str, value: str) -> str | None:
    if not IS_WIN:
        return None
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as k:
                    return winreg.QueryValueEx(k, value)[0]
            except OSError:
                continue
    return None


def _spawn(args: list[str], cwd: str | None = None) -> int:
    kwargs = {"cwd": cwd, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WIN:
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs).pid


def _open_uri(uri: str) -> None:
    if IS_WIN:
        os.startfile(uri)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", uri])
    else:
        subprocess.Popen(["xdg-open", uri])


def running_processes() -> list[str]:
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                                 text=True, timeout=15).stdout
            return [line.split('","')[0].strip('"') for line in out.splitlines() if line]
        out = subprocess.run(["ps", "-axo", "comm"], capture_output=True, text=True,
                             timeout=15).stdout
        return [Path(line.strip()).name for line in out.splitlines()[1:]]
    except Exception:
        return []


def is_running(*names: str) -> bool:
    procs = {p.lower() for p in running_processes()}
    return any(n.lower() in procs for n in names)


# ---------------------------------------------------------------- The Sims 4
def sims4_install_dir() -> Path | None:
    env = os.environ.get("S4KIT_SIMS4_DIR")
    if env:
        return Path(env)
    reg = _win_registry(r"SOFTWARE\Maxis\The Sims 4", "Install Dir")
    candidates = [reg] if reg else []
    if IS_WIN:
        candidates += [
            r"C:\Program Files\EA Games\The Sims 4",
            r"C:\Program Files (x86)\Origin Games\The Sims 4",
            r"C:\Program Files (x86)\Steam\steamapps\common\The Sims 4",
            r"C:\Program Files\Steam\steamapps\common\The Sims 4",
        ]
        candidates += glob.glob(r"[D-Z]:\*\steamapps\common\The Sims 4")
        candidates += glob.glob(r"[D-Z]:\*\The Sims 4")
    elif IS_MAC:
        candidates += ["/Applications/The Sims 4.app",
                       str(Path.home() / "Applications/The Sims 4.app")]
    return _first_existing(candidates)


def sims4_exe() -> Path | None:
    root = sims4_install_dir()
    if not root:
        return None
    if IS_MAC and root.suffix == ".app":
        return root
    return _first_existing([root / "Game" / "Bin" / "TS4_x64.exe",
                            root / "Game-cracked" / "Bin" / "TS4_x64.exe"])


def sims4_docs_dir() -> Path:
    env = os.environ.get("S4KIT_SIMS4_DOCS")
    if env:
        return Path(env)
    base = _win_documents() / "Electronic Arts"
    found = _first_existing([base / "The Sims 4"] + glob.glob(str(base / "*Sims 4*")))
    return found or base / "The Sims 4"


def sims4_status() -> dict:
    docs = sims4_docs_dir()
    exe = sims4_exe()
    return {
        "install_dir": str(sims4_install_dir() or ""),
        "exe": str(exe or ""),
        "docs_dir": str(docs),
        "docs_exists": docs.exists(),
        "mods_dir": str(docs / "Mods"),
        "tray_dir": str(docs / "Tray"),
        "running": is_running("TS4_x64.exe", "TS4.exe", "The Sims 4"),
        "options": read_options(),
        "platform": platform.platform(),
    }


def launch_sims4(method: str = "auto", extra_args: list[str] | None = None) -> dict:
    """method: auto | exe | steam.  'auto' uses the exe if found (EA app installs), else Steam."""
    exe = sims4_exe()
    if method in ("auto", "exe") and exe:
        if IS_MAC:
            subprocess.Popen(["open", "-a", str(exe)])
            return {"launched": True, "via": "app", "path": str(exe)}
        pid = _spawn([str(exe), *(extra_args or [])], cwd=str(exe.parent))
        return {"launched": True, "via": "exe", "pid": pid, "path": str(exe),
                "note": "The EA app / Steam must be installed and signed in; it will hand off."}
    if method in ("auto", "steam"):
        _open_uri(f"steam://rungameid/{STEAM_APP_ID}")
        return {"launched": True, "via": "steam"}
    return {"launched": False, "error": "Sims 4 executable not found; set S4KIT_SIMS4_DIR"}


# Options.ini --------------------------------------------------------------
def read_options() -> dict:
    ini = sims4_docs_dir() / "Options.ini"
    if not ini.exists():
        return {}
    text = ini.read_text(encoding="utf-8", errors="replace")
    keys = ("modsdisabled", "scriptmodsenabled", "resourcelistenabled", "fullscreen",
            "resolutionwidth", "resolutionheight")
    out = {}
    for k in keys:
        m = re.search(rf"^\s*{k}\s*=\s*(\S+)", text, re.M | re.I)
        if m:
            out[k] = m.group(1)
    return out


def set_mods_enabled(enabled: bool = True, script_mods: bool = True) -> dict:
    """Edit Options.ini so CC and script mods load. Game must be closed (it rewrites the file)."""
    ini = sims4_docs_dir() / "Options.ini"
    if not ini.exists():
        return {"ok": False, "error": f"{ini} not found - start the game once first."}
    if is_running("TS4_x64.exe"):
        return {"ok": False, "error": "Close The Sims 4 first; it overwrites Options.ini on exit."}
    text = ini.read_text(encoding="utf-8", errors="replace")
    shutil.copy2(ini, ini.with_suffix(".ini.s4kit.bak"))
    for key, val in (("modsdisabled", "0" if enabled else "1"),
                     ("scriptmodsenabled", "1" if script_mods else "0")):
        if re.search(rf"^\s*{key}\s*=", text, re.M | re.I):
            text = re.sub(rf"^(\s*{key}\s*=\s*)\S*", rf"\g<1>{val}", text, flags=re.M | re.I)
        else:
            text = re.sub(r"^\[options\]\s*$", f"[options]\n{key} = {val}", text,
                          count=1, flags=re.M | re.I)
    ini.write_text(text, encoding="utf-8")
    return {"ok": True, "options": read_options()}


# ---------------------------------------------------------------- Sims 4 Studio
def s4s_exe() -> Path | None:
    env = os.environ.get("S4KIT_S4S_EXE")
    if env:
        return Path(env)
    if not IS_WIN:
        return None  # S4S is Windows-only (runs on Mac via its own wrapper build)
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"),
          os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
          os.environ.get("LOCALAPPDATA", "")]
    candidates = []
    for base in pf:
        candidates += glob.glob(os.path.join(base, "Sims 4 Studio*", "S4Studio.exe"))
        candidates += glob.glob(os.path.join(base, "*", "Sims 4 Studio*", "S4Studio.exe"))
    return _first_existing(sorted(candidates, reverse=True))


def launch_s4s(package: str | None = None) -> dict:
    exe = s4s_exe()
    if not exe:
        return {"launched": False, "error": "S4Studio.exe not found; set S4KIT_S4S_EXE"}
    args = [str(exe)] + ([str(Path(package).resolve())] if package else [])
    pid = _spawn(args, cwd=str(exe.parent))
    return {"launched": True, "pid": pid, "exe": str(exe), "opened": package}


# ---------------------------------------------------------------- Blender
def blender_exe() -> Path | None:
    env = os.environ.get("S4KIT_BLENDER_EXE")
    if env:
        return Path(env)
    if IS_WIN:
        found = glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe")
        found += glob.glob(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")

        def ver(p):
            m = re.search(r"Blender (\d+)\.(\d+)", p)
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        found.sort(key=ver, reverse=True)
        return _first_existing(found)
    if IS_MAC:
        return _first_existing(["/Applications/Blender.app/Contents/MacOS/Blender"])
    w = shutil.which("blender")
    return Path(w) if w else None


ADDON_PATH = Path(__file__).resolve().parent.parent / "blender_addon" / "s4kit_bridge.py"


def launch_blender(blend_file: str | None = None, start_bridge: bool = True,
                   port: int = 9876) -> dict:
    exe = blender_exe()
    if not exe:
        return {"launched": False, "error": "Blender not found; set S4KIT_BLENDER_EXE"}
    args = [str(exe)]
    if blend_file:
        args.append(str(Path(blend_file).resolve()))
    if start_bridge:
        args += ["--python", str(ADDON_PATH), "--", f"--s4kit-port={port}"]
    pid = _spawn(args)
    return {"launched": True, "pid": pid, "exe": str(exe), "bridge_port": port if start_bridge else None}


def run_blender_headless(script: str, blend_file: str | None = None,
                         timeout: int = 600) -> dict:
    """Run a Python script in background Blender (no UI). Returns stdout/stderr."""
    exe = blender_exe()
    if not exe:
        return {"ok": False, "error": "Blender not found; set S4KIT_BLENDER_EXE"}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(script)
        script_path = f.name
    args = [str(exe), "-b"] + ([str(blend_file)] if blend_file else []) + \
           ["--factory-startup", "--python-exit-code", "1", "--python", script_path]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "returncode": p.returncode,
                "stdout": p.stdout[-20000:], "stderr": p.stderr[-8000:]}
    finally:
        os.unlink(script_path)


def environment_report() -> dict:
    return {
        "sims4": sims4_status(),
        "sims4_studio": {"exe": str(s4s_exe() or ""),
                         "running": is_running("S4Studio.exe")},
        "blender": {"exe": str(blender_exe() or ""),
                    "running": is_running("blender.exe", "Blender", "blender"),
                    "bridge_addon": str(ADDON_PATH)},
    }
