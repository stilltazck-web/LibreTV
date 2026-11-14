# S4Kit one-shot setup for Windows.
# Run:  powershell -ExecutionPolicy Bypass -File setup_s4kit.ps1
# Asks before installing any software. Safe to re-run.
$ErrorActionPreference = "Stop"
$Branch  = "claude/sims4-pc-connectors-tools-0tyxqf"
$RepoUrl = "https://github.com/stilltazck-web/LibreTV.git"
$Root    = Join-Path $env:USERPROFILE "S4Kit"
$Summary = [ordered]@{}

function Ask($q) { (Read-Host "$q [y/N]") -match '^(y|yes)$' }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

# ---------- 1. Get the code ----------
Step "1. Get the code"
$Kit = $null
$top = $null
if (Have git) { try { $top = (git rev-parse --show-toplevel 2>$null) } catch { $top = $null } }
$origin = $null
if ($top) { try { $origin = (git -C $top remote get-url origin 2>$null) } catch {} }
if ($top -and $origin -match 'LibreTV') {
    Write-Host "Current folder is a LibreTV clone: $top"
    git -C $top fetch origin $Branch
    git -C $top checkout $Branch
    git -C $top pull --ff-only origin $Branch
    $Kit = Join-Path $top "sims4-creator-kit"
} else {
    if (-not (Have git)) {
        if (Ask "git is not installed. Install it with winget (Git.Git)?") {
            winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
            Refresh-Path
        }
    }
    New-Item -ItemType Directory -Force $Root | Out-Null
    $clone = Join-Path $Root "LibreTV"
    if (Have git) {
        if (Test-Path (Join-Path $clone ".git")) {
            git -C $clone fetch origin $Branch; git -C $clone checkout $Branch; git -C $clone pull --ff-only origin $Branch
        } else {
            git clone -b $Branch $RepoUrl $clone
        }
        $Kit = Join-Path $clone "sims4-creator-kit"
    } else {
        Write-Host "No git; downloading branch ZIP instead."
        $zip = Join-Path $Root "branch.zip"
        Invoke-WebRequest "https://github.com/stilltazck-web/LibreTV/archive/refs/heads/$Branch.zip" -OutFile $zip
        $dest = Join-Path $Root "zip"
        if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
        Expand-Archive $zip $dest
        $Kit = (Get-ChildItem $dest -Directory | Select-Object -First 1).FullName + "\sims4-creator-kit"
    }
}
if (-not (Test-Path "$Kit\install_windows.ps1")) { throw "sims4-creator-kit not found at $Kit" }
$Kit = (Resolve-Path $Kit).Path
$Summary["Kit folder"] = $Kit

# ---------- 2. Python ----------
Step "2. Python 3.10+"
function PyOk {
    try { $v = (& python --version 2>&1) -replace 'Python ','' } catch { return $false }
    if ($v -notmatch '^\d+\.\d+') { return $false }
    $p = $v.Split('.'); return ([int]$p[0] -gt 3 -or ([int]$p[0] -eq 3 -and [int]$p[1] -ge 10))
}
if (-not (PyOk)) {
    if (Ask "Python 3.10+ not found on PATH. Install Python 3.12 with winget?") {
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        Refresh-Path
    }
    if (-not (PyOk)) { throw "Python 3.10+ still not on PATH. Open a new PowerShell window and re-run this script." }
}
Write-Host (python --version)

# ---------- 3. Install + tests ----------
Step "3. Install s4kit and run tests"
Push-Location $Kit
try {
    powershell -ExecutionPolicy Bypass -File install_windows.ps1
    $Py  = Join-Path $Kit ".venv\Scripts\python.exe"
    $Exe = Join-Path $Kit ".venv\Scripts\s4kit-mcp.exe"
    & $Py -m pip install -q pytest
    & $Py -m pytest -q tests
    $Summary["Tests"] = if ($LASTEXITCODE -eq 0) { "PASSED" } else { "FAILED (exit $LASTEXITCODE)" }
} finally { Pop-Location }

# ---------- 5. Blender add-on (before detection so we know Blender) ----------
Step "5. Blender add-on"
function PyVal($expr) { (& $Py -c "import s4kit.apps as a; r=$expr; print(r if r else '')").Trim() }
$blender = PyVal "a.blender_exe()"
if (-not $blender) {
    $blender = Get-ChildItem "C:\Program Files\Blender Foundation\Blender *\blender.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if ($blender) {
    $ver = $null
    try {
        $ver = & $blender -b --factory-startup --python-expr "import bpy;print('S4VER',bpy.app.version[0],bpy.app.version[1])" 2>&1 |
            ForEach-Object { if ("$_" -match '^S4VER (\d+) (\d+)') { "$($Matches[1]).$($Matches[2])" } } |
            Select-Object -First 1
    } catch {}
    if (-not $ver) { $ver = (Split-Path (Split-Path $blender -Parent) -Leaf) -replace '^Blender\s*','' }
    $addons = Join-Path $env:APPDATA "Blender Foundation\Blender\$ver\scripts\addons"
    New-Item -ItemType Directory -Force $addons | Out-Null
    Copy-Item (Join-Path $Kit "blender_addon\s4kit_bridge.py") $addons -Force
    try { & $blender -b --python-expr "import bpy; bpy.ops.preferences.addon_enable(module='s4kit_bridge'); bpy.ops.wm.save_userpref()" 2>&1 | Out-Null } catch {}
    $Summary["Blender add-on"] = "installed to $addons and enabled (Blender $ver)"
} else {
    $Summary["Blender add-on"] = "SKIPPED - Blender not found"
}

