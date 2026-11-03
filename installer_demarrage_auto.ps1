# Démarrage automatique de Gestion libre à l'ouverture de votre session Windows (sans fenêtre, sans droit d'administrateur, sans mot de passe enregistré).
#   clic droit > « Exécuter avec PowerShell »    ou    powershell -ExecutionPolicy Bypass -File installer_demarrage_auto.ps1
# Options :
#   -Demarrer          lance aussi le serveur tout de suite
#   -NomTache          nom de la tâche (par défaut « GestionLibre-Serveur »)
#   -Port              port d'écoute (8090 par défaut)
#   -DossierDonnees    dossier des données (essais seulement ; par défaut « donnees »)
# Le serveur n'écoute que sur cet ordinateur (127.0.0.1), jamais sur le réseau, et jamais en mode démonstration.
# Retirer : desinstaller_demarrage_auto.ps1
param(
    [string]$NomTache = "GestionLibre-Serveur",
    [int]$Port = 8090,
    [string]$DossierDonnees = "",
    [switch]$Demarrer
)
$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $MyInvocation.MyCommand.Path
$lanceur = Join-Path $racine "demarrer_serveur.ps1"
if (-not ((Test-Path (Join-Path $racine ".venv_$env:USERNAME\Scripts\python.exe")) -or (Test-Path (Join-Path $racine ".venv\Scripts\python.exe")))) { Write-Host "Environnement absent : lancez d'abord installer.ps1" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $lanceur)) { Write-Host "demarrer_serveur.ps1 est introuvable à côté de ce script." -ForegroundColor Red; exit 1 }

$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $lanceur + '" -Port ' + $Port
if ($DossierDonnees) { $arguments += ' -DossierDonnees "' + $DossierDonnees + '"' }
$utilisateur = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $racine
$declencheur = New-ScheduledTaskTrigger -AtLogOn -User $utilisateur
$principal = New-ScheduledTaskPrincipal -UserId $utilisateur -LogonType Interactive -RunLevel Limited         # votre compte, sans élévation, sans mot de passe stocké
$reglages = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

$existe = Get-ScheduledTask -TaskName $NomTache -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $NomTache -Action $action -Trigger $declencheur -Principal $principal -Settings $reglages `
    -Description "Lance Gestion libre en arrière-plan à l'ouverture de session (127.0.0.1:$Port, jamais en réseau)." -Force | Out-Null
Write-Host ($(if ($existe) { "Tâche « $NomTache » mise à jour." } else { "Tâche « $NomTache » créée." })) -ForegroundColor Green
Write-Host "Le serveur se lancera à chaque ouverture de session, en arrière-plan : http://127.0.0.1:$Port"
Write-Host "Journal : $(if ($DossierDonnees) { $DossierDonnees } else { Join-Path $racine 'donnees' })\journal_serveur.log"

if ($Demarrer) {
    Start-ScheduledTask -TaskName $NomTache
    Write-Host "Démarrage demandé : patientez quelques secondes puis ouvrez http://127.0.0.1:$Port" -ForegroundColor Green
}
