# make_kgb_snapshot.ps1
# Creates a KGB snapshot using the clipboard as the name

# Get the snapshot name from clipboard
$Name = Get-Clipboard
if (-not $Name) {
    Write-Host "Clipboard is empty. Copy a snapshot name first (e.g., KGB-2025-09-06_xxx)."
    exit 1
}

$root = 'C:\Piper'
$out  = Join-Path $root 'snapshots'
$t    = Get-Date

# Run the Python snapshot maker
python "$root\scripts\make_snapshot.py"

# Ensure snapshots folder exists
New-Item -ItemType Directory -Path $out -Force | Out-Null

# Find the latest zip created since script start
$zip = Get-ChildItem -Path $root -Recurse -Filter *.zip -File | Where-Object { $_.LastWriteTime -ge $t } | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($zip) {
    $dest = Join-Path $out "$Name.zip"
    Move-Item $zip.FullName $dest -Force
    Write-Host "Snapshot saved as $dest"
} else {
    Write-Warning "No zip produced by make_snapshot.py"
}