# ---------- 6. Detect apps, search disk for anything missing ----------
Step "6. Detect The Sims 4, Sims 4 Studio, Blender"
$drives = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -ne $null } | ForEach-Object { $_.Root }
function Find-OnDisk($name) {
    Write-Host "  Searching all drives for $name (may take a few minutes)..."
    foreach ($d in $drives) {
        $hit = Get-ChildItem -Path $d -Filter $name -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}
$EnvVars = [ordered]@{}

$sims = PyVal "a.sims4_install_dir()"
if (-not $sims) {
    $e = Find-OnDisk "TS4_x64.exe"
    if ($e) { $sims = (Resolve-Path (Join-Path (Split-Path $e -Parent) "..\..")).Path; $EnvVars["S4KIT_SIMS4_DIR"] = $sims }
}
$docs = PyVal "(lambda p: p if p.exists() else None)(a.sims4_docs_dir())"
if (-not $docs) {
    foreach ($c in @("$env:OneDrive\Documents\Electronic Arts\The Sims 4", "$env:OneDrive\Dokumente\Electronic Arts\The Sims 4",
                     "$env:USERPROFILE\Documents\Electronic Arts\The Sims 4")) {
        if ($c -and (Test-Path $c)) { $docs = $c; break }
    }
    if (-not $docs) {
        $o = Get-ChildItem $env:USERPROFILE -Filter "Options.ini" -Recurse -File -ErrorAction SilentlyContinue |
             Where-Object { $_.DirectoryName -like "*The Sims 4" } | Select-Object -First 1
        if ($o) { $docs = $o.DirectoryName }
    }
    if ($docs) { $EnvVars["S4KIT_SIMS4_DOCS"] = $docs }
}
$s4s = PyVal "a.s4s_exe()"
if (-not $s4s) { $s4s = Find-OnDisk "S4Studio.exe"; if ($s4s) { $EnvVars["S4KIT_S4S_EXE"] = $s4s } }
if (-not (PyVal "a.blender_exe()")) {
    if (-not $blender) { $blender = Find-OnDisk "blender.exe" }
    if ($blender) { $EnvVars["S4KIT_BLENDER_EXE"] = $blender }
}
$Summary["The Sims 4 install"] = if ($sims)    { $sims }    else { "NOT FOUND" }
$Summary["Sims 4 Documents"]   = if ($docs)    { $docs }    else { "NOT FOUND" }
$Summary["Sims 4 Studio"]      = if ($s4s)     { $s4s }     else { "NOT FOUND" }
$Summary["Blender"]            = if ($blender) { $blender } else { "NOT FOUND" }
$Summary["Env overrides set"]  = if ($EnvVars.Count) { ($EnvVars.Keys -join ", ") } else { "none needed" }

# ---------- 4. Register with Claude (now that env overrides are known) ----------
Step "4. Register with Claude"
# Claude Desktop
$cfgDir = Join-Path $env:APPDATA "Claude"
$cfg    = Join-Path $cfgDir "claude_desktop_config.json"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
if (Test-Path $cfg) {
    $bak = "$cfg.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item $cfg $bak
    Write-Host "Backed up config to $bak"
    $raw = Get-Content $cfg -Raw
    $json = if ($raw.Trim()) { $raw | ConvertFrom-Json } else { [pscustomobject]@{} }
} else { $json = [pscustomobject]@{} }
if (-not $json.PSObject.Properties["mcpServers"]) { $json | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) }
$entry = [pscustomobject]@{ command = $Exe }
if ($EnvVars.Count) { $entry | Add-Member -NotePropertyName env -NotePropertyValue ([pscustomobject]$EnvVars) }
if ($json.mcpServers.PSObject.Properties["s4kit"]) { $json.mcpServers.PSObject.Properties.Remove("s4kit") }
$json.mcpServers | Add-Member -NotePropertyName s4kit -NotePropertyValue $entry
[IO.File]::WriteAllText($cfg, ($json | ConvertTo-Json -Depth 50), (New-Object Text.UTF8Encoding($false)))
$Summary["Claude Desktop"] = "registered in $cfg"

# Claude Code
if (Have claude) {
    try { claude mcp remove s4kit --scope user 2>&1 | Out-Null } catch {}
    $cargs = @("mcp","add","s4kit","--scope","user")
    foreach ($k in $EnvVars.Keys) { $cargs += @("-e", "$k=$($EnvVars[$k])") }
    $cargs += @("--", $Exe)
    & claude @cargs
    $Summary["Claude Code"] = "registered (user scope)"
} else { $Summary["Claude Code"] = "SKIPPED - 'claude' CLI not on PATH" }

# ---------- Final report ----------
Step "Environment report (with overrides applied)"
foreach ($k in $EnvVars.Keys) { Set-Item "env:$k" $EnvVars[$k] }
& $Py -c "import json,s4kit.apps as a; print(json.dumps(a.environment_report(), indent=2))"

Step "SUMMARY"
$Summary.GetEnumerator() | ForEach-Object { "{0,-20} {1}" -f $_.Key, $_.Value } | Write-Host
Write-Host @"

Next:
  1. Fully quit Claude desktop (tray icon > Quit) and reopen it.
  2. In Blender press N > S4Kit tab > Start bridge.
  3. In a new chat ask Claude to "run environment_report".
"@
