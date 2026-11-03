# Lance le serveur Gestion libre EN ARRIÈRE-PLAN (sans fenêtre), pour cet ordinateur uniquement (127.0.0.1).
# Utilisé par la tâche planifiée créée par installer_demarrage_auto.ps1 ; vous pouvez aussi le lancer à la main.
#   -Port             port d'écoute (8090 par défaut)
#   -DossierDonnees   dossier des données (par défaut : « donnees » à côté de ce script). Réservé aux essais.
# Sécurité : jamais --lan (pas d'accès réseau), jamais --demo (le mode démonstration ne se lance pas sur les vraies données).
param(
    [int]$Port = 8090,
    [string]$DossierDonnees = ""
)
$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $racine
$python = Join-Path $racine ".venv_$env:USERNAME\Scripts\python.exe"   # environnement propre à ce compte Windows, sinon .venv
if (-not (Test-Path $python)) { $python = Join-Path $racine ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { Write-Error "Environnement absent : lancez d'abord installer.ps1"; exit 1 }

$donnees = if ($DossierDonnees) { $DossierDonnees } else { Join-Path $racine "donnees" }
New-Item -ItemType Directory -Force -Path $donnees | Out-Null
$journal = Join-Path $donnees "journal_serveur.log"
if ((Test-Path $journal) -and ((Get-Item $journal).Length -gt 5MB)) { Move-Item -Force $journal ($journal + ".1") }       # un seul ancien journal gardé

function Ecrire([string]$texte) {
    # Si le serveur en marche tient le journal ouvert, l'écriture peut échouer : ce n'est pas grave, on ne s'arrête pas pour cela.
    try { Add-Content -Path $journal -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $texte) -Encoding UTF8 -ErrorAction Stop } catch { }
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Ecrire "Le port $Port est déjà utilisé : le serveur tourne sans doute déjà, rien à faire."
    exit 0
}

# Jamais de mode démonstration sur ces données ; dossier de données choisi seulement si demandé.
Remove-Item Env:GESTION_DEMO -ErrorAction SilentlyContinue
# Ce serveur est aussi publié en HTTPS privé via Tailscale Serve : protège le cookie de session.
$env:GESTION_SECURE_COOKIES = "1"
if ($DossierDonnees) { $env:GESTION_DATA_DIR = $DossierDonnees }

Ecrire "Démarrage du serveur sur http://127.0.0.1:$Port (données : $donnees)"
$commande = '"' + $python + '" -m gestion.main --port ' + $Port + ' >> "' + $journal + '" 2>&1'
Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", ('"' + $commande + '"')) -WorkingDirectory $racine -WindowStyle Hidden
