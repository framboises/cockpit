# Deploie mediamtx.yml (repo) vers le dossier du service mediamtx et le redemarre.
#
# - injecte la cle MEDIAMTX_AUTH_HMAC_KEY (variable machine, la meme que
#   celle lue par Cockpit) a la place de __MEDIAMTX_AUTH_KEY__
# - sauvegarde la config en place (mediamtx.yml.bak-AAAAMMJJ-HHMMSS)
# - redemarre le service mediamtx et verifie que l'API locale repond
#
# Usage (PowerShell admin) : pwsh -File scripts\deploy_mediamtx.ps1

$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "..\mediamtx.yml"
# Fichier de config reellement lu par le service : parametre NSSM
# (E:\TITAN\production\mediamtx\mediamtx.yml ; C:\mediamtx est une ancienne
# installation de test, inutilisee).
$dst = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\mediamtx\Parameters").AppParameters
if (-not $dst -or -not $dst.EndsWith(".yml")) { throw "Config du service mediamtx introuvable dans NSSM (AppParameters)." }
$dstDir = Split-Path $dst
$logFile = Join-Path $dstDir "stdout.log"

$key = [Environment]::GetEnvironmentVariable("MEDIAMTX_AUTH_HMAC_KEY", "Machine")
if (-not $key) { throw "Variable machine MEDIAMTX_AUTH_HMAC_KEY absente : rien n'est deploye." }

$content = (Get-Content $src -Raw).Replace("__MEDIAMTX_AUTH_KEY__", $key)
if ($content.Contains("__MEDIAMTX_AUTH_KEY__")) { throw "Substitution de la cle incomplete." }

if (Test-Path $dst) {
    $bak = "$dst.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item $dst $bak
    Write-Host "Sauvegarde : $bak"
}
Set-Content -Path $dst -Value $content -Encoding utf8NoBOM
Write-Host "Config deployee : $dst"

Restart-Service mediamtx
Start-Sleep -Seconds 3
$svc = Get-Service mediamtx
Write-Host "Service mediamtx : $($svc.Status)"

$paths = $null
for ($i = 0; $i -lt 10 -and -not $paths; $i++) {
    try { $paths = Invoke-RestMethod -Uri "http://127.0.0.1:9997/v3/paths/list" -TimeoutSec 3 }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $paths) { throw "API mediamtx injoignable apres redemarrage : voir $logFile" }
Write-Host ("API mediamtx OK, paths : " + (($paths.items | ForEach-Object { $_.name }) -join ", "))
