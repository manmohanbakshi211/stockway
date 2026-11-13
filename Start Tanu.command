#!/bin/bash
# Double-click to start Tanu. Close this window to stop it.
# Each start: pulls the latest version from GitHub, sets up Tanu's own Python environment (.venv-tanu, separate from
# the old app's .venv), then runs Tanu. Before the switch-over Tanu only shows its app (http://127.0.0.1:8790);
# after it, Tanu runs the market day (http://127.0.0.1:8765). Tanu never places an order.
cd "$(dirname "$0")" || exit 1

echo "Tanu"
echo "----"
PIP_OPTS="--default-timeout=120 --retries 10"

# 1. Update the code (skipped quietly if offline or not a git copy)
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  echo "Checking for updates..."
  if GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet 2>/tmp/tanu_git_err.txt; then
    echo "Up to date: Tanu $(cat tanu/VERSION 2>/dev/null) ($(git log -1 --format='%h, %cd' --date=format:'%d %b %Y'))."
  else
    echo "Couldn't update right now (offline or GitHub login needed). Starting the current version."
    head -2 /tmp/tanu_git_err.txt
  fi
fi

# 2. Tanu's own environment (first time: a few minutes)
REQ_HASH=$(shasum tanu/requirements.txt 2>/dev/null | cut -c1-12)
if [ ! -x ".venv-tanu/bin/python" ]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/macos/ , then double-click this file again."
    read -n 1 -s -r; exit 1
  fi
  echo "Setting up Tanu's environment (first time only)..."
  "$PY" -m venv .venv-tanu || { echo "Could not create Tanu's environment."; read -n 1 -s -r; exit 1; }
  .venv-tanu/bin/python -m pip install --upgrade pip --quiet $PIP_OPTS
fi
if [ "$(cat .venv-tanu/.req 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "Installing Tanu's packages..."
  if .venv-tanu/bin/python -m pip install $PIP_OPTS -r tanu/requirements.txt --quiet; then
    echo "$REQ_HASH" > .venv-tanu/.req
  else
    echo "Package install failed; Tanu may still work."
  fi
fi

# 3. Check the machine, then run
.venv-tanu/bin/python -m tanu doctor
PORT=$(.venv-tanu/bin/python -c 'from tanu.core import config; print(config.load().web_port)' 2>/dev/null || echo 8790)
( sleep 3; open "http://127.0.0.1:$PORT" ) >/dev/null 2>&1 &
.venv-tanu/bin/python -m tanu run
echo "Tanu stopped. You can close this window."
read -n 1 -s -r
