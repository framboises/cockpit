"""Dialogue complet contre le JUMEAU de remorque (docs/pmv/banc-essai/faux_panneau.py).

Le jumeau est lance dans un sous-processus sur un port libre de 127.0.0.1 ; il exige
l'octet 00 final comme la vraie remorque. On verifie les trames qu'il a recues
(frames.bin, lu a partir de la taille qu'il avait avant le test) contre la reference.
Aucune adresse autre que 127.0.0.1 n'est jamais contactee.
"""
import os
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, RACINE)

import pmv_panneau as pn   # noqa: E402
import pmv_protocole as pp  # noqa: E402

BANC = os.path.join(RACINE, "docs", "pmv", "banc-essai")
FRAMES = os.path.join(BANC, "banc", "frames.bin")
VECTEURS = os.path.join(RACINE, "docs", "pmv", "vecteurs")


def _port_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def jumeau():
    port = _port_libre()
    proc = subprocess.Popen([sys.executable, os.path.join(BANC, "faux_panneau.py"), str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fin = time.time() + 10
    while time.time() < fin:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("le jumeau n'a pas demarre")
    yield port
    proc.kill()
    proc.wait(5)


def _taille_frames():
    return os.path.getsize(FRAMES) if os.path.exists(FRAMES) else 0


def _frames_depuis(debut):
    with open(FRAMES, "rb") as f:
        f.seek(debut)
        brut = f.read()
    out, i = [], 0
    while i < len(brut):
        n = struct.unpack_from("<I", brut, i)[0]
        out.append(brut[i + 4:i + 4 + n])
        i += 4 + n
    return out


def _pixels(nom):
    with open(os.path.join(VECTEURS, nom + ".png"), "rb") as f:
        return pp.pixels_from_image_bytes(f.read())


def test_test_connexion_lecture_seule(jumeau):
    debut = _taille_frames()
    with pn.Panneau("127.0.0.1", jumeau) as p:
        res = pn.operation_test(p)
    assert res["dalle"] == [96, 64]
    assert res["noms"] == ["temp.Nmg"]
    recues = _frames_depuis(debut)
    assert recues and all(f[12] == 1 and f[13] == 0x02 for f in recues)   # aucune trame 2/0x..


@pytest.mark.parametrize("nom", ["mire-couleurs", "degrade", "noir", "blanc"])
def test_envoi_conforme_a_la_reference(jumeau, nom):
    debut = _taille_frames()
    px = _pixels(nom)
    etapes = []
    with pn.Panneau("127.0.0.1", jumeau) as p:
        pn.operation_envoi(p, pp.fichier_message(px), etape=lambda *a: etapes.append(a))
    recues = _frames_depuis(debut)
    # a l'octet pres, numeros de sequence compris (1 a 20 sur la session)
    assert recues == pp.trames_envoi(px)
    seqs = [struct.unpack_from("<H", f, 10)[0] for f in recues]
    assert len(seqs) == len(set(seqs))
    assert not any(f[12:14] == b"\x02\x0c" for f in recues)
    assert ("affiche", "ok") in [e[:2] for e in etapes]


def test_sauvegarde_garde_l_affichage_d_origine(jumeau):
    """Scenario 3 : apres deux envois, la sauvegarde nomme toujours temp.Nmg ;
    scenario 4 : la restauration la remet."""
    port = jumeau
    with pn.Panneau("127.0.0.1", port) as p:
        pn.operation_restaurer(p, open(os.path.join(BANC, "donnees-remorque", "SEQUENT.SYS"), "rb").read())
    sauvegarde = None
    for nom in ("noir", "blanc"):
        with pn.Panneau("127.0.0.1", port) as p:
            r = pn.operation_envoi(p, pp.fichier_message(_pixels(nom)), sauvegarde_existante=sauvegarde)
        if r["nouvelle_sauvegarde"] is not None:
            sauvegarde = r["nouvelle_sauvegarde"]
    assert pp.parse_sequent(sauvegarde) == ["temp.Nmg"]
    with pn.Panneau("127.0.0.1", port) as p:
        pn.operation_restaurer(p, sauvegarde)
        assert pn.operation_test(p)["noms"] == ["temp.Nmg"]


def test_restaurer_refuse_une_sauvegarde_invalide(jumeau):
    with pn.Panneau("127.0.0.1", jumeau) as p:
        with pytest.raises(pn.EchecPanneau) as e:
            pn.operation_restaurer(p, b"XX-pas-une-liste")
    assert e.value.code == "sauvegarde_invalide"


def test_commande_interdite_jamais_emise(jumeau):
    debut = _taille_frames()
    with pn.Panneau("127.0.0.1", jumeau) as p:
        with pytest.raises(pp.CommandeInterdite):
            p.commande(2, 0x0C, b"\x00" * 4)
    time.sleep(0.2)
    assert _frames_depuis(debut) == []


class _PanneauMuet:
    """Serveur TCP qui accepte et lit, mais ne repond jamais (scenario 5, sans toucher
    au reply_mode.txt du jumeau partage)."""

    def __init__(self):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(1)
        self.port = self.s.getsockname()[1]
        self.recu = b""
        threading.Thread(target=self._servir, daemon=True).start()

    def _servir(self):
        try:
            c, _ = self.s.accept()
            c.settimeout(5)
            while True:
                m = c.recv(65536)
                if not m:
                    break
                self.recu += m
        except OSError:
            pass


def test_panneau_muet_trois_essais_puis_echec_borne():
    muet = _PanneauMuet()
    t0 = time.monotonic()
    with pn.Panneau("127.0.0.1", muet.port, delais=(0.2, 0.3, 0.3)) as p:
        with pytest.raises(pn.EchecPanneau) as e:
            pn.operation_test(p)
    assert e.value.code == "ne_repond_pas"
    assert time.monotonic() - t0 < 5
    time.sleep(0.2)
    trame = pp.frame(1, 0x02, pp.read_sys_args("CONFIG.SYS"), seq=1, dst=pp.DST) + b"\x00"
    assert muet.recu == trame * 3          # 3 essais, trame IDENTIQUE, octet 00 compris


def test_echeance_globale():
    muet = _PanneauMuet()
    with pn.Panneau("127.0.0.1", muet.port, delais=(5, 5, 5), echeance_s=0.5) as p:
        t0 = time.monotonic()
        with pytest.raises(pn.EchecPanneau) as e:
            pn.operation_test(p)
    assert e.value.code in ("delai_depasse", "ne_repond_pas")
    assert time.monotonic() - t0 < 2


def test_injoignable():
    with pytest.raises(pn.EchecPanneau) as e:
        pn.Panneau("127.0.0.1", _port_libre())
    assert e.value.code == "injoignable"


def test_dns_analyse_reponse_figee():
    ident = 0x1234
    req = pn.construire_requete_dns("infoDW330HC.ddns.net", ident)
    # reponse : meme en-tete (QR=1), question recopiee, une reponse A par pointeur de compression
    rep = struct.pack(">HHHHHH", ident, 0x8180, 1, 1, 0, 0) + req[12:]
    rep += b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes([203, 0, 113, 30])
    assert pn.analyser_reponse_dns(rep, ident) == "203.0.113.30"
    nx = struct.pack(">HHHHHH", ident, 0x8183, 1, 0, 0, 0) + req[12:]
    assert pn.analyser_reponse_dns(nx, ident) is None
    with pytest.raises(ValueError):
        pn.analyser_reponse_dns(rep, ident + 1)


def test_plaque():
    assert pn.normaliser_plaque("DW-330-HC") == "dw330hc"
    assert pn.nom_dns("dw 330 hc") == "infoDW330HC.ddns.net"
