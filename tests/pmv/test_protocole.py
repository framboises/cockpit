"""Recette A : les octets produits par pmv_protocole == ceux valides sur remorque (docs/pmv/vecteurs)."""
import os
import sys

import pytest

RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, RACINE)

import pmv_protocole as pp  # noqa: E402

VECTEURS = os.path.join(RACINE, "docs", "pmv", "vecteurs")
DONNEES = os.path.join(RACINE, "docs", "pmv", "banc-essai", "donnees-remorque")
IMAGES = ["blanc", "noir", "degrade", "mire-couleurs"]


def _lire(nom, mode="rb"):
    with open(os.path.join(VECTEURS, nom), mode) as f:
        return f.read()


def _pixels(nom):
    return pp.pixels_from_image_bytes(_lire(nom + ".png"))


def _trames_hex(nom):
    """Trames du fichier .hex, OCTET 00 FINAL COMPRIS."""
    lignes = _lire(nom + ".trames-envoi.hex", "r").splitlines()
    return [bytes.fromhex(l.strip()) for l in lignes if l.strip() and not l.startswith("#")]


@pytest.mark.parametrize("nom", IMAGES)
def test_bmp565(nom):
    assert pp.bmp565(_pixels(nom)) == _lire(nom + ".bmp565.bmp")


@pytest.mark.parametrize("nom", IMAGES)
def test_fichier_panneau(nom):
    assert pp.fichier_message(_pixels(nom)) == _lire(nom + ".fichier-panneau.bin")


@pytest.mark.parametrize("nom", IMAGES)
def test_trames_envoi(nom):
    attendu = _trames_hex(nom)
    emis = [t + b"\x00" for t in pp.trames_envoi(_pixels(nom))]
    assert len(emis) == len(attendu)
    assert emis == attendu


def test_temoin_octet_mute_detecte():
    px = _pixels("mire-couleurs")
    r, g, b = px[10][10]
    px[10][10] = (r ^ 0x80, g, b)
    assert pp.bmp565(px) != _lire("mire-couleurs.bmp565.bmp")
    assert [t + b"\x00" for t in pp.trames_envoi(px)] != _trames_hex("mire-couleurs")


def test_sens_image_damier_jaune_en_haut_a_gauche():
    px = _pixels("mire-couleurs")
    # le damier jaune occupe le coin 16 x 16 en haut a gauche (une case sur deux)
    assert (255, 255, 0) in [px[y][x] for y in range(1, 16) for x in range(1, 16)]
    assert (255, 255, 0) not in [px[y][x] for y in range(48, 63) for x in range(1, 16)]


@pytest.mark.parametrize("cmd,sub", [(2, 0x0C), (1, 0x10), (4, 0x03), (4, 0x04), (2, 0x09), (2, 0x0D)])
def test_liste_blanche_refuse(cmd, sub):
    with pytest.raises(pp.CommandeInterdite):
        pp.frame(cmd, sub)
    with pytest.raises(pp.CommandeInterdite):
        pp.verifier_commande(cmd, sub)


def test_liste_blanche_accepte():
    for cmd, sub in [(1, 0x02), (2, 0x08), (2, 0x02)]:
        pp.verifier_commande(cmd, sub)


def test_config_remorque_96x64():
    with open(os.path.join(DONNEES, "CONFIG.SYS"), "rb") as f:
        assert pp.parse_config_dims(f.read()) == (96, 64)
    assert pp.parse_config_dims(b"\x00\x00") is None


def test_sequent_remorque():
    with open(os.path.join(DONNEES, "SEQUENT.SYS"), "rb") as f:
        assert pp.parse_sequent(f.read()) == ["temp.Nmg"]


def test_sequent_notre_liste():
    contenu = pp.sequent_sys([("D", "T", pp.NOM_MESSAGE)])
    assert len(contenu) == 44
    assert pp.parse_sequent(contenu) == [pp.NOM_MESSAGE]
    with pytest.raises(ValueError):
        pp.parse_sequent(b"XX\x04\x00")


def test_check_reply():
    req = pp.frame(1, 0x02, pp.read_sys_args("CONFIG.SYS"), seq=7, dst=pp.DST)
    rep = bytearray(req[:16])
    rep[1] = 0xA8
    rep[4:6] = b"\x00\x00"
    rep[14] = 0
    rep[15] = 0
    rep[2:4] = (sum(rep[4:16]) & 0xFFFF).to_bytes(2, "little")
    assert pp.check_reply(bytes(rep), 7, 1, 0x02) == 0
    assert pp.check_reply(bytes(rep), 8, 1, 0x02) == 2      # autre sequence : a ignorer
    mauvais = bytearray(rep)
    mauvais[2] ^= 1
    assert pp.check_reply(bytes(mauvais), 7, 1, 0x02) == 2  # somme fausse : a ignorer


def test_image_mauvaise_taille_refusee():
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (64, 32)).save(buf, format="PNG")
    with pytest.raises(pp.ImageInvalide):
        pp.pixels_from_image_bytes(buf.getvalue())
    with pytest.raises(pp.ImageInvalide):
        pp.pixels_from_image_bytes(b"pas une image")


def test_aller_retour_rgb_png():
    px = _pixels("degrade")
    assert pp.pixels_from_rgb(pp.rgb_from_pixels(px)) == px
    assert pp.pixels_from_image_bytes(pp.png_from_pixels(px)) == px
