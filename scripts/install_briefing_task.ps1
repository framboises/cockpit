# =============================================================================
#  Cockpit - Installation de la tache planifiee "Briefing de releve"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_briefing_task.ps1
#
#  La tache tourne TOUTES LES 15 MINUTES. Elle ne fait rien tant que
#  l'interrupteur n'est pas active dans Cockpit (Briefing > Releve auto) ;
#  les heures de releve se reglent dans Cockpit, pas ici.
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : briefing_releve.bat a cote de ce .ps1)
#    -Minutes   periode en minutes (defaut : 15 ; doit rester egale a la
#               fenetre --window du script, 15 par defaut)
#    -TaskName  nom de la tache (defaut : "Cockpit - Briefing de releve")
#    -RunAsUser compte d'execution (defaut : "SYSTEM")
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Briefing de releve" /F
#
#  Pour TESTER sans attendre un creneau (aucun cout, aucun mail) :
#    python scripts\briefing_releve.py --force --no-llm --dry-run
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [int]$Minutes      = 15,
    [string]$TaskName  = "Cockpit - Briefing de releve",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "briefing_releve.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Briefing de releve" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Periode     : toutes les $Minutes minutes"
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
    "/SC", "MINUTE",
    "/MO", "$Minutes",
    "/ST", "00:00",
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
Write-Host ""
schtasks /Query /TN "$TaskName" /V /FO LIST | Select-String "Nom de la tache","Prochaine execution","Etat","Derniere execution","Resultat"
Write-Host ""
Write-Host "Activer l'envoi et regler les heures dans Cockpit : Briefing > Releve auto." -ForegroundColor Cyan
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\briefing_releve-YYYYMMDD.log"
