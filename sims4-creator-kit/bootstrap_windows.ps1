# S4Kit one-step installer for Windows.
# Paste the whole thing into PowerShell (normal, not admin) or run:  powershell -ExecutionPolicy Bypass -File bootstrap_windows.ps1
# Safe to run again: it updates the code and re-registers everything.

& {
$ErrorActionPreference = "Continue"   # native tools write progress to stderr; failures are checked explicitly
$Root   = Join-Path $env:USERPROFILE "S4Kit"
$Repo   = "https://github.com/stilltazck-web/LibreTV.git"
$Branch = "claude/sims4-pc-connectors-tools-0tyxqf"
$Clone  = Join-Path $Root "LibreTV"
$Kit    = Join-Path $Clone "sims4-creator-kit"

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "    ! $m" -ForegroundColor Yellow }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget-Install($id) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget not available - install $id manually, then run this again."
  }
  winget install --id $id -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
}
function Find-Python {
  $probe = Join-Path $env:TEMP "s4kit_probe.py"
  "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" | Set-Content -Encoding ASCII $probe
  $candidates = @(
    @{ Exe = "py"; Args = @("-3", $probe) },
    @{ Exe = "python"; Args = @($probe) }
  )
  foreach ($c in $candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    $exe = $c.Exe; $cArgs = $c.Args
    $out = & $exe @cArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
      $path = ($out | Select-Object -Last 1).ToString().Trim()
      if ($path -and (Test-Path $path)) { return $path }
    }
  }
  return $null
}

# ---------------------------------------------------------------- 1. Git + Python
Say "Checking Git and Python"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Install "Git.Git" }
$Python = Find-Python
if (-not $Python) { Winget-Install "Python.Python.3.12"; $Python = Find-Python }
if (-not $Python) { throw "Python 3.10+ not found. Open a NEW PowerShell window and run this again." }
Write-Host "    Python: $Python"

# ---------------------------------------------------------------- 2. Code
Say "Downloading S4Kit to $Clone"
New-Item -ItemType Directory -Force $Root | Out-Null
if (Test-Path (Join-Path $Clone ".git")) {
  git -C $Clone fetch --depth 1 origin $Branch
  git -C $Clone checkout -B $Branch FETCH_HEAD
} else {
  git clone --depth 1 -b $Branch $Repo $Clone
}
if ($LASTEXITCODE -ne 0) { throw "git failed (if GitHub asked you to sign in, sign in and run again)." }

# ---------------------------------------------------------------- 3. Install + test
Say "Installing the s4kit connector"
& $Python -m venv (Join-Path $Kit ".venv")
if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
$VPy = Join-Path $Kit ".venv\Scripts\python.exe"
$Exe = Join-Path $Kit ".venv\Scripts\s4kit-mcp.exe"
& $VPy -m pip install --upgrade pip --quiet
& $VPy -m pip install --quiet -e ($Kit + "[test]")
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
Push-Location $Kit
& $VPy -m pytest -q tests
$TestsOk = ($LASTEXITCODE -eq 0)
Pop-Location
if (-not $TestsOk) { Warn "Some tests failed - send the output above to Claude." }

# ---------------------------------------------------------------- 4. Claude Desktop + Claude Code
Say "Connecting s4kit to Claude"
$MergePy = Join-Path $env:TEMP "s4kit_merge_config.py"
@'
import glob, json, os, shutil, sys, time
exe = sys.argv[1]
dirs = [os.path.join(os.environ["APPDATA"], "Claude")]
dirs += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "*Claude*",
                               "LocalCache", "Roaming", "Claude"))
targets = [d for d in dirs if os.path.isdir(d)] or dirs[:1]
for d in targets:
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "claude_desktop_config.json")
    cfg = {}
    if os.path.exists(path):
        shutil.copy2(path, path + time.strftime(".%Y%m%d-%H%M%S.bak"))
        with open(path, encoding="utf-8-sig") as f:
            text = f.read().strip()
        try:
            cfg = json.loads(text) if text else {}
        except json.JSONDecodeError as e:
            sys.exit(f"Not changing {path}: it isn't valid JSON ({e}). Fix it or add s4kit by hand.")
    servers = cfg.setdefault("mcpServers", {})
    entry = servers.get("s4kit", {})
    entry["command"] = exe
    servers["s4kit"] = entry  # keeps any "env" overrides you added before
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print("    Claude Desktop config:", path, "->", sorted(servers))
'@ | Set-Content -Encoding UTF8 $MergePy
& $VPy $MergePy $Exe
if ($LASTEXITCODE -ne 0) { Warn "Claude Desktop config was not changed (see message above)." }

