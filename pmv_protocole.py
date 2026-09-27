"""Protocole JetFileII des remorques PMV (controleur Sigma 3000) -- fonctions PURES.

Aucun acces reseau, aucun Flask, aucun Mongo : ce module ne fait que fabriquer et
lire des octets, pour pouvoir etre verifie a l'octet pres contre les vecteurs de
docs/pmv/vecteurs/ (tests/pmv/test_protocole.py).

Les fonctions bmp565, panel_file_like_sigma_editor, frame, check_reply,
write_file_frames, sequent_sys et write_sys_frames sont reprises TELLES QUELLES de
docs/pmv/reference/jetfile.py, l'implementation validee sur remorque le
27/09/2026. Ne pas les "ameliorer" : la moindre difference d'octet et le panneau
n'affiche plus rien (ou pire, affiche autre chose).

Ecarts assumes par rapport a la reference :
  - nmg_from_bmp (forme QZ00SAX de la DLL) est omise : elle n'a jamais ete
    affichee sur une remorque, on n'emploie que la forme Sigma Editor ;
  - frame() applique la liste blanche des commandes (voir COMMANDES_AUTORISEES).
"""
import io
import struct

W, H = 96, 64
PSZ = 0x300                       # taille de paquet (768 octets), celle des remorques
DST = 0x0101                      # adresse JetFileII : groupe 1, unite 1
PORT = 9520
NOM_MESSAGE = "PMVED.Nmg"         # nom 8.3 fixe de NOTRE fichier-message
CHEMIN_MESSAGE = "D:\\T\\" + NOM_MESSAGE   # D: = memoire flash du panneau

# ---------------------------------------------------------------- liste blanche

# Seules ces commandes peuvent partir vers un panneau. Toute autre leve
# CommandeInterdite AVANT la fabrication de la trame.
#   1/0x02 lire un fichier systeme (CONFIG.SYS, SEQUENT.SYS)
#   2/0x08 ecrire un fichier (le fichier-message)
#   2/0x02 ecrire un fichier systeme (SEQUENT.SYS)
# Jamais 2/0x0C (reecrit la configuration d'affichage, effet inconnu).
# Pas de 1/0x10 non plus : absente des vecteurs valides, CONFIG.SYS suffit.
# La liste ne s'elargit qu'apres un essai valide sur remorque.
COMMANDES_AUTORISEES = frozenset({(1, 0x02), (2, 0x08), (2, 0x02)})


class CommandeInterdite(Exception):
    pass


def verifier_commande(cmd, sub):
    if (cmd, sub) not in COMMANDES_AUTORISEES:
        raise CommandeInterdite("commande %d/0x%02x interdite (liste blanche PMV)" % (cmd, sub))


# ---------------------------------------------------------------- image -> fichier panneau

def bmp565(pixels, w=W, h=H):
    """pixels : liste de h lignes de w triplets (r, g, b) 0-255, ligne 0 en haut.
    Rend un BMP 16 bits BI_BITFIELDS 5-6-5, lignes de bas en haut (format de Sigma Editor)."""
    stride = (w * 2 + 3) & ~3
    data = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for (r, g, b) in pixels[y]:
            row += struct.pack('<H', ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
        data += row + b'\0' * (stride - len(row))
    off = 14 + 40 + 12
    hdr = b'BM' + struct.pack('<IHHI', off + len(data), 0, 0, off)
    dib = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 16, 3, len(data), 0, 0, 0, 0)
    masks = struct.pack('<III', 0xF800, 0x07E0, 0x001F)
    return hdr + dib + masks + bytes(data)


