"""Portails / portillons / acces piste (couche 3P) dans cockpit_lieux :
portail par nom, portails autour d'un lieu de la carte, boutons carte et Waze."""
import pytest

import alfred_chat as AC
import alfred_lieux as AL
import alfred_portails as P
from conftest import FakeDb


def _pt(lng, lat):
    return {"type": "Point", "coordinates": [lng, lat]}


def _3p(nom, lat, lng, nature="Portail", acces="", verrou="Clé", **kw):
    return {"type": "Feature", "geometry": _pt(lng, lat),
            "properties": {"Nom": nom, "Nature": nature, "Accès": acces, "Zone": kw.get("zone", "Nord Ouest"),
                           "Extérieur/Intérieur": "Extérieur", "Verrous": verrou,
                           "Largeur (cm)": kw.get("largeur", ""), "Commentaires": kw.get("com", "")}}


# Porte Nord vers 47.9555, 0.2070 ; un poste de secours << PORTE NORD >> loin de la
BASE = dict(
    **{"3p": [{"features": [
        _3p("ACT 2", 47.95326, 0.19627, acces="Actisud", largeur="365"),
        _3p("PON 7", 47.95560, 0.20710, nature="Portillon", acces="Porte Nord"),
        _3p("PON 8", 47.95575, 0.20720, nature="Portillon", acces="Porte Nord", verrou="ILOQ"),
        _3p("P9D 1", 47.95540, 0.20694, acces="P9D", verrou="Chaine + Cadenas"),
        _3p("LOIN 1", 47.96500, 0.22000, acces="Ailleurs"),
        _3p("T52 1", 47.95025, 0.20813, nature="Portillon", acces="T52", com="Accès loges (1er étage)"),
        _3p("WEL 1", 47.95346, 0.20822, nature="Portillon", acces="Welcome"),
        _3p("HOU 1", 47.94760, 0.20970, acces="Houx")]}]},
    portes=[{"features": [
        {"geometry": _pt(0.20700, 47.95550), "properties": {"Name": "PORTE NORD PIETONS"}},
        {"geometry": _pt(0.20705, 47.95565), "properties": {"Name": "PORTE NORD VL"}}]}],
    postesecours=[{"features": [{"geometry": _pt(0.2300, 47.9400), "properties": {"Nom": "PORTE NORD"}}]}],
    tribunes=[{"features": [{"geometry": _pt(0.20810, 47.95020),
                             "properties": {"NOM": "STANDS", "NUMERO 2026": "52"}}]}],
    terrains=[{"features": [{"geometry": {"type": "Polygon", "coordinates": [[
        [0.2060, 47.9550], [0.2080, 47.9550], [0.2080, 47.9560], [0.2060, 47.9560], [0.2060, 47.9550]]]},
        "properties": {"Name": "P9 D"}}]}],
    mp=[{"features": [{"geometry": {"type": "MultiPoint", "coordinates": [[0.24151, 47.92690]]},
                       "properties": {"name": "MP17", "description": "Zone 5"}},
                      {"geometry": {"type": "MultiPoint", "coordinates": [[0.20711, 47.94925]]},
                       "properties": {"name": "MP35b", "description": "Zone 9"}}]}],
    campements_commissaires=[{"features": [
        {"geometry": {"type": "Polygon", "coordinates": [[[0.2200, 47.9233], [0.2202, 47.9233],
                                                          [0.2202, 47.9235], [0.2200, 47.9233]]]},
         "properties": {"Name": "CC25", "description": "Intérieur Indianapolis"}},
        {"geometry": {"type": "Polygon", "coordinates": [[[0.2116, 47.9470], [0.2118, 47.9470],
                                                          [0.2118, 47.9472], [0.2116, 47.9470]]]},
         "properties": {"Name": "C11", "description": "Entrée par la Porte sud"}},
        {"geometry": {"type": "Polygon", "coordinates": [[[0.2321, 47.9440], [0.2323, 47.9440],
                                                          [0.2323, 47.9442], [0.2321, 47.9440]]]},
         "properties": {"Name": "CC11", "description": "1ère chicane"}}]}],
    giratoires=[{"features": [{"geometry": {"type": "MultiPoint", "coordinates": [[0.18587, 47.92936]]},
                               "properties": {"name": "Giratoire de l'oeuf"}}]}],
    passerelles=[{"features": [{"geometry": _pt(0.20972, 47.94758),
                                "properties": {"Nom": "HOUX", "type": "permanente", "PMR": "non",
                                               "fermeture": "iloq"}}]}],
    Virages=[{"features": [{"geometry": _pt(0.21544, 47.95893),
                            "properties": {"NAME": "V4", "DESCRIPTION": "La chapelle"}}]}],
    batiment=[{"features": [
        {"geometry": {"type": "Polygon", "coordinates": [[[0.2084, 47.9532], [0.2086, 47.9532],
                                                          [0.2086, 47.9534], [0.2084, 47.9532]]]},
         "properties": {"name": "Welcome Center", "building": "yes"}},
        {"geometry": {"type": "Polygon", "coordinates": [[[0.2300, 47.9300], [0.2302, 47.9300],
                                                          [0.2302, 47.9302], [0.2300, 47.9300]]]},
         "properties": {"name": "Maison Mme DUPONT", "building": "yes"}}]}],
    grid_ref=[{"grid_ref": "Z17", "latitude": 47.95320, "longitude": 0.19630},
              {"grid_ref": "T52", "latitude": 47.9000, "longitude": 0.1000}],
)


