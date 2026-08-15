"""Verrouillage du comportement du mur meteo apres son extraction.

Ces tests ne jugent pas les seuils, ils les CONSTATENT : le mur du PC
Organisation tourne en permanence sur ces valeurs, et la montre doit repeter
exactement ce qu'il decide. Un test qui divergerait ici signalerait que les
deux ecrans vont se contredire.

Le calme est le cas de reference : 18 degres, 55 %, pas de pluie, CAPE faible
donnent un WBGT normal (verifie contre meteo_thermique.SEUILS_WBGT). Chaque
test ne fait varier que le parametre qu'il examine.
"""

from datetime import datetime

from conftest import FakeDb

import meteo_etat

MAINTENANT = datetime(2026, 8, 15, 12, 0)
JOUR = "2026-08-15"


def heure(h, **surcharges):
    """Un creneau horaire de meteo_previsions, calme par defaut."""
    entree = {
        "Heure": "%02d:00" % h,
        "Température (°C)": 18.0,
        "Humidité (%)": 55,
        "Pluviométrie (mm)": 0.0,
        "Vent moyen (km/h)": 12.0,
        "Vent rafale (km/h)": 20.0,
        "Direction vent (°)": 210,
        "Direction vent": "SO",
        "Nebulosite (%)": 40,
        "Rayonnement (J/m2)": 400000,
        "CAPE (J/kg)": 100,
        "Foudre (impacts/km2)": 0.0,
        "Grele (kg/m2)": 0.0,
        "source": "arome",
    }
    entree.update(surcharges)
    return entree


def base(surcharges_par_heure=None, **autres_collections):
    """Une base ou seul le jour courant porte des previsions.

    Les creneaux commencent a midi : etat_mur ecarte ce qui precede l'heure
    courante, et un horizon plus court ferait varier le nombre de consignes
    sans rapport avec ce qu'on teste.
    """
    surcharges_par_heure = surcharges_par_heure or {}
    heures = [heure(h, **surcharges_par_heure.get(h, {})) for h in range(12, 24)]
    return FakeDb(meteo_previsions=[{"Date": JOUR, "Heures": heures}],
                  **autres_collections)


def contrainte(etat, cle):
    return next(c for c in etat["contraintes"] if c["cle"] == cle)


class TestEtatMur:
    def test_base_vide_ne_leve_pas(self):
        # La montre doit pouvoir demander la meteo meme quand aucune source
        # n'a encore ecrit : un bloc a None est acceptable, une exception non.
        etat = meteo_etat.etat_mur(FakeDb(), MAINTENANT, {})
        assert isinstance(etat, dict)
        assert etat["actuel"] is None
        assert etat["prochaines"] == []
        assert etat["prochaine_pluie"] is None
        assert etat["vigilance"] is None
        assert etat["radar"] is None
        assert etat["sol"] is None

    def test_contrat_de_cles(self):
        # Le mur et la montre lisent le meme dictionnaire : toute cle qui
        # disparaitrait viderait un bloc d'ecran sans erreur visible.
        etat = meteo_etat.etat_mur(base(), MAINTENANT, {})
        assert set(etat) == {
            "maintenant", "actuel", "prochaines", "prochaine_pluie",
            "consignes", "contraintes", "verdict", "fraicheur", "vigilance",
            "sol", "radar"}

    def test_horizon_borne_a_douze_heures(self):
        # Douze heures, soit la duree d'une vacation. La valeur est ecrite en
        # clair et non lue depuis la constante : la comparer a elle-meme ne
        # dirait rien si quelqu'un la changeait.
        etat = meteo_etat.etat_mur(base(), MAINTENANT, {})
        assert meteo_etat.HORIZON_HEURES == 12
        assert len(etat["prochaines"]) == 12
        assert etat["actuel"]["heure"] == "12:00"
        assert etat["prochaines"][-1]["heure"] == "23:00"

    def test_calme_ne_signale_rien(self):
        etat = meteo_etat.etat_mur(base(), MAINTENANT, {})
        assert etat["consignes"] == []
        assert {c["niveau"] for c in etat["contraintes"]} == {"normal"}
        assert etat["verdict"]["niveau"] == "normal"
        assert etat["verdict"]["titre"] == "RIEN A SIGNALER"

    def test_config_non_consomme(self):
        # config est dans la signature pour que mur et montre appellent la
        # meme fonction ; le mur ne lit ni bbox ni rayon de veille aujourd'hui.
        vide = meteo_etat.etat_mur(base(), MAINTENANT, {})
        peuplee = meteo_etat.etat_mur(
            base(), MAINTENANT, {"bbox_site": {"west": 0.1}, "veille_km": 40})
        assert vide == peuplee