def panel_file_like_sigma_editor(bmp, pause_s=3):
    """Forme que recoit le panneau quand on envoie un .Nmg de Sigma Editor :
    <SOH>Z00<STX>AA + codes + bloc RC + BMP + <EOT>. Codes du message envoye a la
    remorque le 27/09/2026 (temp.Nmg)."""
    codes = (b'\x1b0b'                     # mise en page automatique
             + b'\x082'                    # interligne 2
             + b'\x0e2' + b'%04d' % pause_s  # pause longue, en secondes
             + b'\x1f0\x1e0'               # centrage vertical et horizontal
             + b'\x0aI0\x0aO0'             # entree et sortie : saut (affichage direct)
             + b'\x0f2'                    # vitesse 2
             + b'\x070'                    # pas de clignotement
             + b'\x14@0'                   # inserer l'image n. '0' du bloc RC
             + b'\x0d\x04')                # fin de ligne, fin du texte
    rc = b'RC\x01\x00\x010\x00\x00' + struct.pack('<II', len(bmp), 0x28)
    rc += b'\0' * (0x28 - len(rc))
    return b'\x01Z00\x02AA' + codes + rc + bmp + b'\x04'


# ---------------------------------------------------------------- trames

SYNC_REQ = b'\x55\xa7'          # requete, somme de controle simple
SYNC_REP = b'\x55\xa8'          # reponse attendue


