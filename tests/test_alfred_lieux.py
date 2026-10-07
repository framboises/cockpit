"""cockpit_lieux (alfred_lieux.py) : regles de la VM, corpus porte Nord, defauts corriges.

Les horaires sont ceux du parametrage reel 24H CAMIONS 2026 (PORTE NORD
PIETONS, lus le 07/10/2026), recopies ici pour que le test ne depende pas
de la base.
"""

from datetime import datetime

import alfred_lieux as L
import alfred_tools
from conftest import FakeDb  # noqa: E402

NOW = datetime(2026, 9, 25, 10, 0)
CTX = {"event": "24H CAMIONS", "year": "2026"}


def _j(date, orga=None, public=None, vip=None):
    def b(x):
        if x is None:
            return {"open": None, "close": None, "closed": True, "is24h": False}
        if x == "24h":
            return {"open": "00:00", "close": "23:59", "closed": False, "is24h": True}
        return {"open": x[0], "close": x[1], "closed": False, "is24h": False}
    return {"date": date, "dayControl": "controle", "organisation": b(orga),
            "public": b(public), "vip": b(vip)}


NORD_PIETONS = {"id": "a1", "name": "PORTE NORD PIETONS",
                "access": {"orga": True, "public": True, "vip": True},
                "dates": [_j("2026-09-21", ("06:00", "22:00")), _j("2026-09-22", ("06:00", "22:00")),
                          _j("2026-09-23", ("06:00", "22:00")), _j("2026-09-24", ("06:00", "00:00")),
                          _j("2026-09-25", ("06:00", "23:59")), _j("2026-09-26", "24h", ("07:00", "23:59"),
                                                                    ("07:00", "23:59")),
                          _j("2026-09-27", ("00:00", "21:00"), ("00:00", "19:00"), ("00:00", "19:00"))]}
NORD_VL = {"id": "a2", "name": "PORTE NORD VL", "access": {"orga": True, "public": False, "vip": False},
           "dates": [_j("2026-09-25", ("06:00", "22:00"), ("07:00", "20:00"))]}
EST = {"id": "a3", "name": "PORTE EST", "access": {"orga": False, "public": True},
       "dates": [_j("2026-09-26", ("06:00", "20:00"), ("07:00", "20:00"))]}
CHINETTI = {"id": "p1", "name": "CHINETTI", "access": {"orga": True, "public": True},
            "capacite_theorique": 1200,
            "dates": [_j("2026-09-26", ("06:00", "23:59"), ("06:00", "23:59")),
                      _j("2026-09-27", ("00:00", "20:00"), ("00:00", "20:00"))]}
TRIBUNE = {"id": "t1", "name": "SINGHER", "access": {"public": True, "vip": True},
           "dates": [{"date": "2026-09-26", "public": {"open": "08:30", "close": "22:30",
                                                       "closed": False, "open24h": False},
                      "vip": {"open": "08:30", "close": "22:30", "closed": False}}]}


def _db():
    data = {
        "portesHoraires": {"a1": NORD_PIETONS, "a2": NORD_VL, "a3": EST},
        "parkingsHoraires": [CHINETTI],
        "tribunesHoraires": {"t1": TRIBUNE},
        "hospiHoraires": [{"id": "h1", "name": "CLUB DES PILOTES", "access": {"vip": True},
                           "information": "Acces badge"}],
        "globalHoraires": {"centreMedical": [
            {"date": "2026-09-25", "openTime": "12:00", "closeTime": "23:59"},
            {"date": "2026-09-26", "openTime": "00:00", "closeTime": "23:59", "is24h": True},
            {"date": "2026-09-27", "openTime": "00:00", "closeTime": "19:30"}]},
    }
    return FakeDb(parametrages=[{"event": "24H CAMIONS", "year": "2026", "data": data}])


def lieux(**args):
    return L.t_lieux(_db(), args, CTX, now=NOW)


# --- Algorithme de fusion -------------------------------------------------

def test_orga_continu_du_vendredi_06h_au_dimanche_21h():
    r = lieux(nom="Porte Nord Pietons", public="organisation")
    assert ("en continu du vendredi 25 septembre 2026 à 06:00 au dimanche 27 septembre 2026 "
            "à 21:00") in r["resume"]
    assert "jeudi 24 septembre 2026 de 06:00 à minuit (00:00)" in r["resume"]
    # Corpus : jamais 05:00, jamais "a partir de 00:00"
    assert "05:00" not in r["resume"] and "à partir de 00:00" not in r["resume"]


