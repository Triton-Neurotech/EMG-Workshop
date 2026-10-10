#!/usr/bin/env bash
# One-time setup (macOS / Linux): creates a private Python environment in .venv
# and installs everything the workshop needs. Run it from the project folder:
#     ./setup.sh
set -e
cd "$(dirname "$0")"

# Pick the first Python >= 3.10 we can find (Macs often have an old 3.9 as plain python3).
PY=""
for cand in python3.12 python3.13 python3.11 python3.14 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && \
     "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and re-run."
  exit 1
fi

echo "Creating .venv with $("$PY" --version) ..."
"$PY" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
echo "Installing packages (this takes a minute or two the first time) ..."
python -m pip install -r requirements.txt
echo
python check_setup.py
echo
echo "Setup complete. Start the workshop with:"
echo "    ./run.sh --keyboard     # no hardware: hold Space to flex"
echo "    ./run.sh                # with a MindRove or Ganglion streaming"
