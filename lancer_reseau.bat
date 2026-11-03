@echo off
rem Lance Gestion libre pour les telephones du meme reseau (HTTP non chiffre : reseau de confiance uniquement).
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist ".venv_%USERNAME%\Scripts\python.exe" set "PY=.venv_%USERNAME%\Scripts\python.exe"
if not exist "%PY%" (
  echo Environnement absent : lancez d'abord installer.ps1
  pause & exit /b 1
)
"%PY%" -m gestion.main --lan
pause
