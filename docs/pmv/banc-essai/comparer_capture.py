"""Recette : ce que le jumeau a reçu de la page est-il identique à ce qui a été validé sur remorque ?

usage :
  1. python banc-essai/faux_panneau.py            (laisser tourner)
  2. lancer le relais, ouvrir la page, envoyer une image de vecteurs/ (ex. mire-couleurs.png, importée
     dans l'éditeur) vers l'adresse 127.0.0.1
  3. python banc-essai/comparer_capture.py vecteurs/mire-couleurs.png

Compare, hors numéro de séquence et somme de contrôle (qui en dépend) :
  · toutes les trames 2/0x08 (le fichier-message, paquet par paquet) ;
  · la trame 2/0x02 (la liste de lecture SEQUENT.SYS).
Vérifie aussi qu'AUCUNE trame 2/0x0C n'a été émise. Seules les trames de la DERNIÈRE connexion comptent.
Le jumeau exige l'octet 00 final (comme la remorque) : sans lui, la page n'aurait obtenu aucune réponse.
"""
import os, struct, sys
from PIL import Image
ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ICI, '..', 'reference'))
import jetfile as jf

img = Image.open(sys.argv[1]).convert('RGB')
assert img.size == (96, 64), 'image 96 x 64 attendue'
px = [[img.getpixel((x, y)) for x in range(96)] for y in range(64)]
fichier = jf.panel_file_like_sigma_editor(jf.bmp565(px), 3)
attendu_fic = jf.write_file_frames('D:\\T\\PMVED.Nmg', fichier, dst=0x0101)
attendu_seq = jf.write_sys_frames('SEQUENT.SYS', jf.sequent_sys([('D', 'T', 'PMVED.Nmg')]), dst=0x0101)

raw = open(os.path.join(ICI, 'banc', 'frames.bin'), 'rb').read()
cap, i = [], 0
while i < len(raw):
    n = struct.unpack_from('<I', raw, i)[0]; cap.append(raw[i + 4:i + 4 + n]); i += 4 + n
# dernier envoi : à partir de la dernière lecture de CONFIG.SYS
debut = max((k for k, f in enumerate(cap) if f[12:14] == b'\x01\x02' and b'CONFIG.SYS' in f[16:32]), default=0)
cap = cap[debut:]
# une réémission légitime (même trame, à l'identique, après un délai sans réponse) ne compte qu'une fois
cap = [f for k, f in enumerate(cap) if k == 0 or f != cap[k - 1]]

def sans_seq(fr):
    fr = bytearray(fr); fr[2:4] = b'\0\0'; fr[10:12] = b'\0\0'; return bytes(fr)

ok = True
def verdict(nom, bon, detail=''):
    global ok
    ok &= bon
    print(('IDENTIQUE  ' if bon else 'DIFFÉRENT  ') + nom + ('' if bon else '  ' + detail))

recu_fic = [f for f in cap if f[12:14] == b'\x02\x08']
recu_seq = [f for f in cap if f[12:14] == b'\x02\x02']
verdict(f'fichier-message : {len(recu_fic)} trames 2/0x08 reçues, {len(attendu_fic)} attendues',
        [sans_seq(f) for f in recu_fic] == [sans_seq(f) for f in attendu_fic],
        '→ comparer vecteurs/*.fichier-panneau.bin et *.trames-envoi.hex')
verdict('liste de lecture : trame 2/0x02', [sans_seq(f) for f in recu_seq] == [sans_seq(f) for f in attendu_seq])
verdict('aucune trame 2/0x0C (interdite)', not any(f[12:14] == b'\x02\x0c' for f in cap))
seqs = [struct.unpack_from('<H', f, 10)[0] for f in cap]
verdict('numéros de séquence tous différents sur la session', len(seqs) == len(set(seqs)), str(seqs))
print('BILAN :', 'conforme à ce qui a été validé sur remorque' if ok else 'ÉCARTS — ne pas essayer sur une remorque')
