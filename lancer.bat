@echo off
rem Lance Gestion libre sur cet ordinateur uniquement : http://127.0.0.1:8090
rem Si le serveur tourne deja (demarrage automatique installe), ouvre seulement la page.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist ".venv_%USERNAME%\Scripts\python.exe" set "PY=.venv_%USERNAME%\Scripts\python.exe"
if not exist "%PY%" (
  echo Environnement absent : lancez d'abord installer.ps1
  pause & exit /b 1
)
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  echo Gestion libre est deja lance. Ouverture de la page...
  start "" http://127.0.0.1:8090
  exit /b 0
)
start "" http://127.0.0.1:8090
"%PY%" -m gestion.main
pause