if (Get-Command claude -ErrorAction SilentlyContinue) {
  try { claude mcp remove s4kit --scope user 2>$null | Out-Null } catch { }
  claude mcp add s4kit --scope user -- $Exe
  Write-Host "    Claude Code: registered (user scope)"
} else {
  Write-Host "    Claude Code CLI not found - skipped (Claude Desktop is set up)."
}

# ---------------------------------------------------------------- 5. Blender add-on
Say "Installing the Blender add-on"
$Blender = $env:S4KIT_BLENDER_EXE
if (-not $Blender) {
  $Blender = Get-ChildItem "C:\Program Files\Blender Foundation\Blender*\blender.exe",
                           "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe" -ErrorAction SilentlyContinue |
             Sort-Object { if ($_.Directory.Name -match '(\d+)\.(\d+)') { [int]$Matches[1] * 1000 + [int]$Matches[2] } else { 0 } } -Descending |
             Select-Object -First 1 -ExpandProperty FullName
}
if ($Blender) {
  $AddonPy = Join-Path $env:TEMP "s4kit_addon_install.py"
@'
import bpy, shutil, sys, os, addon_utils
src = sys.argv[sys.argv.index("--") + 1]
d = bpy.utils.user_resource('SCRIPTS', path="addons", create=True)
shutil.copy(src, d)
bpy.utils.refresh_script_paths()
addon_utils.modules(refresh=True)
addon_utils.enable("s4kit_bridge", default_set=True, persistent=True,
                   handle_error=lambda e: print("S4KIT_ADDON_ERROR", e))
if "s4kit_bridge" in bpy.context.preferences.addons:
    bpy.context.preferences.use_preferences_save = True
    bpy.ops.wm.save_userpref()
    print("S4KIT_ADDON_OK", os.path.join(d, "s4kit_bridge.py"))
else:
    print("S4KIT_ADDON_ERROR not enabled")
'@ | Set-Content -Encoding UTF8 $AddonPy
  $out = & $Blender -b --python $AddonPy -- (Join-Path $Kit "blender_addon\s4kit_bridge.py") 2>&1
  $ok = $out | Select-String "S4KIT_ADDON_OK"
  if ($ok) { Write-Host "    Blender: $Blender`n    Add-on enabled; the bridge starts automatically when Blender opens." }
  else { Warn "Add-on install failed. Install blender_addon\s4kit_bridge.py by hand (Preferences > Add-ons)."; $out | Select-String "S4KIT|Error" | Write-Host }
} else {
  Warn "Blender not found. Set S4KIT_BLENDER_EXE or install the add-on by hand."
}

# ---------------------------------------------------------------- 6. Detection report
Say "What s4kit found on this PC"
$ReportPy = Join-Path $env:TEMP "s4kit_report.py"
@'
import json, s4kit.apps as a
r = a.environment_report()
s = r["sims4"]
rows = [("Sims 4 game exe", s["exe"], "S4KIT_SIMS4_DIR"),
        ("Sims 4 Documents", s["docs_dir"] if s["docs_exists"] else "", "S4KIT_SIMS4_DOCS"),
        ("Sims 4 Studio", r["sims4_studio"]["exe"], "S4KIT_S4S_EXE"),
        ("Blender", r["blender"]["exe"], "S4KIT_BLENDER_EXE")]
for name, val, var in rows:
    print(f"    {'OK ' if val else 'MISSING'}  {name:18} {val or '(set ' + var + ')'}")
o = s["options"]
state = lambda v, on: "unknown (start the game once)" if v is None else ("on" if v == on else "OFF")
print("    Custom content:", state(o.get("modsdisabled"), "0"), "| Script mods:", state(o.get("scriptmodsenabled"), "1"))
'@ | Set-Content -Encoding UTF8 $ReportPy
& $VPy $ReportPy

Say "Done"
Write-Host @"
    1. Quit Claude completely (tray icon > Quit) and open it again.
    2. Open Blender - the S4Kit bridge starts by itself (sidebar N > S4Kit shows 'listening').
    3. In a new Claude chat, ask:  run environment_report
    If anything shows MISSING above, tell Claude the real path and it will add the override.
"@
}
