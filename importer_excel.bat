@echo off
rem Import du classeur Excel de gestion.
rem   importer_excel.bat "C:\chemin\Classeur.xlsx" identifiant_proprietaire            (essai a blanc : rien n'est ecrit)
rem   importer_excel.bat "C:\chemin\Classeur.xlsx" identifiant_proprietaire --apply    (import reel)
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist ".venv_%USERNAME%\Scripts\python.exe" set "PY=.venv_%USERNAME%\Scripts\python.exe"
if "%~2"=="" (
  echo Usage : importer_excel.bat "fichier.xlsx" identifiant_proprietaire [--apply]
  exit /b 2
)
"%PY%" -m gestion.importer %1 --user %2 %3 %4
pause