class TestConsigneVent:
    def test_rafale_au_dessus_de_80_est_critique(self):
        etat = meteo_etat.etat_mur(
            base({15: {"Vent rafale (km/h)": 92.0}}), MAINTENANT, {})
        consigne = next(c for c in etat["consignes"] if c["heure"] == "15:00")
        assert consigne["niveau"] == "critique"
        assert "evacuation des structures provisoires" in consigne["texte"]
        assert contrainte(etat, "vent")["niveau"] == "critique"
        assert etat["verdict"]["niveau"] == "critique"
        assert etat["verdict"]["titre"] == "RAFALES 92 KM/H"
        assert etat["verdict"]["echeance"] == "15:00"

    def test_rafale_entre_60_et_80_est_danger(self):
        etat = meteo_etat.etat_mur(
            base({14: {"Vent rafale (km/h)": 65.0}}), MAINTENANT, {})
        consigne = next(c for c in etat["consignes"] if c["heure"] == "14:00")
        assert consigne["niveau"] == "danger"
        assert "securiser les structures" in consigne["texte"]
        assert contrainte(etat, "vent")["niveau"] == "danger"

    def test_rafale_de_vigilance_ne_produit_pas_de_consigne(self):
        # 45 km/h marque la contrainte sans imposer d'action horaire : le
        # bandeau du mur n'a pas a se remplir pour du vent ordinaire.
        etat = meteo_etat.etat_mur(
            base({14: {"Vent rafale (km/h)": 45.0}}), MAINTENANT, {})
        assert etat["consignes"] == []
        vent = contrainte(etat, "vent")
        assert vent["niveau"] == "vigilance"
        assert vent["pic"] == 45
        assert vent["pic_heure"] == "14:00"
        assert vent["seuils"] == [40, 60, 80]

    def test_verdict_de_repli_sur_la_contrainte(self):
        etat = meteo_etat.etat_mur(
            base({14: {"Vent rafale (km/h)": 45.0}}), MAINTENANT, {})
        assert etat["verdict"]["titre"] == "VENT A SURVEILLER"
        assert etat["verdict"]["niveau"] == "vigilance"


class TestConsigneChaleur:
    def test_wbgt_en_danger_impose_une_consigne(self):
        etat = meteo_etat.etat_mur(
            base({16: {"Température (°C)": 31.0, "Humidité (%)": 62}}),
            MAINTENANT, {})
        consigne = next(c for c in etat["consignes"] if c["heure"] == "16:00")
        assert consigne["niveau"] == "danger"
        assert consigne["texte"].startswith("WBGT ")
        chaleur = contrainte(etat, "chaleur")
        assert chaleur["niveau"] == "danger"
        assert chaleur["pic_heure"] == "16:00"

    def test_wbgt_normal_reste_muet(self):
        etat = meteo_etat.etat_mur(base(), MAINTENANT, {})
        assert contrainte(etat, "chaleur")["niveau"] == "normal"


class TestConsigneOrage:
    def test_orage_avere_impose_une_mise_a_l_abri(self):
        etat = meteo_etat.etat_mur(
            base({17: {"CAPE (J/kg)": 2600, "Foudre (impacts/km2)": 4.0,
                       "Grele (kg/m2)": 1.2}}),
            MAINTENANT, {})
        consigne = next(c for c in etat["consignes"] if c["heure"] == "17:00")
        assert consigne["niveau"] == "critique"
        assert consigne["texte"].endswith("mise a l'abri")
        orage = contrainte(etat, "orage")
        assert orage["niveau"] == "critique"
        assert orage["detail"] == "declenchement prevu"
        assert orage["pic_heure"] == "17:00"

    def test_orage_possible_est_un_danger_sans_consigne_horaire(self):
        # "possible" marque la contrainte mais ne declenche pas de consigne :
        # seul l'orage avere le fait.
        etat = meteo_etat.etat_mur(
            base({17: {"CAPE (J/kg)": 1800, "Foudre (impacts/km2)": 0.5}}),
            MAINTENANT, {})
        assert etat["consignes"] == []
        orage = contrainte(etat, "orage")
        assert orage["niveau"] == "danger"
        assert orage["consigne"] == "Mise a l abri a preparer"


class TestContrainteSol:
    def test_pluie_horaire_declenche_une_vigilance(self):
        etat = meteo_etat.etat_mur(
            base({18: {"Pluviométrie (mm)": 9.5}}), MAINTENANT, {})
        consigne = next(c for c in etat["consignes"] if c["heure"] == "18:00")
        assert consigne["niveau"] == "vigilance"
        assert "parkings en herbe" in consigne["texte"]
        sol = contrainte(etat, "sol")
        assert sol["niveau"] == "vigilance"
        assert sol["pic"] == 9.5
        assert sol["unite"] == "mm"

    def test_cumul_au_dessus_de_15_mm_est_un_danger(self):
        etat = meteo_etat.etat_mur(
            base({18: {"Pluviométrie (mm)": 9.0},
                  19: {"Pluviométrie (mm)": 7.0}}), MAINTENANT, {})
        sol = contrainte(etat, "sol")
        assert sol["niveau"] == "danger"
        assert sol["consigne"] == "Parkings en herbe a fermer ou renforcer"
        assert sol["pic"] == 16.0

    def test_sol_tres_sec_bascule_sur_le_risque_incendie(self):
        # Sans pluie, c'est l'indice d'humidite des sols qui parle, et il parle
        # de feu, pas de portance.
        etat = meteo_etat.etat_mur(
            base(meteo_sol=[{"circuit": True, "date": "2026-08-14",
                             "swi": 0.15}]),
            MAINTENANT, {})
        sol = contrainte(etat, "sol")
        assert sol["niveau"] == "vigilance"
        assert "risque incendie" in sol["consigne"]
        assert sol["unite"] == "SWI"
        assert sol["swi"] == 0.15


