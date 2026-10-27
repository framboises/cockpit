# =============================================================================
#  Cockpit - Installation de la tache planifiee "Sync Voisins"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_voisins_task.ps1
#
#  La tache tourne 3 FOIS PAR JOUR (02:00, 06:15 et 13:15) : programmes
#  d'Antares, matchs a domicile du MSB et du Mans FC (horaires TV fixes
#  quelques semaines avant), puis vignettes de la timeline SAISON, puis
#  preremplissage Groundmaster des matchs du Mans FC (groundmaster\foot_sync.py).
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
Write-Host "  Periode     : tous les jours a 02:00, 06:15 et 13:15"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

# Plusieurs declencheurs quotidiens : schtasks /Create n'en pose qu'un, on
# passe par Register-ScheduledTask (remplace la tache existante).
$action = New-ScheduledTaskAction -Execute $BatPath
$triggers = @("02:00", "06:15", "13:15") | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
if ($RunAsUser -ieq "SYSTEM") {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Force | Out-Null
} else {
    $cred = Get-Credential -UserName $RunAsUser -Message "Mot de passe du compte d'execution"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
        -User $RunAsUser -Password $cred.GetNetworkCredential().Password -RunLevel Highest -Force | Out-Null
}

Write-Host ""
Write-Host "Tache installee avec succes." -ForegroundColor Green
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\voisins_sync-YYYYMMDD.log"
Write-Host "Donnees : collection voisins_events ; vignettes origin 'voisins' dans timetable SAISON."
