#!/usr/bin/env bash
# Cerebro launcher for macOS and Linux.
#   ./cerebro.sh            interactive menu
#   ./cerebro.sh start      run a command directly
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.9+ is required but was not found on your PATH."
  echo "Install it from https://python.org/downloads and run this again."
  exit 1
fi

if [ $# -gt 0 ]; then
  exec "$PYTHON" cerebro.py "$@"
fi

# No arguments: show a menu rather than a usage error.
cat <<'MENU'

  Cerebro

   1) Start Cerebro           (API + dashboard)
   2) Launch desktop widget
   3) Run setup / install
   4) Check status
   5) Diagnose problems
   6) Quit

MENU
read -rp "  Choose [1-6]: " choice
case "$choice" in
  1) exec "$PYTHON" cerebro.py start ;;
  2) exec "$PYTHON" cerebro.py widget ;;
  3) exec "$PYTHON" cerebro.py setup ;;
  4) exec "$PYTHON" cerebro.py status ;;
  5) exec "$PYTHON" cerebro.py doctor ;;
  *) exit 0 ;;
esac
