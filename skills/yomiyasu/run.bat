@echo off
rem Run a vendored yomiyasu script: run.bat lint|check|diff [args]. See VENDOR.md.
rem It needs only the standard library, so it works without the venv too.
rem This file must stay ASCII only (cmd.exe on Japanese Windows may misread other characters).
setlocal
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -B "%~dp0scripts\run_yomiyasu.py" %*
exit /b %ERRORLEVEL%
