# =============================================================================
#  Cockpit - Installation de la tache planifiee "Collecte Musee"
#
#  A executer en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_musee_task.ps1
#
#  La tache tourne TOUTES LES 5 MINUTES, 24 h/24 (decalee a HH:01, HH:06...).
#  Elle doit tourner AVANT l'ouverture (10h) : le releve d'avant ouverture sert
#  de base des visiteurs du jour. Lecture seule cote borne Handshake, ecrit
#  uniquement musee_releves / musee_passages / musee_jours. Autonome du live
#  controle (tache "Live controle acces") : aucune dependance entre les deux.
#
#  Configuration (sans reinstaller) : cockpit_settings {_id: "musee"} :
#    enabled, ouverture "10:00", fermeture "19:00", jours_fermes [], area_id,
#    locations [...], transactions.
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : musee_collect.bat a cote de ce .ps1)
#    -TaskName  nom de la tache (defaut : "Cockpit - Collecte Musee")
#    -RunAsUser compte d'execution (defaut : "SYSTEM")
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Collecte Musee" /F
#
#  Pour LANCER a la main :
#    python scripts\musee_collect.py             (collecte)
#    python scripts\musee_collect.py --dry-run   (interroge la borne, n'ecrit rien)
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$TaskName  = "Cockpit - Collecte Musee",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "musee_collect.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

if ($RunAsUser -ieq "SYSTEM") {
    $envMachine = [Environment]::GetEnvironmentVariable("TITAN_ENV", "Machine")
    if ($envMachine -ne "prod") {
        Write-Warning "TITAN_ENV n'est pas 'prod' au niveau Machine (valeur : '$envMachine') : la tache ecrirait dans titan_dev."
    }
}

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Collecte Musee" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Periode     : toutes les 5 minutes (a partir de 00:01)"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

# Sous Windows PowerShell 5, avec ErrorActionPreference=Stop, le message
# d'erreur de schtasks quand la tache n'existe pas encore ("Le fichier
# specifie est introuvable") devient une erreur bloquante : on le tolere ici.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = cmd /c "schtasks /Query /TN `"$TaskName`" >nul 2>&1"
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if ($exists) {
    Write-Host "Tache existante detectee, suppression..." -ForegroundColor Yellow
    schtasks /Delete /TN "$TaskName" /F | Out-Null
}

$taskArgs = @(
    "/Create",
    "/TN", "$TaskName",
    "/TR", "`"$BatPath`"",
    "/SC", "MINUTE",
    "/MO", "5",
    "/ST", "00:01",
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
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\musee_collect-YYYYMMDD.log"
Write-Host "Donnees : musee_releves, musee_passages, musee_jours ; etat : GET /api/musee/state"
