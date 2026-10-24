# =============================================================================
#  Cockpit - Installation de la tache planifiee "Cockpit - Etat cameras"
#
#  A executer en tant qu'Administrateur, depuis une console interactive (le mot
#  de passe Windows du compte d'execution est demande) :
#    powershell -ExecutionPolicy Bypass -File install_cameras_health_task.ps1
#
#  La tache tourne TOUTES LES 2 MINUTES, 24 h/24 (a partir de 00:01).
#  Elle teste la connexion au port web de chaque camera active, SANS
#  identifiant ni requete (jamais les cameras de piste), et historise l'etat
#  dans cameras_health / cameras_health_log. L'alerte "Cameras - hors ligne"
#  de la centrale (alert_engine.py) lit ce resultat.
#
#  Compte d'execution : celui de Cockpit par defaut (aucun mot de passe camera
#  n'est lu, mais TITAN_ENV / MONGO_URI doivent etre visibles du compte).
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : cameras_health.bat a cote de ce .ps1)
#    -TaskName  nom de la tache (defaut : "Cockpit - Etat cameras")
#    -RunAsUser compte d'execution (defaut : utilisateur courant)
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Etat cameras" /F
#
#  Pour LANCER a la main :
#    python cameras_health.py             (releve)
#    python cameras_health.py --dry-run   (teste les cameras, n'ecrit rien)
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$TaskName  = "Cockpit - Etat cameras",
    [string]$RunAsUser = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "cameras_health.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

if ([Environment]::GetEnvironmentVariable("TITAN_ENV", "Machine") -ne "prod") {
    Write-Warning "TITAN_ENV n'est pas 'prod' au niveau Machine : la tache ecrirait dans titan_dev."
}

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Etat cameras" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Periode     : toutes les 2 minutes (a partir de 00:01)"
Write-Host "  Execute par : $RunAsUser"
Write-Host ""

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = cmd /c "schtasks /Query /TN `"$TaskName`" >nul 2>&1"
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if ($exists) {
    Write-Host "Tache existante detectee, suppression..." -ForegroundColor Yellow
    schtasks /Delete /TN "$TaskName" /F | Out-Null
}

Write-Host "Mot de passe Windows de $RunAsUser demande (tache executee meme sans session ouverte)..." -ForegroundColor Yellow
& schtasks /Create /TN "$TaskName" /TR "`"$BatPath`"" /SC MINUTE /MO 2 /ST 00:01 /RL HIGHEST /RU "$RunAsUser" /RP /F
if ($LASTEXITCODE -ne 0) {
    Write-Error "Echec de la creation de la tache (code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Tache installee avec succes." -ForegroundColor Green
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\cameras_health-YYYYMMDD.log"
