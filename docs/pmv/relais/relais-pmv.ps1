# Relais PMV : transporte les octets entre PMV_Bitmap_Editor.html et les remorques (TCP 9520).
#
# Une page web ne peut pas ouvrir de connexion TCP : ce relais le fait pour elle. Il ne comprend rien
# au protocole — la page fabrique les trames et vérifie les réponses ; le relais les transporte.
#
# Sécurité :
#   · il n'écoute que sur CE poste (http://localhost:9521/) ;
#   · il n'accepte que la page ouverte depuis un FICHIER local (en-tête Origin « null ») : un site web
#     visité dans le navigateur ne peut pas s'en servir pour parler aux remorques ;
#   · il ne se connecte qu'à une adresse IPv4 numérique, sur le port 9520 (ou 3001) — rien d'autre.
#
# Lancement : double-clic sur « Lancer-relais-PMV.cmd », à côté de ce fichier. Laisser la fenêtre ouverte.
#
# API (utilisée par la page) :
#   GET  /etat                                    -> {"ok":true,"version":"…"}
#   POST /echange?hote=IP&port=9520&attente=ms    corps = octets à envoyer (vide = seulement écouter)
#                                                 -> 200 + une trame de réponse complète (55 A8 / 55 A4)
#                                                 -> 204 si rien reçu dans le délai
#   POST /fermer?hote=IP&port=9520                -> ferme la connexion
param([int]$PortEcoute = 9521)

$VERSION = '1.0.0'
$ErrorActionPreference = 'Stop'
$connexions = @{}   # "ip:port" -> @{ client; flux; tampon (List[byte]) }

function Ecrire([string]$texte, [string]$couleur = 'Gray') {
    Write-Host ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $texte) -ForegroundColor $couleur
}

function Repondre($ctx, [int]$code, [byte[]]$corps, [string]$type = 'application/octet-stream') {
    $r = $ctx.Response
    $r.StatusCode = $code
    $r.Headers['Access-Control-Allow-Origin'] = '*'
    $r.Headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    $r.Headers['Access-Control-Allow-Headers'] = 'Content-Type'
    $r.Headers['Access-Control-Allow-Private-Network'] = 'true'
    $r.Headers['Cache-Control'] = 'no-store'
    if ($corps -and $corps.Length) {
        $r.ContentType = $type
        $r.ContentLength64 = $corps.Length
        $r.OutputStream.Write($corps, 0, $corps.Length)
    }
    $r.OutputStream.Close()
}

function Texte([string]$s) { return [System.Text.Encoding]::UTF8.GetBytes($s) }

function Fermer([string]$cle) {
    $c = $connexions[$cle]
    if ($c) { try { $c.client.Close() } catch {}; $connexions.Remove($cle) }
}

function Connexion([string]$hote, [int]$port) {
    $cle = "${hote}:${port}"
    $c = $connexions[$cle]
    if ($c -and $c.client.Connected) { return $c }
    Fermer $cle
    $client = New-Object System.Net.Sockets.TcpClient
    $client.NoDelay = $true
    $tache = $client.ConnectAsync($hote, $port)
    if (-not $tache.Wait(8000)) { try { $client.Close() } catch {}; throw "pas de réponse de $cle en 8 s" }
    $c = @{ client = $client; flux = $client.GetStream(); tampon = New-Object 'System.Collections.Generic.List[byte]' }
    $connexions[$cle] = $c
    Ecrire "connecté à $cle" 'Green'
    return $c
}

# Extrait du tampon une trame de réponse complète (55 A8 / 55 A4 + longueur), ou $null.
function ExtraireTrame($tampon) {
    while ($tampon.Count -ge 2) {
        $i = -1
        for ($k = 0; $k -lt $tampon.Count - 1; $k++) {
            if ($tampon[$k] -eq 0x55 -and ($tampon[$k + 1] -eq 0xA8 -or $tampon[$k + 1] -eq 0xA4)) { $i = $k; break }
        }
        if ($i -lt 0) { # rien qui ressemble à un début de réponse : garder au plus le dernier octet
            $garde = $tampon[$tampon.Count - 1]; $tampon.Clear(); if ($garde -eq 0x55) { $tampon.Add($garde) }; return $null
        }
        if ($i -gt 0) { $tampon.RemoveRange(0, $i) }
        if ($tampon.Count -lt 16) { return $null }
        $total = 16 + $tampon[14] * 4 + ($tampon[4] + 256 * $tampon[5])
        if ($tampon.Count -lt $total) { return $null }
        $trame = $tampon.GetRange(0, $total).ToArray()
        $tampon.RemoveRange(0, $total)
        return ,$trame
    }
    return $null
}

