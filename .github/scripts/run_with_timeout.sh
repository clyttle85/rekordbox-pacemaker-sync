#!/bin/bash
# Usage: run_with_timeout.sh SECONDS COMMAND [ARGS...]
# macOS has no `timeout` command. Fails if COMMAND fails or runs past the limit,
# which is what a modal dialog blocking a headless app looks like.
set -u

limit="$1"
shift

"$@" &
pid=$!
elapsed=0
while kill -0 "$pid" 2>/dev/null; do
  if [ "$elapsed" -ge "$limit" ]; then
    echo "::error::Timed out after ${limit}s: $*"
    kill -9 "$pid" 2>/dev/null
    exit 124
  fi
  sleep 1
  elapsed=$((elapsed + 1))
done
wait "$pid"
