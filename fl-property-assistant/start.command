#!/usr/bin/env bash
# One-click start (Mac / Linux): sets up on first run, then opens the app in your browser.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up (takes a minute)..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi
PORT="${PORT:-8765}"
( sleep 2; (command -v open >/dev/null && open "http://127.0.0.1:$PORT") || \
  (command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:$PORT") || true ) &
echo "FL Property Records running at http://127.0.0.1:$PORT  (Ctrl-C to stop)"
exec .venv/bin/python -m flprops web --port "$PORT"
