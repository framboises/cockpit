# =============================================================================
#  Cockpit - Installation de la tache planifiee "Rapport matinal PC Org"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_morning_report_task.ps1
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : meme repertoire que ce .ps1)
#    -Time      heure d'execution (defaut : "07:00")
#    -TaskName  nom de la tache (defaut : "Cockpit - Rapport matinal PC Org")
#    -RunAsUser compte sous lequel la tache tourne (defaut : "SYSTEM")
#               Pour utiliser un compte specifique : -RunAsUser "DOMAIN\user"
#               Sera demande mot de passe interactivement.
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Rapport matinal PC Org" /F
#
#  Pour TESTER le .bat sans attendre 07h :
#    schtasks /Run /TN "Cockpit - Rapport matinal PC Org"
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$Time      = "07:00",
    [string]$TaskName  = "Cockpit - Rapport matinal PC Org",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"

# Resolution automatique du chemin du .bat si non fourni
if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "morning_report.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Rapport matinal" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Heure       : $Time (tous les jours)"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

# Suppression de l'ancienne tache si elle existe
$existing = schtasks /Query /TN "$TaskName" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Tache existante detectee, suppression..." -ForegroundColor Yellow
    schtasks /Delete /TN "$TaskName" /F | Out-Null
}

# Creation
$args = @(
    "/Create",
    "/TN", "$TaskName",
    "/TR", "`"$BatPath`"",
    "/SC", "DAILY",
    "/ST", "$Time",
    "/RL", "HIGHEST",
    "/F"
)

if ($RunAsUser -ieq "SYSTEM") {
    $args += @("/RU", "SYSTEM")
} else {
    $args += @("/RU", $RunAsUser)
    Write-Host "Mot de passe pour $RunAsUser sera demande..." -ForegroundColor Yellow
    # /RP omis : sera demande interactivement
}

& schtasks @args
if ($LASTEXITCODE -ne 0) {
    Write-Error "Echec de la creation de la tache (code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Tache installee avec succes." -ForegroundColor Green
Write-Host ""
Write-Host "Verification :"
schtasks /Query /TN "$TaskName" /V /FO LIST | Select-String "Nom de la tache","Prochaine execution","Etat","Derniere execution","Resultat"
Write-Host ""
Write-Host "Pour declencher un test immediat :" -ForegroundColor Cyan
Write-Host "  schtasks /Run /TN `"$TaskName`""
Write-Host ""
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\morning_report-YYYYMMDD.log"
