@echo off
setlocal

REM Activate your venv (so 'requests' is available)
call C:\Piper\venv\Scripts\activate.bat

REM Choose model for this run (already pulled)
set SIDECAR_MODEL=deepseek-coder:6.7b

REM Run the sidekick
python C:\Piper\sidekick\sidekick.py

REM Show result
notepad C:\Piper\sidekick\result.md
