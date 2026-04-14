param(
    [string]$MainRepoPath = ""
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
if (-not $env:PIPER_LLAMA_SERVER_URL) {
    $env:PIPER_LLAMA_SERVER_URL = "http://127.0.0.1:8080"
}

Write-Host "Using Python:" $pythonExe
Write-Host "Using shared repo:" $MainRepoPath
Write-Host "Using llama server:" $env:PIPER_LLAMA_SERVER_EXE
Write-Host "Using model:" $env:PIPER_MODEL_PATH

Push-Location $repoRoot
try {
    & $pythonExe .\app.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
