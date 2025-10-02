@echo off
setlocal
set PYTHONIOENCODING=utf-8

call C:\Piper\venv\Scripts\activate.bat

REM Build import edges without Graphviz
python C:\Piper\sidekick\imports_map_generic.py C:\Piper\scripts > C:\Piper\sidekick\deps.txt

REM Unused/dead code (zero-filter as requested)
vulture C:\Piper\scripts > C:\Piper\sidekick\vulture.txt

REM Security scan
bandit -r C:\Piper\scripts > C:\Piper\sidekick\bandit.txt

REM Concatenate raw outputs into one Markdown
cd /d C:\Piper\sidekick
python collate_merge_all.py

notepad analyzer_full.md
