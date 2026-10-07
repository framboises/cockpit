# =============================================================================
#  Cockpit - Installation de la tache planifiee "Sync Voisins"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_voisins_task.ps1
#
#  La tache tourne 2 FOIS PAR JOUR (06:15 et 13:15) : programmes d'Antares,
#  matchs a domicile du MSB et du Mans FC (horaires TV fixes quelques
#  semaines avant), puis vignettes de la timeline SAISON.
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : voisins_sync.bat a cote de ce .ps1)
#    -TaskName  nom de la tache (defaut : "Cockpit - Sync Voisins")
#    -RunAsUser compte d'execution (defaut : "SYSTEM")
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Sync Voisins" /F
#
#  Pour LANCER a la main :
#    python voisins_sync.py --dry-run   (affiche sans ecrire)
#    python voisins_sync.py
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$TaskName  = "Cockpit - Sync Voisins",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "voisins_sync.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Sync Voisins" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Periode     : tous les jours a 06:15 et 13:15"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

# Windows PowerShell 5.1 + ErrorActionPreference=Stop : le message d'erreur de
# schtasks (tache absente) devient une erreur bloquante, meme redirige. On
# passe par cmd pour ne lire que le code retour.
cmd /c "schtasks /Query /TN `"$TaskName`" >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Tache existante detectee, suppression..." -ForegroundColor Yellow
    schtasks /Delete /TN "$TaskName" /F | Out-Null
}

# Quotidienne a 06:15, repetee toutes les 7 h pendant 8 h (=> 06:15 et 13:15)
$taskArgs = @(
    "/Create",
    "/TN", "$TaskName",
    "/TR", "`"$BatPath`"",
    "/SC", "DAILY",
    "/ST", "06:15",
    "/RI", "420",
    "/DU", "08:00",
    "/RL", "HIGHEST",
    "/F"
)

if ($RunAsUser -ieq "SYSTEM") {
    $taskArgs += @("/RU", "SYSTEM")
} else {
    $taskArgs += @("/RU", $RunAsUser)
    Write-Host "Mot de passe pour $RunAsUser sera demande..." -ForegroundColor Yellow
}

& schtasks @taskArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Echec de la creation de la tache (code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Tache installee avec succes." -ForegroundColor Green
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\voisins_sync-YYYYMMDD.log"
Write-Host "Donnees : collection voisins_events ; vignettes origin 'voisins' dans timetable SAISON."
