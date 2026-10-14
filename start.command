#!/usr/bin/env bash
# Double-click this file on Mac (or run ./start.command on Linux) to start the Outreach CRM.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting things up. This takes a minute..."
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
( sleep 2; (command -v open >/dev/null && open http://127.0.0.1:5000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5000) ) >/dev/null 2>&1 &
python crm.py
