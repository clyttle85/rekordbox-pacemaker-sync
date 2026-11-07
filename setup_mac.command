#!/bin/bash
# First-time setup for running from source on a Mac: double-click this file.
# Creates .venv, installs the requirements, then starts the app.
# Use run_mac.command to start it after that.

cd "$(dirname "$0")" || exit 1

# Keep the Terminal window open so any message can be read (only when double-clicked)
pause_if_interactive() {
  if [ -t 0 ]; then
    echo
    read -r -p "Press Return to close this window. "
  fi
}

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  cat <<'MESSAGE'

Python 3.9 or newer is needed, and wasn't found.

If a window just appeared asking to install the "Command Line Developer Tools",
click Install, wait for it to finish, then double-click this file again.

Otherwise, install Python from https://www.python.org/downloads/macos/
and double-click this file again.
MESSAGE
  pause_if_interactive
  exit 1
fi

echo "Using $(python3 --version)"

if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv"
  if ! python3 -m venv .venv; then
    echo "Could not create the virtual environment."
    pause_if_interactive
    exit 1
  fi
fi

echo "Installing requirements (this can take a few minutes the first time)"
if ! .venv/bin/python -m pip install --upgrade pip \
  || ! .venv/bin/python -m pip install -r requirements.txt; then
  echo "Installing the requirements failed. See the messages above."
  pause_if_interactive
  exit 1
fi

echo "Starting the app"
.venv/bin/python main.py "$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo "The app exited with an error (code $status)."
  pause_if_interactive
fi
exit "$status"
