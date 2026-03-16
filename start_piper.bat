@echo off
setlocal

title Piper Core
echo Starting Piper Core...
echo.
set "PIPER_LAUNCHER=batch"
set "RESTART_EXIT_CODE=85"

set "PYTHON_EXE="

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
)

:launch
if defined PYTHON_EXE (
    echo Using virtual environment: %PYTHON_EXE%
    "%PYTHON_EXE%" app.py
) else (
    echo WARNING: Virtual environment not found. Using global Python.
    python app.py
)

set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="%RESTART_EXIT_CODE%" (
    echo Restarting Piper...
    echo.
    goto launch
)

pause
exit /b %EXIT_CODE%
