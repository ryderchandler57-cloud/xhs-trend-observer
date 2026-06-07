@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_keyword_batch.ps1" %*
exit /b %ERRORLEVEL%
