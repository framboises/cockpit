# =============================================================================
#  Cockpit - Installation de la tache planifiee "Export Cowork"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_cowork_task.ps1
#
#  La tache tourne TOUS LES JOURS a 05:30 (avant le briefing du matin) : elle
#  regenere activites_site_60j.json et l'ecrase dans SharePoint SAFER / COWORK.
#
#  Pre-requis : connexion Graph faite une fois (cache msal_cowork_cache.bin) :
#    python scripts\export_cowork_activites.py --login
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : export_cowork.bat a cote de ce .ps1)
#    -At        heure de declenchement (defaut : 05:30)
#    -TaskName  nom de la tache (defaut : "Cockpit - Export Cowork")
#    -RunAsUser compte d'execution (defaut : "SYSTEM")
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Export Cowork" /F
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$At        = "05:30",
    [string]$TaskName  = "Cockpit - Export Cowork",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "export_cowork.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path
$cache = Join-Path (Split-Path -Parent (Split-Path -Parent $BatPath)) "msal_cowork_cache.bin"
if (-not (Test-Path $cache)) {
    Write-Error "Connexion Graph absente ($cache) : lancer d'abord python scripts\export_cowork_activites.py --login"
    exit 1
}

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Export Cowork" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Heure       : tous les jours a $At"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

$existing = schtasks /Query /TN "$TaskName" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Tache existante detectee, suppression..." -ForegroundColor Yellow
    schtasks /Delete /TN "$TaskName" /F | Out-Null
}

$taskArgs = @("/Create", "/TN", "$TaskName", "/TR", "`"$BatPath`"", "/SC", "DAILY", "/ST", "$At", "/RL", "HIGHEST", "/F")
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
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\export_cowork-YYYYMMDD.log"
