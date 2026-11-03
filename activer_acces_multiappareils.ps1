# Publie l'installation Bhantal locale sur le tailnet Tailscale en HTTPS.
# Le serveur métier continue d'écouter uniquement sur 127.0.0.1:8090.
$ErrorActionPreference = 'Stop'
$port = 8090
$tailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'

if (-not (Test-Path -LiteralPath $tailscale)) {
    throw 'Tailscale doit être installé sur cet ordinateur.'
}

try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/state" -UseBasicParsing -TimeoutSec 5
} catch {
    throw "Le serveur Bhantal ne répond pas sur 127.0.0.1:$port. Démarrez d'abord Gestion libre."
}

$statusText = & $tailscale status --json
if ($LASTEXITCODE -ne 0) { throw 'Impossible de lire l’état Tailscale.' }
$status = $statusText | ConvertFrom-Json
if (-not $status.Self.Online) { throw 'Connectez d’abord cet ordinateur à Tailscale.' }

& $tailscale serve --bg --https=443 "http://127.0.0.1:$port"
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve n’a pas pu activer HTTPS.' }

$hostName = ([string]$status.Self.DNSName).TrimEnd('.')
if (-not $hostName) { throw 'Le nom HTTPS Tailscale de cet ordinateur est indisponible.' }
Write-Host ''
Write-Host 'Application Bhantal accessible en HTTPS sur le réseau privé Tailscale :' -ForegroundColor Green
Write-Host "  https://$hostName" -ForegroundColor Cyan
Write-Host ''
Write-Host 'Sur chaque appareil : installez Tailscale, connectez-le au même réseau privé, puis ouvrez cette adresse.'
Write-Host 'Chaque personne devra ensuite utiliser son propre compte Gestion libre.'
Write-Host 'Ne partagez pas cet accès avec une personne qui ne doit pas voir les données de Bhantal.'
