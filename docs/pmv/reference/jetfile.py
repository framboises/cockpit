"""Implémentation de référence, indépendante de czJetFileII.dll, de ce qu'il faut pour
afficher un bitmap 96 x 64 sur un panneau Sigma 3000 (protocole JetFileII complet).

Tout ce qui est ici a été établi par capture de la DLL du fabricant contre un faux panneau.
"""
import struct

# ---------------------------------------------------------------- image -> fichier panneau

def bmp565(pixels, w=96, h=64):
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

def nmg_from_bmp(bmp, pause_s=3, effect=0x30):
    """Équivalent exact de czBmp2Nmg(bmp, sortie, pause_s, effect)."""
    eff = effect if effect > 0x2e else 0x30
    head = bytearray(0x46)
    head[0:7] = b'QZ00SAX'
    codes = (b'\x1a1'                      # police 7x6
             + b'\x1c/\xff\xff\xff'        # couleur du texte : blanc (BGR)
             + b'\x0aI' + bytes([eff])     # effet d'entrée
             + b'\x0aO' + bytes([eff])     # effet de sortie
             + b'\x0e2' + b'%04d' % pause_s  # pause longue, en secondes
             + b'\x14@0'                   # insérer l'image n° '0' du bloc RC
             + b'\x04')                    # fin du texte
    head[7:7 + len(codes)] = codes
    rc = 7 + len(codes)                    # début du bloc RC
    head[rc:rc + 8] = b'RC\x01\x00\x010\x00\x00'   # 1 ressource, étiquette '0'
    struct.pack_into('<II', head, rc + 8, len(bmp), 0x46 - rc)  # taille, décalage depuis 'RC'
    return bytes(head) + bmp

def panel_file_like_sigma_editor(bmp, pause_s=3):
    """Forme que reçoit le panneau quand on envoie un .Nmg de Sigma Editor : la DLL le transforme en
    <SOH>Z00<STX>AA + corps du .Nmg à partir de l'octet 18 + <EOT>. Les codes ci-dessous sont ceux du
    message envoyé à la remorque le 27/09/2026 (temp.Nmg) ; l'annexe « NoteNmg » de l'éditeur est omise."""
    codes = (b'\x1b0b'                     # mise en page automatique
             + b'\x082'                    # interligne 2
             + b'\x0e2' + b'%04d' % pause_s  # pause longue, en secondes
             + b'\x1f0\x1e0'               # centrage vertical et horizontal
             + b'\x0aI0\x0aO0'             # entrée et sortie : saut (affichage direct)
             + b'\x0f2'                    # vitesse 2
             + b'\x070'                    # pas de clignotement
             + b'\x14@0'                   # insérer l'image n° '0' du bloc RC
             + b'\x0d\x04')                # fin de ligne, fin du texte
    rc = b'RC\x01\x00\x010\x00\x00' + struct.pack('<II', len(bmp), 0x28)
    rc += b'\0' * (0x28 - len(rc))
    return b'\x01Z00\x02AA' + codes + rc + bmp + b'\x04'

# ---------------------------------------------------------------- trames

SYNC_REQ = b'\x55\xa7'          # requête, somme de contrôle simple
SYNC_REP = b'\x55\xa8'          # réponse attendue

def frame(cmd, sub, args=b'', data=b'', seq=1, src=0, dst=0, flag=0):
    args = args + b'\0' * (-len(args) % 4)
    body = struct.pack('<HHHH', len(data), src, dst, seq) + bytes([cmd, sub, len(args) // 4, flag]) + args + data
    return SYNC_REQ + struct.pack('<H', sum(body) & 0xffff) + body

def check_reply(rep, seq, cmd, sub):
    """Validation telle que la fait la DLL : 0 = succès, 2 = à réessayer, sinon code d'état."""
    if len(rep) < 16 or rep[:2] != SYNC_REP: return 2
    ck, dlen, src, dst, rseq = struct.unpack_from('<HHHHH', rep, 2)
    if rseq != seq or rep[12] != cmd or rep[13] != sub: return 2
    if sum(rep[4:16 + rep[14] * 4 + dlen]) & 0xffff != ck: return 2
    if rep[15] == 0: return 0
    if rep[15] == 1:
        st = struct.unpack_from('<H', rep, 16)[0]
        return 0 if st == 0x9000 else st
    return 0x5004

def write_file_frames(path, content, psz=0x300, first_seq=1, dst=0):
    """Écriture d'un fichier par paquets (commande 2/0x08, variante choisie par la DLL pour ce panneau)."""
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

def write_sys_frames(name, content, psz=0x300, first_seq=1, dst=0):
    """Écriture d'un fichier système (commande 2/0x02), ex. SEQUENT.SYS."""
    n = (len(content) + psz - 1) // psz
    nm = name.encode('latin-1')[:11]
    out = []
    for i in range(n):
        args = nm + b'\0' * (12 - len(nm)) + struct.pack('<IHHI', len(content), psz, n, i + 1)
        out.append(frame(2, 0x02, args, content[i * psz:(i + 1) * psz], seq=first_seq + i, dst=dst))
    return out
