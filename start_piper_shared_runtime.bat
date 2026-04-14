@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0start_piper_shared_runtime.ps1" %*
exit /b %ERRORLEVEL%
