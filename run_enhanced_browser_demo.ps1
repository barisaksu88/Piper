param(
    [string]$MainRepoPath = "",
    [string]$OutputDir = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $MainRepoPath) {
    $defaultMain = Join-Path (Split-Path $repoRoot -Parent) "Piper"
    if (Test-Path $defaultMain) {
        $MainRepoPath = $defaultMain
    } else {
        $MainRepoPath = $repoRoot
    }
}

$pythonExe = Join-Path $MainRepoPath ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Could not find the shared Python environment at $pythonExe"
}

$modelPath = Join-Path $MainRepoPath "models\llama\Qwen_Qwen3.5-9B-Q6_K.gguf"
$mmprojPath = Join-Path $MainRepoPath "models\llama\Qwen3.5-9B.mmproj-F16.gguf"
$llamaExe = Join-Path $MainRepoPath "runtime\llama.cpp\llama-server.exe"

if (Test-Path $modelPath) {
    $env:PIPER_MODEL_PATH = $modelPath
}
if (Test-Path $mmprojPath) {
    $env:PIPER_MMPROJ_PATH = $mmprojPath
}
if (Test-Path $llamaExe) {
    $env:PIPER_LLAMA_SERVER_EXE = $llamaExe
}

if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot "data\harness\results\enhanced_browser_demo"
}

$args = @(".\scripts\computer_use_enhanced_actions_demo.py", "--output-dir", $OutputDir)
if ($Json) {
    $args += "--json"
}

Write-Host "Using Python:" $pythonExe
Write-Host "Using shared repo:" $MainRepoPath
Write-Host "Writing artifacts to:" $OutputDir

Push-Location $repoRoot
try {
    & $pythonExe @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
