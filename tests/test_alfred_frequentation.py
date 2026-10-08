"""cockpit_frequentation (alfred_frequentation.py) et evenements (alfred_evenements.py).

Les editions sont celles de la base reelle (24H CAMIONS 2025/2026, SUPERBIKE
2026, 24H MOTOS 2026, lues le 08/10/2026 par scan_frequentation.load_editions
en LIVE_FIRST), recopiees ici sous la forme compacte de l'outil pour que le
test ne depende ni de la base ni du rejeu des archives.
"""

from datetime import datetime

import alfred_evenements as AE
import alfred_frequentation as F
import alfred_tools
from conftest import FakeDb  # noqa: E402

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=F.TZ_PARIS)


def _j(date, offset, pic=None, heure=None, entrees=None):
    return {"date": date, "offset": offset, "pic": pic, "heure": heure, "entrees": entrees,
            "mesure": pic is not None, "motif": None if pic is not None else "aucune_mesure"}


C2026 = {"annee": 2026, "course": "2026-09-26", "source": "live_controle", "portes": 16,
         "solde_initial": None, "remise_a_zero": True, "jours": [
             _j("2026-09-21", -5), _j("2026-09-22", -4),
             _j("2026-09-23", -3, 390, "15:36", 1206), _j("2026-09-24", -2, 897, "16:15", 2440),
             _j("2026-09-25", -1, 4362, "22:42", 9257), _j("2026-09-26", 0, 49975, "19:15", 74045),
             _j("2026-09-27", 1, 37501, "13:06", 40947), _j("2026-09-28", 2)]}
C2025 = {"annee": 2025, "course": "2025-09-20", "source": "scan_import", "portes": 19,
         "solde_initial": None, "remise_a_zero": False, "jours": [
             _j("2025-09-15", -5, 53, "15:15", 247), _j("2025-09-16", -4, 156, "15:00", 481),
             _j("2025-09-17", -3, 242, "15:45", 736), _j("2025-09-18", -2, 746, "18:30", 1634),
             _j("2025-09-19", -1, 4852, "23:45", 7443), _j("2025-09-20", 0, 52520, "17:15", 73532),
             _j("2025-09-21", 1, 39547, "12:30", 40822)]}
C2024 = {"annee": 2024, "course": "2024-09-28", "source": "scan_import", "portes": 12,
         "solde_initial": None, "remise_a_zero": False, "jours": [
             _j("2024-09-27", -1, 4989, "23:45", 8204), _j("2024-09-28", 0, 46456, "17:00", 69681),
             _j("2024-09-29", 1, 35691, "12:30", 37736)]}
SBK2026 = {"annee": 2026, "course": "2026-04-04", "source": "live_controle", "portes": 6,
           "solde_initial": None, "remise_a_zero": True, "jours": [
               _j("2026-04-04", 0, 3, "21:00", 12), _j("2026-04-05", 1, 4265, "15:15", 5000)]}
SBK2025 = {"annee": 2025, "course": "2025-03-29", "source": "collecte_temps_reel", "portes": 4,
           "solde_initial": None, "remise_a_zero": False, "jours": [
               _j("2025-03-29", 0, 3982, "17:00", 6000), _j("2025-03-30", 1, 5829, "16:00", 7000)]}
M2026 = {"annee": 2026, "course": "2026-04-18", "source": "live_controle", "portes": 19,
         "solde_initial": 8916, "remise_a_zero": False, "jours": [
             _j("2026-04-17", -1, 28006, "21:50", 41437), _j("2026-04-18", 0, 48322, "15:05", 68580)]}

EDITIONS = {"24H CAMIONS": [C2026, C2025, C2024], "SUPERBIKE": [SBK2026, SBK2025],
            "24H MOTOS": [M2026]}


