# =============================================================================
#  Cockpit - Installation de la tache planifiee "Sync Momentus"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_momentus_task.ps1
#
#  La tache tourne TOUTES LES HEURES (a HH:05). Le script fait un import
#  incremental, et un import complet quand le dernier a plus de 20 h.
#
#  Pre-requis : MOMENTUS_CLIENT_ID et MOMENTUS_CLIENT_SECRET definis au niveau
#  MACHINE (la tache tourne en SYSTEM, qui ne lit pas les variables
#  utilisateur). Ce script le verifie et refuse d'installer sinon.
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : momentus_sync.bat a cote de ce .ps1)
#    -TaskName  nom de la tache (defaut : "Cockpit - Sync Momentus")
#    -RunAsUser compte d'execution (defaut : "SYSTEM")
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Sync Momentus" /F
#
#  Pour LANCER a la main :
#    python momentus_sync.py            (incremental)
#    python momentus_sync.py --full     (complet + reconciliation)
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$TaskName  = "Cockpit - Sync Momentus",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "momentus_sync.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

if ($RunAsUser -ieq "SYSTEM") {
    foreach ($n in "MOMENTUS_CLIENT_ID", "MOMENTUS_CLIENT_SECRET") {
        if (-not [Environment]::GetEnvironmentVariable($n, "Machine")) {
            Write-Error "$n n'est pas defini au niveau Machine : la tache (SYSTEM) ne pourrait pas s'authentifier."
            exit 1
        }
    }
}

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Sync Momentus" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Periode     : toutes les heures (HH:05)"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

$existing = schtasks /Query /TN "$TaskName" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Tache existante detectee, suppression..." -ForegroundColor Yellow
    schtasks /Delete /TN "$TaskName" /F | Out-Null
}

$taskArgs = @(
    "/Create",
    "/TN", "$TaskName",
    "/TR", "`"$BatPath`"",
    "/SC", "HOURLY",
    "/MO", "1",
    "/ST", "00:05",
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
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\momentus_sync-YYYYMMDD.log"
Write-Host "Etat de la synchro : collection momentus_sync_state (_id 'state'), historique momentus_sync_runs."
