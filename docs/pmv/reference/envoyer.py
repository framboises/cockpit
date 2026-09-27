"""Envoie une image 96 x 64 à une remorque PMV (contrôleur Sigma 3000, protocole JetFileII, TCP 9520).

Déroulé, dans cet ordre :
  1. LECTURE SEULE : fiche système du panneau ; arrêt si la dalle n'est pas 96 x 64.
  2. SAUVEGARDE de la liste de lecture actuelle (SEQUENT.SYS) dans tools/banc/, pour pouvoir restaurer.
     Si elle ne peut pas être lue, arrêt (sauf --sans-sauvegarde).
  3. Envoi du message en D:\\T\\<nom> (commande 2/0x08), puis de la nouvelle liste de lecture (2/0x02).
  ⛔ La commande 2/0x0C (réécriture de la fiche système) n'est JAMAIS émise.

usage :
  python tools/envoyer.py <IP|PLAQUE> --test                  lecture seule, rien n'est modifié
  python tools/envoyer.py <IP|PLAQUE> --mire                  envoie une mire d'essai
  python tools/envoyer.py <IP|PLAQUE> --image dessin.bmp      envoie une image 96 x 64 (BMP, PNG…)
  python tools/envoyer.py <IP|PLAQUE> --restaurer tools/banc/SEQUENT_….SYS
options : --port 9520  --dst 0x0101  --pause 3  --nom PMVED.Nmg  --crc (contrôle 55 A3 au lieu de 55 A7)
Une PLAQUE (AA-000-AA) est résolue en info<PLAQUE>.ddns.net par le DNS du système.
Tout ce qui passe sur le fil est journalisé dans tools/banc/envoi.log.
"""
import argparse, os, re, socket, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jetfile as jf

BANC = os.path.join(HERE, 'banc'); os.makedirs(BANC, exist_ok=True)
LOG = os.path.join(BANC, 'envoi.log')
W, H = 96, 64

def log(msg, echo=True):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    with open(LOG, 'a', encoding='utf-8') as f: f.write(line + '\n')
    if echo: print(msg)

class Echec(Exception):
    pass

# ------------------------------------------------------------------ contrôle CRC (mode 55 A3)
def _table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8): c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
        t.append(c)
    return t
CRC_TAB = _table()

def crc16(b):
    """CRC de la DLL (table vérifiée identique) : CCITT réfléchi, init 0xFFFF, inversé, octets permutés."""
    c = 0xffff
    for x in b: c = (c >> 8) ^ CRC_TAB[(c ^ x) & 0xff]
    return ((~c & 0xff) << 8) | ((~c >> 8) & 0xff)

