#!/bin/bash
# Double-click to start Bank Nifty Radar. Close this window to stop it.
# Each start: pulls the latest version from GitHub, installs any new packages, opens the app.
cd "$(dirname "$0")" || exit 1

echo "Bank Nifty Radar"
echo "----------------"

PIP_OPTS="--default-timeout=120 --retries 10"

# 1. Update the code (skipped quietly if offline or not a git copy)
REQ_BEFORE=""
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  REQ_BEFORE=$(git rev-parse HEAD:requirements.txt 2>/dev/null)
  echo "Checking for updates..."
  if GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet 2>/tmp/radar_git_err.txt; then
    echo "Up to date ($(git log -1 --format='%h, %cd' --date=format:'%d %b %Y'))."
  else
    echo "Couldn't update right now (offline or GitHub login needed). Starting the current version."
    head -2 /tmp/radar_git_err.txt
  fi
fi

# 2. First-time setup
if [ ! -x ".venv/bin/python" ]; then
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PY="$c"; break
      fi
    fi
  done
  if [ -z "$PY" ]; then
    echo ""
    echo "Python 3.10 or newer is needed."
    echo "Install it from https://www.python.org/downloads/macos/ , then double-click this file again."
    open "https://www.python.org/downloads/macos/"
    read -n 1 -s -r -p "Press any key to close."
    exit 1
  fi
  echo "First start: setting up (a few minutes, needs internet)..."
  "$PY" -m venv .venv || { echo "Could not create the app environment."; read -n 1 -s -r; exit 1; }
  .venv/bin/python -m pip install --upgrade pip --quiet $PIP_OPTS
  if ! .venv/bin/python -m pip install $PIP_OPTS -r requirements.txt; then
    echo ""
    echo "Setup failed. Check your internet connection and double-click this file again."
    rm -rf .venv
    read -n 1 -s -r -p "Press any key to close."
    exit 1
  fi
  echo "Setup complete."
# 3. An update added packages: install just those
elif [ -n "$REQ_BEFORE" ] && [ "$REQ_BEFORE" != "$(git rev-parse HEAD:requirements.txt 2>/dev/null)" ]; then
  echo "Installing new packages from the update..."
  .venv/bin/python -m pip install $PIP_OPTS -r requirements.txt --quiet || echo "Package install failed; the app may still work."
fi

.venv/bin/python app.py
