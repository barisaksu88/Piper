@echo off
setlocal
set ROOT=C:\Projects\Piper
"%ROOT%\.venv\Scripts\python.exe" -u "%ROOT%\scripts\tts_windows_probe.py" %*