# ------------------------------------------------------------------ dialogue avec le panneau
class Panneau:
    def __init__(self, hote, port, dst, crc, delai=10.0, essais=3):
        self.dst, self.crc, self.essais, self.delai = dst, crc, essais, delai
        log(f'connexion TCP {hote}:{port} (destination {dst:04x}, contrôle {"CRC 55 A3" if crc else "somme 55 A7"})')
        self.s = socket.create_connection((hote, port), timeout=delai)
        self.s.settimeout(delai)
        self.tampon = b''

    def fermer(self):
        try: self.s.close()
        except OSError: pass

    def trame(self, cmd, sub, args=b'', data=b'', seq=1):
        fr = bytearray(jf.frame(cmd, sub, args, data, seq=seq, dst=self.dst))
        if self.crc:
            fr[1] = 0xa3
            fr[2:4] = struct.pack('<H', crc16(fr[4:]))
        return bytes(fr)

    def _lire_reponse(self):
        """Lit une réponse complète (55 A8 / 55 A4), en sautant d'éventuels octets parasites."""
        fin = time.time() + self.s.gettimeout()
        while True:
            i = self.tampon.find(b'\x55')
            while i >= 0 and i + 1 < len(self.tampon) and self.tampon[i + 1] not in (0xa8, 0xa4):
                i = self.tampon.find(b'\x55', i + 1)
            if i > 0: self.tampon = self.tampon[i:]
            if i >= 0 and len(self.tampon) >= 16:
                total = 16 + self.tampon[14] * 4 + struct.unpack_from('<H', self.tampon, 4)[0]
                if len(self.tampon) >= total:
                    rep, self.tampon = self.tampon[:total], self.tampon[total:]
                    return rep
            if time.time() > fin: raise socket.timeout()
            morceau = self.s.recv(65536)
            if not morceau: raise Echec('le panneau a fermé la connexion')
            log('  <- brut ' + morceau[:64].hex(), echo=False)
            self.tampon += morceau

    def commande(self, cmd, sub, args=b'', data=b'', seq=None):
        """Envoie une trame, attend l'accusé ; rend la réponse. 3 essais, comme la DLL.
        🔴 Numéro de séquence UNIQUE sur toute la session (le paramètre seq est ignoré) : la DLL repart
        de 1 à chaque opération, et le 27/09 l'accusé TARDIF de la lecture de CONFIG.SYS (1/0x02,
        seq 1) a été pris pour celui de la lecture de SEQUENT.SYS (1/0x02, seq 1) — sauvegarde fausse."""
        self._seq = getattr(self, '_seq', 0) % 0xffff + 1
        seq = self._seq
        fr = self.trame(cmd, sub, args, data, seq)
        # 🔴 Mesuré le 27/09 sur deux remorques : le panneau ne répond à une trame qu'à l'arrivée de la
        # SUIVANTE. Chaque trame attendait donc tout le délai, puis sa réémission déclenchait la réponse.
        # Réémettre une trame identique est sans effet de bord (logo reçu intact, chaque paquet en double)
        # ⇒ 1er essai court (1,5 s), puis le délai normal.
        attente = [1.5] + [self.delai] * (self.essais - 1)
        for essai in range(1, self.essais + 1):
            self.s.settimeout(attente[essai - 1])
            log(f'  -> {cmd}/{sub:#04x} seq={seq} {len(fr)} octets : {fr[:48].hex()}', echo=False)
            # 🔴 Mesuré le 27/09 (sonde en lecture seule) : le panneau ne traite une trame qu'une fois
            # AU MOINS UN OCTET DE PLUS reçu derrière elle. Sans lui : aucune réponse ; avec un 00 final :
            # réponse en 0,04 s, et 5 trames enchaînées ainsi ont toutes répondu.
            self.s.sendall(fr + b'\x00')
            # une réponse qui ne correspond pas (typiquement l'accusé TARDIF d'une requête déjà renvoyée,
            # mesuré sur la remorque le 27/09) est ignorée : on continue d'écouter, sans renvoyer.
            code = 2
            while code == 2:
                try:
                    rep = self._lire_reponse()
                except socket.timeout:
                    log(f'  pas de réponse à {cmd}/{sub:#04x} seq={seq} (essai {essai}/{self.essais})', echo=essai > 1)
                    break
                log(f'  <- {rep[:48].hex()}', echo=False)
                code = self._verif_crc(rep, seq, cmd, sub) if self.crc else jf.check_reply(rep, seq, cmd, sub)
                if code == 2:
                    log(f'  réponse ignorée (ne correspond pas à {cmd}/{sub:#04x} seq={seq}) : {rep[:16].hex()}', echo=False)
            if code == 2: continue
            if code == 0: return rep
            raise Echec(f'le panneau refuse {cmd}/{sub:#04x} : code {code:#06x}')
        raise Echec(f'aucune réponse valable à {cmd}/{sub:#04x} après {self.essais} essais')

    def _verif_crc(self, rep, seq, cmd, sub):
        if len(rep) < 16 or rep[:2] != b'\x55\xa4': return 2
        rseq = struct.unpack_from('<H', rep, 10)[0]
        if rseq != seq or rep[12] != cmd or rep[13] != sub: return 2
        if crc16(rep[4:]) != struct.unpack_from('<H', rep, 2)[0]: return 2
        if rep[15] == 0: return 0
        if rep[15] == 1:
            st = struct.unpack_from('<H', rep, 16)[0]
            return 0 if st == 0x9000 else st
        return 0x5004

    # ---------------------------------------------------------- opérations
    def fiche_systeme(self):
        rep = self.commande(1, 0x10)
        return rep[16 + rep[14] * 4:]

    def lire_fichier_systeme(self, nom, psz=0x300):
        """Commande 1/0x02, paquet par paquet ; rend le contenu ou None si le panneau n'a pas le fichier."""
        nm = nom.encode('latin-1')[:11]
        contenu, total, idx = b'', None, 1
        while True:
            args = nm + b'\0' * (12 - len(nm)) + struct.pack('<HH', psz, idx)
            try:
                rep = self.commande(1, 0x02, args, seq=idx)
            except Echec as e:
                if idx == 1: log(f'  lecture de {nom} impossible : {e}'); return None
                raise
            alen, dlen = rep[14] * 4, struct.unpack_from('<H', rep, 4)[0]
            if total is None:
                total = struct.unpack_from('<I', rep, 20)[0] if alen == 8 else struct.unpack_from('<H', rep, 16)[0]
                if total == 0: return None
            contenu += rep[16 + alen:16 + alen + dlen]
            if len(contenu) >= total or dlen == 0: return contenu[:total]
            idx += 1

    def ecrire_fichier(self, chemin, contenu):
        trames = jf.write_file_frames(chemin, contenu, dst=self.dst)
        for i, fr in enumerate(trames):
            alen = fr[14] * 4
            self.commande(fr[12], fr[13], fr[16:16 + alen], fr[16 + alen:], seq=i + 1)
            print(f'\r  {chemin} : paquet {i + 1}/{len(trames)}', end='', flush=True)
        print()

    def ecrire_fichier_systeme(self, nom, contenu):
        for i, fr in enumerate(jf.write_sys_frames(nom, contenu, dst=self.dst)):
            alen = fr[14] * 4
            self.commande(fr[12], fr[13], fr[16:16 + alen], fr[16 + alen:], seq=i + 1)

