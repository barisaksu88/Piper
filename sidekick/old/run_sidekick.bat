@echo off
setlocal

REM Candidate locations
set CAND1=%ProgramFiles%\Ollama\ollama.exe
set CAND2=%LOCALAPPDATA%\Programs\Ollama\ollama.exe
set CAND3=%ProgramData%\Ollama\ollama.exe

set OLLAMA_EXE=
if exist "%CAND1%" set OLLAMA_EXE=%CAND1%
if exist "%CAND2%" set OLLAMA_EXE=%CAND2%
if exist "%CAND3%" set OLLAMA_EXE=%CAND3%

if "%OLLAMA_EXE%"=="" (
  echo Could not find ollama.exe in common locations.
  echo Please run this once and paste the path back here:
  echo   Get-ChildItem -Recurse -Filter ollama.exe ^| Select FullName
  pause
  exit /b 1
)

REM Start server if not already listening
REM quick ping: try a tiny call; if fails, start 'serve'
powershell -NoProfile -Command ^
  "try{Invoke-WebRequest -UseBasicParsing -Method POST -Uri http://localhost:11434/api/tags -Body '{}' | Out-Null; exit 0}catch{exit 1}"
if %ERRORLEVEL% NEQ 0 (
  start "Ollama Serve" "%OLLAMA_EXE%" serve
  timeout /t 2 >nul
)

REM Model for this run
set SIDECAR_MODEL=deepseek-coder:6.7b

cd /d C:\Piper\sidekick
py sidekick.py
notepad result.md
