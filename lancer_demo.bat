@echo off
rem Mode demonstration : donnees FICTIVES, entree sans identifiant, uniquement sur cet ordinateur (port 8091).
rem Les vraies donnees (dossier donnees) ne sont jamais touchees. Pour repartir de zero : supprimer le dossier donnees_demo.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist ".venv_%USERNAME%\Scripts\python.exe" set "PY=.venv_%USERNAME%\Scripts\python.exe"
if not exist "%PY%" (
  echo Environnement absent : lancez d'abord installer.ps1
  pause & exit /b 1
)
start "" http://127.0.0.1:8091
"%PY%" -m gestion.main --demo --port 8091
pause
