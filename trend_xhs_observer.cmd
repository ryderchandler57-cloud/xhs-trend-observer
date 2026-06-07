@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0trend_xhs_observer.ps1" %*
exit /b %ERRORLEVEL%
