#!/bin/bash
# Starts the app after setup_mac.command has been run once: double-click this file.

cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "Setup hasn't been run yet: double-click setup_mac.command first."
  if [ -t 0 ]; then
    echo
    read -r -p "Press Return to close this window. "
  fi
  exit 1
fi

.venv/bin/python main.py "$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo "The app exited with an error (code $status)."
  if [ -t 0 ]; then
    echo
    read -r -p "Press Return to close this window. "
  fi
fi
exit "$status"
