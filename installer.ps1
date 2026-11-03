# Installe l'environnement Python du projet (à lancer une fois).
# Un .venv créé sous un autre compte Windows ne fonctionne pas ici : on crée alors .venv_<compte> sans toucher au premier ; les lanceurs le choisissent d'eux-mêmes.
Set-Location $PSScriptRoot
$venv = ".venv"
if (Test-Path .venv) {
    & .\.venv\Scripts\python.exe -c "pass" 2>$null
    if ($LASTEXITCODE -ne 0) { $venv = ".venv_$env:USERNAME" }
}
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
