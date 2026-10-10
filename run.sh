#!/usr/bin/env bash
# Starts the workshop app using the environment created by setup.sh.
# Any arguments are passed through, e.g.:  ./run.sh --keyboard   ./run.sh --channels 1
cd "$(dirname "$0")"
if [ ! -f .venv/bin/activate ]; then
  echo "No .venv found — run ./setup.sh first."
  exit 1
fi
# shellcheck disable=SC1091
. .venv/bin/activate
exec python main.py "$@"