@pytest.fixture
def db():
    P._CACHE.clear()
    return FakeDb(**{k: list(v) for k, v in BASE.items()})


def test_portail_par_nom_fiche_carre_et_boutons(db):
    for nom in ("ACT 2", "act2", "Act-2"):
        r = P.t_portails(db, {"portail": nom})
        assert r["vue"] == "portail" and r["portail"]["nom"] == "ACT 2"
    assert r["resume"].startswith("ACT 2 : portail, accès Actisud, zone Nord Ouest, extérieur, verrou : Clé, "
                                  "largeur 365 cm.")
    assert "carré Z17" in r["resume"]
    # Les deux boutons passent la liste blanche du chat
    assert [l["url"] for l in AC._liens_surs(r["liens"])] == [
        "/#carte=47.953260,0.196270,ACT%202", "https://ul.waze.com/ul?ll=47.953260%2C0.196270&navigate=yes"]


def test_nom_incomplet_propose_les_noms_proches(db):
    r = P.t_portails(db, {"portail": "PON"})
    assert r["vue"] == "portail_proches" and r["candidats"] == ["PON 7", "PON 8"]
    r = P.t_portails(db, {"portail": "T5"})
    assert r["vue"] == "portail_proches" and r["candidats"] == ["T52 1"]
    # Un nom inconnu n'est jamais remplace par un autre portail
    assert P.t_portails(db, {"portail": "PON 9"})["vue"] == "lieu_inconnu"


def test_autour_de_la_porte_nord_toutes_ses_entrees_pas_le_poste_de_secours(db):
    r = P.t_portails(db, {"autour": "porte nord"})
    assert r["vue"] == "autour"
    assert r["lieu"].startswith("Porte Nord (2 points")
    noms = [p["nom"] for p in r["portails"]]
    assert set(noms) == {"PON 7", "PON 8", "P9D 1"} and "LOIN 1" not in noms
    d = [p["distance_m"] for p in r["portails"]]
    assert d == sorted(d)
    assert AC._liens_surs(r["liens"])[0]["url"].startswith("/#carte=")


def test_nature_et_rayon(db):
    r = P.t_portails(db, {"portail": "portillons", "autour": "porte nord"})
    assert {p["nom"] for p in r["portails"]} == {"PON 7", "PON 8"}
    assert r["resume"].startswith("Portillons à moins de 150 m")
    r = P.t_portails(db, {"autour": "porte nord", "rayon": 5})
    assert r["rayon_m"] == 30 and {p["nom"] for p in r["portails"]} == {"PON 7", "PON 8", "P9D 1"}  # plancher 30 m
    r = P.t_portails(db, {"autour": "ACT 2", "rayon": 30})
    assert [p["nom"] for p in r["portails"]] == ["ACT 2"]


def test_t52_est_la_tribune_et_le_carre_seulement_s_il_est_dit(db):
    r = P.t_portails(db, {"autour": "T52"})
    assert r["lieu"].startswith("Tribune 52") and [p["nom"] for p in r["portails"]][0] == "T52 1"
    r = P.t_portails(db, {"autour": "carré T52"})
    assert r["lieu"] == "carré T52"


