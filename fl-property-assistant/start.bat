@echo off
REM One-click start (Windows): sets up on first run, then opens the app in your browser.
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up, this takes a minute...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
start "" http://127.0.0.1:8765
echo FL Property Records running at http://127.0.0.1:8765  (close this window to stop)
.venv\Scripts\python -m flprops web --port 8765
