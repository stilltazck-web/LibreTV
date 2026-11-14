# S4Kit installer for Windows (run in PowerShell from this folder)
$ErrorActionPreference = "Stop"
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e .
$exe = (Resolve-Path .\.venv\Scripts\s4kit-mcp.exe).Path
Write-Host "`nInstalled. MCP command:" $exe
Write-Host "Claude Code:    claude mcp add s4kit -- `"$exe`""
Write-Host "Claude Desktop: add it to %APPDATA%\Claude\claude_desktop_config.json (see examples\)"
Write-Host "Blender add-on: Edit > Preferences > Add-ons > Install > blender_addon\s4kit_bridge.py"
.\.venv\Scripts\python -c "import json,s4kit.apps as a; print(json.dumps(a.environment_report(), indent=2))"