def _db(contextes=()):
    return FakeDb(
        evenement=[{"nom": "24H AUTOS", "short": "24HA"},
                   {"nom": "24H CAMIONS", "short": "24HC"}, {"nom": "24H MOTOS", "short": "24HM"},
                   {"nom": "SUPERBIKE", "short": "SBK"}, {"nom": "LE MANS CLASSIC", "short": "LMC"},
                   {"nom": "LE MANS FC - LORIENT", "short": "LMFCFCLORIENT"},
                   {"nom": "LE MANS FC - LYON", "short": "LMFCOL"},
                   {"nom": "GP EXPLORER", "short": "GPE"}, {"nom": "GPF", "short": "GPF"},
                   {"nom": "IAME"}],
        parametrages=[{"event": "24H CAMIONS", "year": "2026"}],
        alfred_evenement_contexte=list(contextes))


def _call(monkeypatch, args, ctx=None, direct=None, contextes=()):
    monkeypatch.setattr(F, "toutes_editions",
                        lambda db, ev, annee: [dict(e) for e in EDITIONS.get(ev, [])])
    monkeypatch.setattr(F, "direct", lambda db, ev, an, now_utc=None: direct)
    monkeypatch.setattr(F, "resoudre_evenement", _resolveur(F.resoudre_evenement))
    return F.t_frequentation(_db(contextes), args, ctx or {}, now=NOW)


def _resolveur(vrai):
    # Sans evenement cite ni contexte : l'epreuve << en cours >> est IAME
    # (aucune mesure), comme le 08/10/2026.
    def r(db, args, ctx, now):
        if not args.get("evenement") and not (ctx or {}).get("event"):
            return "IAME", 2026, None, []
        return vrai(db, args, ctx, now)
    return r


# ---------------------------------------------------------------------------
# Evenements : surnoms, sigles, contexte
# ---------------------------------------------------------------------------

def test_resoudre_nom_mots_sigles_et_surnoms():
    db = _db([{"event": "24H CAMIONS", "surnoms": ["TC", "les gros"]}])
    assert AE.resoudre_nom(db, "les camions")[0] == "24H CAMIONS"
    assert AE.resoudre_nom(db, "24 heures camions")[0] == "24H CAMIONS"
    assert AE.resoudre_nom(db, "sbk")[0] == "SUPERBIKE"
    assert AE.resoudre_nom(db, "TC")[0] == "24H CAMIONS"
    assert AE.resoudre_nom(db, "les gros de l'an dernier")[0] == "24H CAMIONS"
    assert AE.resoudre_nom(db, "motos")[0] == "24H MOTOS"
    assert AE.resoudre_nom(db, "la classic")[0] == "LE MANS CLASSIC"


def test_resoudre_nom_forme_la_plus_precise_gagne():
    db = _db([{"event": "24H AUTOS", "surnoms": ["les 24 heures"]}])
    assert AE.resoudre_nom(db, "les 24h motos de l'an dernier")[0] == "24H MOTOS"
    assert AE.resoudre_nom(db, "pic des 24h camions")[0] == "24H CAMIONS"
    assert AE.resoudre_nom(db, "monde aux 24 heures hier")[0] == "24H AUTOS"


def test_resoudre_nom_ambigu_rend_les_candidats():
    ev, cands = AE.resoudre_nom(_db(), "le mans")
    assert ev is None
    assert "LE MANS CLASSIC" in cands and "LE MANS FC - LYON" in cands


def test_contexte_porte_la_consigne_et_la_note_de_l_edition():
    db = _db([{"event": "24H CAMIONS", "description": "Course de camions sur 24 h.",
               "surnoms": ["TC"], "editions": {"2026": "Nouveau village"}}])
    c = AE.contexte(db, "24H CAMIONS", "2026")
    assert c["description"].startswith("Course")
    assert c["note_edition_2026"] == "Nouveau village"
    assert "aucun horaire" in c["consigne"]
    assert AE.contexte(db, "24H MOTOS", "2026") is None


def test_nettoyer_et_conflit_de_surnom():
    doc, err = AE.nettoyer({"event": "24H MOTOS", "surnoms": "les motos, LES MOTOS, sbk",
                            "editions": {"2026": "x", "abcd": "y"}, "description": "  a  "})
    assert err is None
    assert doc["surnoms"] == ["les motos", "sbk"]
    assert doc["editions"] == {"2026": "x"}
    assert AE.conflits_surnoms(_db(), doc) == ["sbk (SUPERBIKE)"]
    assert AE.nettoyer({})[1] == "evenement_requis"