def test_dans_une_aire_distance_nulle_et_rayon_reduit(db):
    r = P.t_portails(db, {"autour": "P9D"})
    assert r["rayon_m"] == P.RAYON_SURFACE
    assert {p["nom"]: p["distance_m"] for p in r["portails"]}["P9D 1"] == 0


def test_repli_sur_le_champ_acces(db):
    r = P.t_portails(db, {"autour": "Actisud"})
    assert r["vue"] == "acces" and [p["nom"] for p in r["portails"]] == ["ACT 2"]
    # << Welcome >> est sur la carte (batiment Welcome Center) : par distance
    r = P.t_portails(db, {"autour": "Welcome"})
    assert r["vue"] == "autour" and r["lieu"] == "Welcome Center" and "WEL 1" in [p["nom"] for p in r["portails"]]
    # << portail du Houx >> : un lieu, pas un nom de portail
    r = P.t_portails(db, {"portail": "portail du Houx"})
    assert r["vue"] in ("acces", "autour") and "HOU 1" in [p["nom"] for p in r["portails"]]


def test_lieu_inconnu_jamais_invente(db):
    r = P.t_portails(db, {"autour": "nulle part"})
    assert r["vue"] == "lieu_inconnu" and not r.get("liens")


def test_cockpit_lieux_aiguille_vers_les_portails(db):
    r = AL.t_lieux(db, {"portail": "ACT 2"}, {})
    assert r["vue"] == "portail"


def test_situer_les_lieux_de_la_carte(db):
    cas = {"poste commissaire MP17": ("Poste commissaire MP17", "Zone 5"),
           "MP 35 bis": ("Poste commissaire MP35b", "Zone 9"),
           "campement CC25": ("Campement commissaires CC25", "Intérieur Indianapolis"),
           "giratoire de l'oeuf": ("Giratoire de l'oeuf", None),
           "rond point de l'oeuf": ("Giratoire de l'oeuf", None),
           "passerelle du houx": ("Passerelle Houx", "fermeture : iloq"),
           "virage V4": ("V4 La chapelle", None),
           "Welcome Center": ("Welcome Center", None)}
    for texte, (nom, info) in cas.items():
        r = P.fiche_lieu(db, texte)
        assert r and r["vue"] == "lieu_carte" and r["lieux"][0]["nom"] == nom, (texte, r)
        if info:
            assert info in r["resume"]
        assert AC._liens_surs(r["liens"])[0]["url"].startswith("/#carte=")
    # C11 et CC11 sont deux campements distincts
    assert [x["nom"] for x in P.fiche_lieu(db, "C11")["lieux"]] == ["Campement commissaires C11"]
    assert P.fiche_lieu(db, "nulle part") is None


def test_batiment_au_nom_d_une_personne_ignore(db):
    assert not any(l["type"] == "bâtiment" and "DUPONT" in l["nom"] for l in P.repertoire(db))
    r = P.fiche_lieu(db, "Maison Mme DUPONT")
    assert r is None or "DUPONT" not in r["resume"]


def test_cockpit_lieux_situer_et_repli_hors_parametrage(db):
    r = AL.t_lieux(db, {"situer": "MP17"}, {})
    assert r["vue"] == "lieu_carte"
    db["parametrages"].docs.append({"event": "24H AUTOS", "year": "2026", "data": {}})
    r = AL.t_lieux(db, {"nom": "campement CC25"}, {"event": "24H AUTOS", "year": "2026"})
    assert r["vue"] == "lieu_carte" and r["resume"].startswith("Pas d'horaires pour « campement CC25 »")
    assert "CC25" in r["resume"] and r["liens"]


def test_liste_blanche_carte():
    ok = AC._liens_surs([{"url": "/#carte=47.95,0.2,ACT%202;47.96,0.21,PON%207", "libelle": "Voir"},
                         {"url": "/#carte=47.95,0.2,<script>", "libelle": "x"},
                         {"url": "/#carte=javascript:alert(1)", "libelle": "x"},
                         {"url": "//evil.example/#carte=47.95,0.2,A", "libelle": "x"}])
    assert [l["url"] for l in ok] == ["/#carte=47.95,0.2,ACT%202;47.96,0.21,PON%207"]
