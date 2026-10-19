@echo off
REM Double-click this file on Windows to start the BSO.
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting things up. This takes a minute...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist .env copy .env.example .env >nul
start "" http://127.0.0.1:5000
python crm.py
pause