class TestUneConsigneParHeure:
    def test_la_plus_grave_l_emporte(self):
        # Pluie et rafale critique a la meme heure : le mur n'affiche que
        # l'action la plus contraignante.
        etat = meteo_etat.etat_mur(
            base({15: {"Vent rafale (km/h)": 92.0,
                       "Pluviométrie (mm)": 9.5}}), MAINTENANT, {})
        a_quinze = [c for c in etat["consignes"] if c["heure"] == "15:00"]
        assert len(a_quinze) == 1
        assert a_quinze[0]["niveau"] == "critique"

    def test_consignes_triees_par_heure(self):
        etat = meteo_etat.etat_mur(
            base({20: {"Vent rafale (km/h)": 92.0},
                  14: {"Vent rafale (km/h)": 65.0}}), MAINTENANT, {})
        assert [c["heure"] for c in etat["consignes"]] == ["14:00", "20:00"]


class TestVerdict:
    def test_la_pluie_imminente_passe_avant_le_calme(self):
        verdict = meteo_etat._verdict(
            {"attendue": True, "dans_min": 30, "intensite_mmh": 1.4,
             "a": "2026-08-15T12:30:00"},
            [], [{"cle": "vent", "niveau": "normal", "libelle": "Vent",
                  "consigne": "", "detail": "en rafales"}])
        assert verdict["niveau"] == "vigilance"
        assert verdict["titre"] == "PLUIE DANS 30 MIN"
        assert verdict["echeance"] == "12:30"
        assert "1.4 mm/h attendus" in verdict["detail"]

    def test_une_consigne_critique_passe_avant_la_pluie(self):
        verdict = meteo_etat._verdict(
            {"attendue": True, "dans_min": 5, "intensite_mmh": 8.0,
             "a": "2026-08-15T12:05:00"},
            [{"niveau": "critique", "heure": "13:00",
              "texte": "Rafales 92 km/h — evacuation des structures provisoires"}],
            [])
        assert verdict["niveau"] == "critique"
        assert verdict["titre"] == "RAFALES 92 KM/H"


class TestFraicheur:
    def test_absence_de_piaf_et_de_previsions_est_signalee(self):
        etat = meteo_etat.etat_mur(FakeDb(), MAINTENANT, {})
        fraicheur = etat["fraicheur"]
        assert fraicheur["ok"] is False
        assert {f["cle"] for f in fraicheur["en_retard"]} == {"piaf", "previsions"}

    def test_previsions_presentes_mais_piaf_absent(self):
        etat = meteo_etat.etat_mur(base(), MAINTENANT, {})
        fraicheur = etat["fraicheur"]
        assert fraicheur["ok"] is False
        assert [f["cle"] for f in fraicheur["en_retard"]] == ["piaf"]
        previsions = next(f for f in fraicheur["flux"] if f["cle"] == "previsions")
        assert previsions["ok"] is True


class TestVigilance:
    def test_le_bulletin_est_rendu_avec_sa_fraicheur(self):
        # Le mur ne recalcule pas la peremption de son cote : etat_mur joint
        # celle que meteo_etat produit, une seule regle pour tout le monde.
        bulletin = {
            "departement": "72",
            "update_time": "2026-08-15T09:00:00+00:00",
            "periodes": [
                {"echeance": "J", "couleur_max": "jaune",
                 "debut": "2026-08-15T04:00:00+00:00",
                 "fin": "2026-08-15T22:00:00+00:00",
                 "phenomenes": [{"nom": "Orages"}]},
                {"echeance": "J1", "couleur_max": "orange",
                 "debut": "2026-08-15T22:00:00+00:00",
                 "fin": "2026-08-16T22:00:00+00:00",
                 "phenomenes": [{"nom": "Canicule"}]},
            ],
        }
        etat = meteo_etat.etat_mur(
            base(meteo_vigilance=[bulletin]), MAINTENANT, {})
        vigilance = etat["vigilance"]
        assert vigilance["departement"] == "72"
        assert vigilance["couleur_jour"] == "jaune"
        assert vigilance["couleur_max"] == "orange"
        assert vigilance["motif"] == "validite_publiee"
        assert vigilance["perime"] is False

    def test_les_fonctions_de_vigilance_restent_publiques(self):
        # meteo.py et app.py les importent : elles ne vivent plus dans meteo.py
        # mais leur contrat, lui, n'a pas bouge.
        assert meteo_etat.niveau_vigilance(None)["couleur_jour"] == "vert"
        assert meteo_etat.etat_vigilance(None)["motif"] == "aucun_bulletin"