def test_public_samedi_07h_au_dimanche_19h():
    r = lieux(nom="porte nord pietons", public="public")
    assert ("en continu du samedi 26 septembre 2026 à 07:00 au dimanche 27 septembre 2026 "
            "à 19:00") in r["resume"]


def test_fermeture_a_00h_suivie_d_une_ouverture_a_06h_reste_deux_plages():
    plages = L.plages_item(NORD_PIETONS, ["organisation"])["organisation"]["plages"]
    assert (datetime(2026, 9, 24, 6), datetime(2026, 9, 25)) in plages
    assert (datetime(2026, 9, 25, 6), datetime(2026, 9, 27, 21)) in plages


def test_fermeture_le_lendemain():
    d, f = L._intervalle(datetime(2026, 9, 26), {"open": "07:00", "close": "02:00"})
    assert f == datetime(2026, 9, 27, 2)


# --- Publics --------------------------------------------------------------

def test_accredites_est_l_organisation():
    assert L.public_canonique("accrédités") == "organisation"
    assert L.public_canonique("Staff") == "organisation"
    assert L.public_canonique("spectateurs") == "public"


def test_sans_public_les_trois_sont_donnes():
    r = lieux(nom="porte nord pietons")
    assert "organisation (accrédités) :" in r["resume"]
    assert "| public :" in r["resume"] and "VIP :" in r["resume"]


def test_prod_accredites_en_texte_libre_donne_l_orga_pas_le_public():
    # Constate le 07/10/2026 : le modele, force de choisir dans une enum,
    # avait passe public="public" pour "accredites".
    r = lieux(nom="porte nord pietons", public="accrédités")
    assert r["public_demande"] == "organisation"
    assert "organisation (accrédités) : lundi 21 septembre 2026" in r["resume"]
    assert "07:00" not in r["resume"]


def test_public_inconnu_donne_les_trois_avec_une_note():
    r = lieux(nom="porte nord pietons", public="pompiers")
    assert "note_public" in r and "| VIP :" in r["resume"]


def test_publics_separes_par_barre_verticale():
    r = lieux(nom="porte nord pietons")
    assert " | public :" in r["resume"]


def test_defaut_vm_1_access_false_ferme_sur_toutes_les_dates():
    r = lieux(nom="porte est")
    assert "fermé à l'organisation" in r["resume"]
    r = lieux(nom="porte nord vl", public="public")
    assert "fermé au public" in r["resume"] and "07:00" not in r["resume"]


def test_tribune_open24h_et_public_absent_non_concerne():
    r = lieux(nom="singher")
    assert "public : samedi 26 septembre 2026 de 08:30 à 22:30" in r["resume"]
    assert "organisation" not in r["resume"]


# --- Noms -----------------------------------------------------------------

def test_porte_nord_ambigu_renvoie_les_deux_variantes():
    r = lieux(nom="porte nord")
    noms = [x["nom"] for x in r["lieux"]]
    assert noms == ["PORTE NORD PIETONS", "PORTE NORD VL"]


def test_defaut_vm_2_faute_de_frappe_garde_l_ambiguite():
    r = lieux(nom="porte nrod")
    assert {x["nom"] for x in r["lieux"]} == {"PORTE NORD PIETONS", "PORTE NORD VL"}


def test_mot_de_type_hors_nom_filtre_la_categorie():
    r = lieux(nom="parking chinetti")
    assert r["lieux"][0]["nom"] == "CHINETTI"
    assert r["lieux"][0]["infos"]["capacite_theorique"] == 1200


def test_lieu_inconnu_propose_les_lieux_du_meme_type():
    r = lieux(nom="porte du tertre")
    assert r["trouve"] is False
    assert "PORTE NORD PIETONS" in r["lieux_du_meme_type"]


# --- Jour, maintenant, services --------------------------------------------

def test_jour_vendredi_donne_la_plage_continue_qui_le_couvre():
    r = lieux(nom="porte nord pietons", public="accrédités", jour="vendredi")
    assert "le vendredi 25 septembre 2026" in r["resume"]
    assert "de 06:00 à minuit (00:00)" in r["resume"]
    assert "au dimanche 27 septembre 2026 à 21:00" in r["resume"]


