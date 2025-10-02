# Attach Mode Smoke (Manual)

Goal: prove GUI↔CLI attach works without code changes.

## Setup
- Two PowerShell windows (A = CLI, B = GUI)
- Encoding: UTF-8
- Log path: `C:\Piper\run\core.log`

### Window A — CLI (interactive; tees to log)
```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($true)
chcp 65001 > $null
New-Item -ItemType Directory -Force C:\Piper\run | Out-Null
Remove-Item C:\Piper\run\core.log -ErrorAction SilentlyContinue
python -X utf8 -u -m scripts.entries.app_cli_entry 2>&1 | ForEach-Object {
  $_
  $_ | Out-File -FilePath C:\Piper\run\core.log -Append -Encoding utf8
}