function Echange([string]$hote, [int]$port, [byte[]]$envoi, [int]$attente) {
    $c = Connexion $hote $port
    if ($envoi.Length) {
        try { $c.flux.Write($envoi, 0, $envoi.Length); $c.flux.Flush() }
        catch { # connexion tombée : une seule reconnexion
            Fermer "${hote}:${port}"; $c = Connexion $hote $port
            $c.flux.Write($envoi, 0, $envoi.Length); $c.flux.Flush()
        }
    }
    $fin = [DateTime]::UtcNow.AddMilliseconds($attente)
    $lu = New-Object byte[] 65536
    while ($true) {
        $trame = ExtraireTrame $c.tampon
        if ($trame) { return ,$trame }
        if ([DateTime]::UtcNow -ge $fin) { return $null }
        if ($c.flux.DataAvailable) {
            $n = $c.flux.Read($lu, 0, $lu.Length)
            if ($n -le 0) { Fermer "${hote}:${port}"; throw 'la remorque a fermé la connexion' }
            for ($k = 0; $k -lt $n; $k++) { $c.tampon.Add($lu[$k]) }
        } else {
            if (-not $c.client.Connected) { Fermer "${hote}:${port}"; throw 'connexion perdue' }
            Start-Sleep -Milliseconds 5
        }
    }
}

function LireCorps($req) {
    $ms = New-Object System.IO.MemoryStream
    $req.InputStream.CopyTo($ms)
    return ,$ms.ToArray()
}

# ------------------------------------------------------------------ boucle principale
$ecoute = New-Object System.Net.HttpListener
$ecoute.Prefixes.Add("http://localhost:$PortEcoute/")
try { $ecoute.Start() }
catch {
    Write-Host "Impossible d'écouter sur le port $PortEcoute : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Un autre relais est peut-être déjà ouvert. Fermez-le, puis relancez.' -ForegroundColor Red
    Read-Host 'Entrée pour fermer'; exit 1
}
$Host.UI.RawUI.WindowTitle = 'Relais PMV — laisser ouvert'
Write-Host ''
Write-Host "  Relais PMV $VERSION prêt — http://localhost:$PortEcoute/" -ForegroundColor Cyan
Write-Host '  Laissez cette fenêtre ouverte pendant les envois. Fermez-la pour arrêter.' -ForegroundColor Cyan
Write-Host ''

while ($ecoute.IsListening) {
    $ctx = $ecoute.GetContext()
    $req = $ctx.Request
    try {
        $origine = $req.Headers['Origin']
        if ($req.HttpMethod -eq 'OPTIONS') { Repondre $ctx 204 $null; continue }
        # seule la page ouverte depuis un fichier local (Origin « null ») est servie
        if ($origine -and $origine -ne 'null') {
            Ecrire "refusé : demande venue de $origine" 'Yellow'
            Repondre $ctx 403 (Texte 'origine refusée') 'text/plain; charset=utf-8'; continue
        }
        $chemin = $req.Url.AbsolutePath
        if ($chemin -eq '/etat') {
            Repondre $ctx 200 (Texte ('{"ok":true,"version":"' + $VERSION + '"}')) 'application/json'; continue
        }
        $hote = [string]$req.QueryString['hote']
        $port = 9520; if ($req.QueryString['port']) { $port = [int]$req.QueryString['port'] }
        $ip = $null
        if (-not [System.Net.IPAddress]::TryParse($hote, [ref]$ip) -or $ip.AddressFamily -ne 'InterNetwork' -or $hote -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
            Repondre $ctx 400 (Texte 'adresse IPv4 attendue') 'text/plain; charset=utf-8'; continue
        }
        if ($port -ne 9520 -and $port -ne 3001) {
            Repondre $ctx 400 (Texte 'port refusé (9520 ou 3001 seulement)') 'text/plain; charset=utf-8'; continue
        }
        if ($chemin -eq '/fermer') {
            Fermer "${hote}:${port}"; Ecrire "connexion fermée : ${hote}:${port}"; Repondre $ctx 204 $null; continue
        }
        if ($chemin -eq '/echange' -and $req.HttpMethod -eq 'POST') {
            $attente = 1500; if ($req.QueryString['attente']) { $attente = [Math]::Min(30000, [Math]::Max(50, [int]$req.QueryString['attente'])) }
            $envoi = LireCorps $req
            if ($envoi.Length -gt 4096) { Repondre $ctx 400 (Texte 'trame trop longue') 'text/plain; charset=utf-8'; continue }
            $rep = Echange $hote $port $envoi $attente
            if ($envoi.Length) {
                $info = if ($envoi.Length -ge 14) { '{0}/0x{1:X2}' -f $envoi[12], $envoi[13] } else { '?' }
                Ecrire ("-> ${hote} commande $info, {0} octets" -f $envoi.Length) 'DarkGray'
            }
            if ($rep) { Repondre $ctx 200 $rep } else { Repondre $ctx 204 $null }
            continue
        }
        Repondre $ctx 404 (Texte 'inconnu') 'text/plain; charset=utf-8'
    }
    catch {
        Ecrire ("erreur : " + $_.Exception.Message) 'Red'
        try { Repondre $ctx 502 (Texte $_.Exception.Message) 'text/plain; charset=utf-8' } catch {}
    }
}
