#!/bin/sh
# Run finder_cli.py (Mac / Linux). The Windows version is run.bat. Same arguments.
# Standard library only. On Mac, only content search works (Everything is Windows only).
# This file must stay ASCII only, with LF line endings.
here=$(cd "$(dirname "$0")" && pwd -P)  # resolve links: the .venv is next to the real skills folder
PY="$here/../../.venv/bin/python"
if [ ! -x "$PY" ]; then
  # No venv: pick a Python 3.10 or later (macOS /usr/bin/python3 is often 3.9, which the scripts do not support)
  PY=
  for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 "$HOME/.local/bin/python3"; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY=$c; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Error: Python 3.10 or later was not found. Stop and report this to the user (do not search for Python yourself)." >&2
    exit 1
  fi
fi
exec "$PY" "$here/scripts/finder_cli.py" "$@"