# ---------------------------------------------------------------------------
# Parametres en langage operateur
# ---------------------------------------------------------------------------

def test_annees_relatives():
    assert F.annees_demandees("l'an dernier", 2026) == [2025]
    assert F.annees_demandees("N-1", 2026) == [2025]
    assert F.annees_demandees("il y a deux ans", 2026) == [2024]
    assert F.annees_demandees("2023 et 2024", 2026) == [2023, 2024]
    assert F.annees_demandees("les éditions précédentes", 2026) == []


def test_nombre_editions():
    assert F.nombre_editions("les 3 dernières éditions") == 3
    assert F.nombre_editions("toutes") == F.EDITIONS_MAX
    assert F.nombre_editions("les éditions précédentes") == F.COMPARAISON_DEFAUT
    assert F.nombre_editions("2024") is None


def test_offset_demande():
    assert F.offset_demande("samedi", C2026) == 0
    assert F.offset_demande("vendredi", C2026) == -1
    assert F.offset_demande("jour de course", C2026) == 0
    assert F.offset_demande("la veille", C2026) == -1
    assert F.offset_demande("J+1", C2026) == 1
    assert F.offset_demande("2026-09-24", C2026) == -2
    assert F.offset_demande("2026-10-01", C2026) == "hors"


# ---------------------------------------------------------------------------
# Outil
# ---------------------------------------------------------------------------

def test_edition_par_surnom_et_jours_sans_mesure(monkeypatch):
    r = _call(monkeypatch, {"evenement": "les camions"})
    assert r["vue"] == "edition" and r["resolu"]["annee"] == 2026
    s = r["resume"]
    assert "Pic de l'édition : 49 975 présents le samedi 26 septembre 2026 à 19:15" in s
    assert "vendredi 25 septembre, J-1 (veille) : pic 4 362 présents à 22:42, 9 257 entrées" in s
    assert "Jours sans mesure (ce n'est pas une fréquentation nulle) : lundi 21 septembre" in s


def test_annee_relative_l_an_dernier(monkeypatch):
    r = _call(monkeypatch, {"evenement": "24h camions", "annee": "l'an dernier"})
    assert r["resolu"]["annee"] == 2025
    assert "52 520 présents le samedi 20 septembre 2025 à 17:15" in r["resume"]


def test_comparaison_alignee_sur_le_jour_de_course(monkeypatch):
    r = _call(monkeypatch, {"evenement": "camions", "comparer_avec": "l'an dernier"})
    s = r["resume"]
    assert r["vue"] == "comparaison" and r["resolu"]["annees"] == [2026, 2025]
    assert "2025 : 52 520 présents, samedi 20 septembre à 17:15 (2026 : -4,8 % par rapport à 2025)" in s
    assert ("samedi, J0 (jour de course) : 2026 49 975 à 19:15 | 2025 52 520 à 17:15 ; "
            "2026 : -4,8 % vs 2025") in s
    # Sources et portes differentes : les deux reserves sont ecrites
    assert "sources de mesure différentes" in s
    assert "seul le pic de présents se compare" in s


def test_comparaison_par_nombre_et_jour(monkeypatch):
    r = _call(monkeypatch, {"evenement": "camions", "comparer_avec": "les 3 dernières éditions",
                            "jour": "samedi"})
    s = r["resume"]
    assert r["resolu"]["annees"] == [2026, 2025, 2024]
    assert "Par jour" in s and s.count("J0 (jour de course) :") == 1
    assert "vendredi" not in s.split("Par jour")[1].split("\n")[1]


def test_mesure_tres_partielle_sans_pourcentage(monkeypatch):
    r = _call(monkeypatch, {"evenement": "sbk", "comparer_avec": "2025"})
    ligne = next(l for l in r["resume"].split("\n") if "J0" in l)
    assert "2026 3 à 21:00 (mesure très partielle)" in ligne
    assert "%" not in ligne


def test_reserve_compteur_non_remis_a_zero(monkeypatch):
    r = _call(monkeypatch, {"evenement": "motos", "annee": "2026"})
    assert "solde initial 8 916" in r["resume"]


