#!/bin/sh
# Run finder_cli.py (Mac / Linux). The Windows version is run.bat. Same arguments.
# Standard library only. On Mac, only content search works (Everything is Windows only).
# This file must stay ASCII only, with LF line endings.
here=$(cd "$(dirname "$0")" && pwd -P)  # resolve links: the .venv is next to the real skills folder
PY="$here/../../.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
  command -v "$PY" >/dev/null 2>&1 || PY=python
fi
exec "$PY" "$here/scripts/finder_cli.py" "$@"
