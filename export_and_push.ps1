Param(
  [string]$Source  = "C:\Piper",
  [string]$Dest    = "C:\Piper_repo",
  [string]$Message = "$(Get-Date -Format 'yyyy-MM-dd HH:mm') export-and-push",
  [string]$RepoUrl = ""  # e.g. "https://github.com/USER/REPO.git"
)

# ---- Sanity checks ----
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Error "Git is not installed or not in PATH."
  exit 1
}
if (-not (Test-Path $Source)) {
  Write-Error "Source path not found: $Source"
  exit 1
}

# ---- Create destination if missing ----
if (-not (Test-Path $Dest)) {
  New-Item -ItemType Directory -Path $Dest | Out-Null
}

# ---- Helper: mirror a directory with robocopy (/MIR) ----
function Sync-Dir {
  param(
    [Parameter(Mandatory=$true)][string]$From,
    [Parameter(Mandatory=$true)][string]$To
  )
  if (-not (Test-Path $From)) { return }
  if (-not (Test-Path $To))   { New-Item -ItemType Directory -Path $To | Out-Null }
  # /MIR mirrors: adds/updates/deletes. Suppress noise for readability.
  robocopy $From $To /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
}

# ---- Mirror code dirs ----
Sync-Dir (Join-Path $Source "scripts") (Join-Path $Dest "scripts")
# Optional: include small, text-only configs (no secrets/large files)
Sync-Dir (Join-Path $Source "config")  (Join-Path $Dest "config")

# ---- Copy small root files if they exist ----
$rootFiles = @("requirements.txt", "README.md", "LICENSE", ".editorconfig")
foreach ($f in $rootFiles) {
  $src = Join-Path $Source $f
  if (Test-Path $src) {
    Copy-Item $src (Join-Path $Dest $f) -Force
  }
}

# ---- Ensure a tight .gitignore at DEST (create if missing) ----
$giPath = Join-Path $Dest ".gitignore"
if (-not (Test-Path $giPath)) {
@"
# Python
__pycache__/
*.py[cod]
*.pyo
*.pyd*
*.egg-info/
.venv/
venv/

# Logs & caches
logs/
*.log
*.tmp
*.cache/
.cache/
*.jsonl

# OS / editor
.DS_Store
Thumbs.db
desktop.ini
.vscode/
.idea/

# Run/artifacts/big stuff (KEEP OUT)
run/
runtime/
tmp/
captures/
renders/
models/
weights/
checkpoints/
assets/
llama.cpp/
snapshots/
"@ | Set-Content -Encoding UTF8 $giPath
}

# ---- Git operations ----
Push-Location $Dest

if (-not (Test-Path ".git")) {
  git init | Out-Null
}

# Ensure we are on main
git branch -M main | Out-Null

# Ensure origin exists
$hasOrigin = (git remote 2>$null) -match "^origin$"
if (-not $hasOrigin) {
  if ([string]::IsNullOrWhiteSpace($RepoUrl)) {
    Pop-Location
    Write-Error "No 'origin' remote set and no -RepoUrl provided. Provide -RepoUrl on first run."
    exit 1
  }
  git remote add origin $RepoUrl | Out-Null
}

# Stage and detect changes
git add -A
$pending = git status --porcelain

if (-not [string]::IsNullOrWhiteSpace($pending)) {
  git commit -m $Message | Out-Null

  # Try a normal push
  $pushOutput = git push -u origin main 2>&1
  if ($LASTEXITCODE -ne 0 -and ($pushOutput -match "non-fast-forward|fetch first|rejected")) {
    Write-Host "Remote moved; attempting rebase, then push..."
    git pull --rebase origin main
    git push -u origin main
  }

  Write-Host "Pushed: $Message"
} else {
  Write-Host "No changes to commit."
}

Pop-Location