def test_contexte_operateur_et_annee_citee_absente(monkeypatch):
    r = _call(monkeypatch, {"annee": "2019"}, ctx={"event": "24H CAMIONS", "year": 2026})
    assert r["trouve"] is False
    assert "Éditions disponibles : 2026, 2025, 2024" in r["resume"]


def test_sans_evenement_ni_mesure_prend_la_derniere_epreuve(monkeypatch):
    monkeypatch.setattr(F, "derniere_mesuree", lambda db, now, exclure=None: ("24H CAMIONS", 2026))
    r = _call(monkeypatch, {})
    assert r["resume"].startswith("IAME n'a aucune mesure de fréquentation : chiffres de la "
                                  "dernière épreuve mesurée, 24H CAMIONS 2026.")
    assert r["evenement"] == "24H CAMIONS"


def test_evenement_ambigu(monkeypatch):
    r = _call(monkeypatch, {"evenement": "le mans"})
    assert r["trouve"] is False and "Demander lequel" in r["resume"]


def test_direct_avec_n1_a_la_meme_heure(monkeypatch):
    d = {"presents": 31200, "releve": "14:30", "pic_jour": 32010, "pic_jour_heure": "14:12",
         "n1": {"annee": 2025, "date": "2025-09-20", "meme_heure": 30000, "pic_jour": 52520}}
    r = _call(monkeypatch, {}, ctx={"event": "24H CAMIONS", "year": 2026}, direct=d)
    s = r["resume"]
    assert r["vue"] == "direct"
    assert "en direct (relevé de 14:30) : 31 200 personnes présentes sur site" in s
    assert "Pic du jour : 32 010 à 14:12" in s
    assert "à la même heure : 30 000 présents (écart +4,0 %)" in s
    assert "Jours précédents de l'édition" in s


def test_collecteur_arrete(monkeypatch):
    r = _call(monkeypatch, {}, ctx={"event": "24H CAMIONS", "year": 2026},
              direct={"collecteur_arrete": True})
    assert "collecteur probablement arrêté" in r["resume"]


def test_manifeste_fusionne_presents(monkeypatch):
    noms = [t["function"]["name"] for t in alfred_tools.manifest()]
    assert "cockpit_frequentation" in noms
    assert "cockpit_presents" not in noms
    assert "cockpit_presents" in alfred_tools.TOOLS   # toujours executable


def test_routes_contexte_enregistrer_puis_lire(monkeypatch):
    """Routes admin de la page Configuration, appelees sous le decorateur de role."""
    from flask import Flask
    import alfred_chat
    db = _db()
    monkeypatch.setattr(alfred_chat, "_db", lambda: db)
    app = Flask(__name__)
    enregistrer = alfred_chat.contexte_enregistrer.__wrapped__
    lister = alfred_chat.contextes_liste.__wrapped__
    with app.test_request_context(json={"event": "24H CAMIONS", "description": "Course de camions.",
                                        "surnoms": "les camions, TC", "editions": {"2026": "x"}}):
        r = enregistrer()
        assert r.get_json()["ok"] is True
    with app.test_request_context(json={"event": "24H MOTOS", "surnoms": "TC"}):
        r, code = enregistrer()
        assert code == 409 and r.get_json()["conflits"] == ["TC (24H CAMIONS)"]
    with app.test_request_context(json={"event": "INCONNU"}):
        assert enregistrer()[1] == 400
    with app.test_request_context():
        evs = lister().get_json()["evenements"]
    assert evs[0]["event"] == "24H CAMIONS" and evs[0]["surnoms"] == ["les camions", "TC"]
    assert evs[0]["annees"] == ["2026"]
    assert AE.resoudre_nom(db, "TC")[0] == "24H CAMIONS"


def test_resultat_borne(monkeypatch):
    import json
    r = _call(monkeypatch, {"evenement": "camions", "comparer_avec": "toutes"})
    assert len(json.dumps(alfred_tools._borner(r), ensure_ascii=False)) <= alfred_tools.RESULT_MAX_CHARS
