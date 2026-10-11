# =============================================================================
#  Cockpit - Installation de la tache planifiee "Cockpit - Capture IA"
#
#  A executer en tant qu'Administrateur, depuis une console interactive (le mot
#  de passe Windows du compte d'execution est demande) :
#    powershell -ExecutionPolicy Bypass -File install_capture_ia_task.ps1
#
#  La tache tourne TOUTES LES MINUTES, 24 h/24, meme sans session ouverte, et
#  survit aux redemarrages du serveur. A chaque passage, capture_ia.py ne prend
#  que les cameras dues (rythme propre a chaque camera, page IA) et dans leur
#  planning : paire de photos, vehicules detectes par l'IA locale (service
#  IntrusionVerifier), etat du trafic enregistre.
#
#  Compte d'execution : celui de Cockpit (defaut : l'utilisateur courant), car la
#  cle COCKPIT_CAMERA_KEY est une variable UTILISATEUR. Sous SYSTEM la tache
#  echouerait ("COCKPIT_CAMERA_KEY absente" dans le log).
#
#  Parametres optionnels :
#    -BatPath   chemin du .bat (defaut : capture_ia.bat a cote de ce .ps1)
#    -TaskName  nom de la tache (defaut : "Cockpit - Capture IA")
#    -RunAsUser compte d'execution (defaut : utilisateur courant)
#
#  Pour SUPPRIMER la tache :
#    schtasks /Delete /TN "Cockpit - Capture IA" /F
#
#  Pour LANCER a la main :
#    python capture_ia.py             (un passage)
#    python capture_ia.py --force     (toutes les cameras, meme non dues)
#    python capture_ia.py --dry-run   (capture et analyse, n'ecrit rien en base)
# =============================================================================

[CmdletBinding()]
param(
    [string]$BatPath   = "",
    [string]$TaskName  = "Cockpit - Capture IA",
    [string]$RunAsUser = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BatPath)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $BatPath = Join-Path $scriptDir "capture_ia.bat"
}
if (-not (Test-Path $BatPath)) {
    Write-Error "Fichier introuvable : $BatPath"
    exit 1
}
$BatPath = (Resolve-Path $BatPath).Path

if ($RunAsUser -ieq "SYSTEM") {
    Write-Error "SYSTEM n'a pas la cle COCKPIT_CAMERA_KEY (variable utilisateur) : utiliser le compte de Cockpit."
    exit 1
}
if ([Environment]::GetEnvironmentVariable("TITAN_ENV", "Machine") -ne "prod") {
    Write-Warning "TITAN_ENV n'est pas 'prod' au niveau Machine : la tache ecrirait dans titan_dev."
}

Write-Host ""
Write-Host "Installation de la tache planifiee Cockpit - Capture IA" -ForegroundColor Cyan
Write-Host "  Nom         : $TaskName"
Write-Host "  Script      : $BatPath"
Write-Host "  Periode     : toutes les minutes"
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
& schtasks /Create /TN "$TaskName" /TR "`"$BatPath`"" /SC MINUTE /MO 1 /RL HIGHEST /RU "$RunAsUser" /RP /F
if ($LASTEXITCODE -ne 0) {
    Write-Error "Echec de la creation de la tache (code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Tache installee avec succes." -ForegroundColor Green
Write-Host "Logs ecrits dans <COCKPIT_DIR>\logs\capture_ia-YYYYMMDD.log"