def frame(cmd, sub, args=b'', data=b'', seq=1, src=0, dst=0, flag=0):
    verifier_commande(cmd, sub)
    args = args + b'\0' * (-len(args) % 4)
    body = struct.pack('<HHHH', len(data), src, dst, seq) + bytes([cmd, sub, len(args) // 4, flag]) + args + data
    return SYNC_REQ + struct.pack('<H', sum(body) & 0xffff) + body


def check_reply(rep, seq, cmd, sub):
    """Validation telle que la fait la DLL : 0 = succes, 2 = a ignorer, sinon code d'etat."""
    if len(rep) < 16 or rep[:2] != SYNC_REP: return 2
    ck, dlen, src, dst, rseq = struct.unpack_from('<HHHHH', rep, 2)
    if rseq != seq or rep[12] != cmd or rep[13] != sub: return 2
    if sum(rep[4:16 + rep[14] * 4 + dlen]) & 0xffff != ck: return 2
    if rep[15] == 0: return 0
    if rep[15] == 1:
        st = struct.unpack_from('<H', rep, 16)[0]
        return 0 if st == 0x9000 else st
    return 0x5004


def write_file_frames(path, content, psz=PSZ, first_seq=1, dst=0):
    """Ecriture d'un fichier par paquets (commande 2/0x08)."""
    n = (len(content) + psz - 1) // psz
    name = path.encode('latin-1') + b'\0'
    out = []
    for i in range(n):
        args = struct.pack('<IHHH', len(content), psz, n, i + 1) + name
        out.append(frame(2, 0x08, args, content[i * psz:(i + 1) * psz], seq=first_seq + i, dst=dst))
    return out


def sequent_sys(entries):
    """Liste de lecture : entries = [(lecteur, dossier, nom_fichier)], ex. ('D', 'T', 'MSG.Nmg')."""
    out = b'SQ\x04\x00' + struct.pack('<HH', len(entries), 0)
    for drive, folder, name in entries:
        nm = name.encode('latin-1')[:12]
        out += drive.encode() + folder.encode() + b'\x0f\xff' + b'\0' * 20 + nm + b'\0' * (12 - len(nm))
    return out


def write_sys_frames(name, content, psz=PSZ, first_seq=1, dst=0):
    """Ecriture d'un fichier systeme (commande 2/0x02), ex. SEQUENT.SYS."""
    n = (len(content) + psz - 1) // psz
    nm = name.encode('latin-1')[:11]
    out = []
    for i in range(n):
        args = nm + b'\0' * (12 - len(nm)) + struct.pack('<IHHI', len(content), psz, n, i + 1)
        out.append(frame(2, 0x02, args, content[i * psz:(i + 1) * psz], seq=first_seq + i, dst=dst))
    return out


# ---------------------------------------------------------------- lecture (1/0x02)

def read_sys_args(nom, psz=PSZ, idx=1):
    """Arguments de 1/0x02 : nom sur 12 octets (11 caracteres max) + taille paquet + n. paquet."""
    nm = nom.encode('latin-1')[:11]
    return nm + b'\0' * (12 - len(nm)) + struct.pack('<HH', psz, idx)


def parse_read_reply(rep):
    """Reponse a 1/0x02 -> (taille_totale, donnees_de_ce_paquet).
    Taille totale : u32 aux octets 20-23 si 8 octets d'arguments, sinon u16 aux octets 16-17."""
    alen = rep[14] * 4
    dlen = struct.unpack_from('<H', rep, 4)[0]
    total = struct.unpack_from('<I', rep, 20)[0] if alen == 8 else struct.unpack_from('<H', rep, 16)[0]
    return total, rep[16 + alen:16 + alen + dlen]


def parse_config_dims(config):
    """CONFIG.SYS : largeur u16 aux octets 2-3, hauteur u16 aux octets 4-5. None si trop court."""
    if not config or len(config) < 6:
        return None
    return struct.unpack_from('<HH', config, 2)


def is_sequent(contenu):
    return bool(contenu) and contenu[:2] == b'SQ'


def parse_sequent(contenu):
    """SEQUENT.SYS -> liste des noms de fichiers joues. ValueError si ce n'est pas une liste 'SQ'.
    Nombre d'entrees u16 aux octets 4-5 ; entrees de 36 octets a partir de l'octet 8 ;
    nom sur 12 octets a l'offset 24 de l'entree."""
    if not is_sequent(contenu):
        raise ValueError("pas une liste de lecture (ne commence pas par SQ)")
    n = struct.unpack_from('<H', contenu, 4)[0] if len(contenu) >= 6 else 0
    noms = []
    for i in range(n):
        base = 8 + 36 * i
        brut = contenu[base + 24:base + 36]
        if len(brut) < 12:
            break
        noms.append(brut.split(b'\0')[0].decode('latin-1', 'replace'))
    return noms


# ---------------------------------------------------------------- pixels

class ImageInvalide(ValueError):
    pass


def pixels_from_image_bytes(donnees):
    """PNG / BMP / JPG -> pixels 96 x 64 (lignes de triplets RGB). Refuse toute autre taille :
    le recadrage se fait dans l'editeur, jamais cote serveur."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(donnees))
        im.load()
    except Exception:
        raise ImageInvalide("image illisible")
    if im.size != (W, H):
        raise ImageInvalide("l'image fait %d x %d, il faut exactement %d x %d" % (im.size[0], im.size[1], W, H))
    im = im.convert('RGB')
    return [[im.getpixel((x, y)) for x in range(W)] for y in range(H)]


def pixels_from_rgb(rgb):
    """Octets RGB bruts (96 x 64 x 3 = 18 432) -> pixels."""
    if len(rgb) != W * H * 3:
        raise ImageInvalide("taille RGB invalide")
    return [[tuple(rgb[(y * W + x) * 3:(y * W + x) * 3 + 3]) for x in range(W)] for y in range(H)]


def rgb_from_pixels(pixels):
    out = bytearray()
    for ligne in pixels:
        for (r, g, b) in ligne:
            out += bytes((r, g, b))
    return bytes(out)


def png_from_pixels(pixels):
    from PIL import Image
    im = Image.frombytes('RGB', (W, H), rgb_from_pixels(pixels))
    buf = io.BytesIO()
    im.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def fichier_message(pixels, pause_s=3):
    """Pixels -> fichier-message pret a ecrire en D:\\T\\PMVED.Nmg."""
    return panel_file_like_sigma_editor(bmp565(pixels), pause_s)


def trames_envoi(pixels, dst=DST):
    """Sequence COMPLETE d'un envoi, telle qu'elle doit partir sur le fil (sans l'octet 00
    final ajoute a l'emission) : lecture CONFIG.SYS, lecture SEQUENT.SYS, fichier-message
    (2/0x08), liste de lecture (2/0x02). Numeros de sequence 1, 2, 3... sur toute la session.
    Sert de reference aux tests (vecteurs/*.trames-envoi.hex)."""
    out = [frame(1, 0x02, read_sys_args('CONFIG.SYS'), seq=1, dst=dst),
           frame(1, 0x02, read_sys_args('SEQUENT.SYS'), seq=2, dst=dst)]
    fic = write_file_frames(CHEMIN_MESSAGE, fichier_message(pixels), first_seq=3, dst=dst)
    out += fic
    out += write_sys_frames('SEQUENT.SYS', sequent_sys([('D', 'T', NOM_MESSAGE)]),
                            first_seq=3 + len(fic), dst=dst)
    return out
