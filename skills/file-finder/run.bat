@echo off
rem Run finder_cli.py with the venv of the opencode config folder.
rem This file must stay ASCII only (cmd.exe on Japanese Windows may misread other characters).
setlocal
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: venv not found: %PY% 1>&2
  echo In the opencode config folder, run: python -m venv .venv 1>&2
  exit /b 3
)
"%PY%" "%~dp0scripts\finder_cli.py" %*
exit /b %ERRORLEVEL%
