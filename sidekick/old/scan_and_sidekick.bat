@echo off
setlocal
REM Ensure UTF-8 for Bandit
set PYTHONIOENCODING=utf-8

REM Activate venv (adjust if your venv path differs)
call C:\Piper\venv\Scripts\activate.bat

REM Build import deps (no Graphviz needed)
py C:\Piper\sidekick\imports_map_generic.py C:\Piper\scripts > C:\Piper\sidekick\deps.txt

REM Unused code (trim noise from tests & old tools)
vulture C:\Piper\scripts --exclude "C:\Piper\scripts\tests\*,C:\Piper\scripts\tools\old\*" > C:\Piper\sidekick\vulture.txt

REM Security scan
bandit -r C:\Piper\scripts > C:\Piper\sidekick\bandit.txt

REM Run Sidekick (model must be pulled; Ollama must be serving)
cd /d C:\Piper\sidekick
py sidekick.py
notepad result.md