# ------------------------------------------------------------------ images
def mire():
    """Mire d'essai : tout blanc (demande de l'auteur, 27/09/2026) — chaque pixel éteint se voit."""
    return [[(255, 255, 255)] * W for _ in range(H)]

def mire_couleurs():
    """Mire de couleurs (non utilisée) : cadre blanc, bandes de couleur, damier dans un coin."""
    px = [[(0, 0, 0)] * W for _ in range(H)]
    bandes = [(255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255), (255, 0, 255), (255, 255, 255)]
    for y in range(H):
        for x in range(W):
            if x in (0, W - 1) or y in (0, H - 1):
                px[y][x] = (255, 255, 255)
            elif 20 <= y < 44 and 4 <= x < W - 4:
                px[y][x] = bandes[(x - 4) * len(bandes) // (W - 8)]
            elif y < 16 and x < 16 and (x // 4 + y // 4) % 2:
                px[y][x] = (255, 255, 0)
    return px

def lire_image(chemin):
    from PIL import Image
    im = Image.open(chemin).convert('RGB')
    if im.size != (W, H):
        raise Echec(f'{chemin} fait {im.size[0]} x {im.size[1]}, il faut exactement {W} x {H}')
    return [[im.getpixel((x, y)) for x in range(W)] for y in range(H)]

def resoudre(cible):
    if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', cible): return cible
    plaque = cible.upper().replace(' ', '').replace('-', '')
    nom = f'info{plaque}.ddns.net'
    ip = socket.gethostbyname(nom)
    log(f'{nom} -> {ip}')
    return ip

# ------------------------------------------------------------------ programme
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cible', help='adresse IP ou plaque de la remorque')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--test', action='store_true', help='lecture seule')
    g.add_argument('--mire', action='store_true', help="envoyer la mire d'essai")
    g.add_argument('--image', help='image 96 x 64 à envoyer')
    g.add_argument('--restaurer', help='fichier SEQUENT.SYS sauvegardé à renvoyer')
    ap.add_argument('--port', type=int, default=9520)
    ap.add_argument('--dst', type=lambda v: int(v, 0), default=0x0101)
    ap.add_argument('--pause', type=int, default=3)
    ap.add_argument('--nom', default='PMVED.Nmg')
    ap.add_argument('--crc', action='store_true')
    ap.add_argument('--sans-sauvegarde', action='store_true')
    a = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]{1,8}\.[Nn][Mm][Gg]', a.nom):
        sys.exit('--nom : 8 caractères au plus + .Nmg (format du panneau)')

    log(f'===== {" ".join(sys.argv[1:])}')
    ip = resoudre(a.cible)
    p = Panneau(ip, a.port, a.dst, a.crc)
    try:
        # 1. lecture seule
        # 1/0x10 = style d'affichage par défaut (52 octets, forme de DEFAULT.SYS) — mesuré sur la remorque
        style = p.fiche_systeme()
        horo = time.strftime("%Y%m%d_%H%M%S")
        open(os.path.join(BANC, f'style_{ip}_{horo}.bin'), 'wb').write(style)
        log(f'style par défaut : {len(style)} octets, {style[:16].hex()}')
        # les dimensions de la dalle sont dans CONFIG.SYS, octets 2 et 4 (u16)
        config = p.lire_fichier_systeme('CONFIG.SYS')
        if not config or len(config) < 6:
            raise Echec('CONFIG.SYS illisible : dimensions de la dalle inconnues, arrêt, rien n\'a été modifié')
        open(os.path.join(BANC, f'CONFIG_{ip}_{horo}.SYS'), 'wb').write(config)
        l, h = struct.unpack_from('<HH', config, 2)
        log(f'CONFIG.SYS : {len(config)} octets, dalle {l} x {h}, début {config[:16].hex()}')
        if (l, h) != (W, H):
            raise Echec(f'dalle annoncée {l} x {h} et non {W} x {H} : arrêt, rien n\'a été modifié')
        if a.test:
            log('✅ lecture seule terminée — rien n\'a été modifié sur le panneau'); return

        if a.restaurer:
            contenu = open(a.restaurer, 'rb').read()
            if contenu[:2] != b'SQ':
                raise Echec(f'{a.restaurer} ne commence pas par « SQ » : ce n\'est pas une liste de lecture, refus')
            p.ecrire_fichier_systeme('SEQUENT.SYS', contenu)
            log(f'✅ SEQUENT.SYS restauré depuis {a.restaurer} ({len(contenu)} octets)'); return

        # 2. sauvegarde de la liste de lecture actuelle
        seq_avant = p.lire_fichier_systeme('SEQUENT.SYS')
        if seq_avant and seq_avant[:2] != b'SQ':
            raise Echec(f'la liste de lecture lue ne commence pas par « SQ » ({seq_avant[:8].hex()}) : '
                        'lecture douteuse, arrêt, rien n\'a été modifié')
        if seq_avant:
            sauv = os.path.join(BANC, f'SEQUENT_{ip}_{time.strftime("%Y%m%d_%H%M%S")}.SYS')
            open(sauv, 'wb').write(seq_avant)
            log(f'liste de lecture actuelle sauvegardée : {sauv} ({len(seq_avant)} octets)')
            log(f'  pour la remettre : python tools/envoyer.py {a.cible} --restaurer "{sauv}"')
        elif not a.sans_sauvegarde:
            raise Echec('liste de lecture actuelle illisible : arrêt, rien n\'a été modifié '
                        '(relancer avec --sans-sauvegarde pour passer outre)')

        # 3. envoi
        px = mire() if a.mire else lire_image(a.image)
        fichier = jf.panel_file_like_sigma_editor(jf.bmp565(px), a.pause)
        chemin = 'D:\\T\\' + a.nom
        log(f'envoi de {chemin} ({len(fichier)} octets)')
        p.ecrire_fichier(chemin, fichier)
        p.ecrire_fichier_systeme('SEQUENT.SYS', jf.sequent_sys([('D', 'T', a.nom)]))
        log('✅ message et liste de lecture acceptés par le panneau — vérifier à l\'œil')
    except (Echec, OSError) as e:
        log(f'❌ {e}')
        sys.exit(1)
    finally:
        p.fermer()

if __name__ == '__main__':
    main()
