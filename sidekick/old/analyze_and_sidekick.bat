@echo off
setlocal
REM Ensure UTF-8 for Bandit on Windows
set PYTHONIOENCODING=utf-8

REM Activate venv (adjust if your venv path differs)
call C:\Piper\venv\Scripts\activate.bat

REM Generate analyzer outputs
pushd C:\Piper
pyan3 scripts/**/*.py --uses --no-defines --dot > sidekick\callgraph.dot
vulture scripts > sidekick\vulture.txt
bandit -r scripts > sidekick\bandit.txt
popd

REM Run sidekick
cd /d C:\Piper\sidekick
py sidekick.py
notepad result.md