def test_maintenant():
    r = lieux(nom="porte nord pietons", maintenant=True)
    assert ("organisation (accrédités) : OUVERT jusqu'au dimanche 27 septembre 2026 à 21:00"
            in r["resume"])
    assert "public : FERMÉ, ouvre le samedi 26 septembre 2026 à 07:00" in r["resume"]


def test_sans_nom_etat_a_l_instant_pendant_l_evenement():
    r = lieux(type="porte")
    assert r["vue"] == "etat_maintenant"
    assert "PORTE NORD PIETONS (porte) : organisation (accrédités) jusqu'au dimanche" in r["resume"]
    assert "PORTE EST" not in r["resume"]  # fermee a 10:00 le 25 : pas listee comme ouverte


def test_prod_sans_nom_apres_l_evenement_rien_n_est_ouvert():
    # Constate le 07/10/2026 : la liste des lieux avait ete lue comme "ouverts".
    r = L.t_lieux(_db(), {}, CTX, now=datetime(2026, 10, 7, 20, 27))
    assert r["lieux_ouverts"] == 0
    assert "AUCUN lieu n'est ouvert : l'événement est terminé" in r["resume"]
    assert "dernière fermeture le dimanche 27 septembre 2026 à 21:00" in r["resume"]


def test_sans_nom_avant_l_evenement_prochaine_ouverture():
    r = L.t_lieux(_db(), {}, CTX, now=datetime(2026, 9, 1, 8, 0))
    assert "AUCUN lieu n'est ouvert. Prochaine ouverture : PORTE NORD PIETONS" in r["resume"]


# --- Controle d'acces ---------------------------------------------------------

def test_prod_tribune_controlee_lue_dans_daycontrol():
    # Constate le 07/10/2026 : "controlee en temps reel via Cockpit", invente.
    tri = dict(TRIBUNE, dates=[dict(e, dayControl="controle") for e in TRIBUNE["dates"]])
    texte, info = L.controle_item(tri)
    assert texte.startswith("contrôle d'accès : contrôlé le samedi 26 septembre 2026")
    assert "moyen de contrôle non précisé" in texte
    assert info["jours_controles"] == ["2026-09-26"]


def test_controle_libre_creneaux_et_moyen():
    item = {"controle": {"type": "PDA", "number": "3"}, "dates": [
        {"date": "2026-09-25", "dayControl": "libre"},
        {"date": "2026-09-26", "dayControl": "controle",
         "dayControlSlots": [{"start": "06:00", "end": "14:00", "type": "controle"}]}]}
    texte, _ = L.controle_item(item)
    assert "accès libre (sans contrôle) le vendredi 25 septembre 2026" in texte
    assert "le samedi 26 septembre 2026 : contrôlé de 06:00 à 14:00" in texte
    assert "moyen de contrôle : PDA x3" in texte


def test_controle_dans_le_resume_d_un_lieu():
    r = lieux(nom="porte nord pietons")
    assert "contrôle d'accès : contrôlé le lundi 21 septembre 2026" in r["resume"]


def test_controle_non_renseigne():
    texte, _ = L.controle_item({"dates": [{"date": "2026-09-26"}]})
    assert texte == "contrôle d'accès non renseigné dans le paramétrage"


def test_service_centre_medical():
    r = lieux(nom="centre médical")
    assert ("en continu du vendredi 25 septembre 2026 à 12:00 au dimanche 27 septembre 2026 "
            "à 19:30") in r["resume"]


def test_hospitalite_sans_horaires():
    r = lieux(nom="club des pilotes")
    assert "pas d'horaires" in r["resume"]


def test_evenement_sans_parametrage():
    r = L.t_lieux(_db(), {"nom": "porte nord", "evenement": "24H CAMIONS", "annee": "2031"},
                  CTX, now=NOW)
    assert r["disponible"] is False


# --- Taille ------------------------------------------------------------------

def test_resultat_borne_sans_casser_le_json():
    gros = {"disponible": True, "resume": "x" * 100, "lieux": [{"nom": "L%d" % i, "pad": "y" * 200}
                                                              for i in range(200)]}
    out = alfred_tools._borner(gros)
    import json
    assert len(json.dumps(out, ensure_ascii=False)) <= alfred_tools.RESULT_MAX_CHARS
    assert out["resume"] == "x" * 100 and out["tronque"] is True


def test_outil_present_au_manifeste():
    noms = {t["function"]["name"] for t in alfred_tools.manifest()}
    assert "cockpit_lieux" in noms
