# Retire le démarrage automatique de Gestion libre (la tâche planifiée). Vos données ne sont JAMAIS touchées.
#   -NomTache     nom de la tâche (par défaut « GestionLibre-Serveur »)
#   -ArreterServeur   arrête aussi le serveur Gestion libre de CE dossier qui écoute sur -Port (8090 par défaut)
param(
    [string]$NomTache = "GestionLibre-Serveur",
    [int]$Port = 8090,
    [switch]$ArreterServeur
)
$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $MyInvocation.MyCommand.Path

$tache = Get-ScheduledTask -TaskName $NomTache -ErrorAction SilentlyContinue
if ($tache) {
    Stop-ScheduledTask -TaskName $NomTache -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $NomTache -Confirm:$false
    Write-Host "Tâche « $NomTache » supprimée." -ForegroundColor Green
} else {
    Write-Host "Aucune tâche « $NomTache » : rien à retirer."
}

if ($ArreterServeur) {
    $python = (Join-Path $racine ".venv").ToLower()      # .venv ou .venv_<compte>
    $ecoute = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ecoute) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($ecoute.OwningProcess)"
        # On n'arrête que le serveur Gestion libre de ce dossier, jamais un autre programme qui utiliserait le même port.
        if ($p -and $p.CommandLine -match "gestion\.main" -and (($p.ExecutablePath + "").ToLower().StartsWith($racine.ToLower()) -or $p.CommandLine.ToLower().Contains($python))) {
            Stop-Process -Id $p.ProcessId -Force
            Write-Host "Serveur arrêté (port $Port)." -ForegroundColor Green
        } else {
            Write-Host "Le port $Port est utilisé par un autre programme : rien n'a été arrêté." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Aucun serveur n'écoute sur le port $Port."
    }
}
