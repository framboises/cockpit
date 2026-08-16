from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from conftest import FakeDb

import watch_timeline


TZ = ZoneInfo("Europe/Paris")

# Samedi de course, 5 h 30 du matin : toute la journee est devant.
NOW = datetime(2026, 4, 18, 5, 30, tzinfo=TZ).astimezone(timezone.utc)


def _timetable(vignettes, event="24H MOTOS", year="2026"):
    """Un document timetable, forme reelle : data = {date: [vignettes]}."""
    data = {}
    for v in vignettes:
        data.setdefault(v["date"], []).append(v)
    return FakeDb(timetable=[{"event": event, "year": year, "data": data}])


def _v(heure, activite, lieu="", date="2026-04-18", categorie="Controle"):
    return {"date": date, "start": heure, "activity": activite,
            "place": lieu, "category": categorie, "department": "SAFE"}


class TestProchaines:
    def test_vignette_compactee_en_tableau(self):
        db = _timetable([_v("08:00", "Ouverture au public", "Controle")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert len(liste) == 1
        quand, activite, lieu, compte = liste[0]
        assert activite == "Ouverture au public"
        assert lieu == "Controle"
        assert compte == 0
        # L'heure voyage en EPOCH, jamais formatee : la montre recalcule
        # elle-meme le compte a rebours, et le releve peut avoir trois
        # minutes de retard.
        assert datetime.fromtimestamp(quand, TZ).strftime("%H:%M") == "08:00"

    def test_epoch_juste_en_heure_d_ete(self):
        # Le 18 avril, Paris est a UTC+2. Un naif interprete comme UTC
        # decalerait de deux heures -- le genre de defaut qui ne se voit
        # qu'au moment ou il compte.
        db = _timetable([_v("08:00", "Ouverture au public")])
        quand = watch_timeline.prochaines(db, "24H MOTOS", 2026,
                                          now_utc=NOW)[0][0]
        attendu = datetime(2026, 4, 18, 8, 0, tzinfo=TZ).timestamp()
        assert quand == int(attendu)

    def test_tri_chronologique(self):
        db = _timetable([_v("09:00", "Tard"), _v("07:00", "Tot"),
                         _v("08:00", "Milieu")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert [v[1] for v in liste] == ["Tot", "Milieu", "Tard"]

    def test_le_passe_est_ecarte(self):
        # Une vignette de 4 h du matin alors qu'il est 5 h 30 : elle
        # n'interesse plus personne.
        db = _timetable([_v("04:00", "Deja passe"), _v("08:00", "A venir")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert [v[1] for v in liste] == ["A venir"]

    def test_au_dela_de_la_fenetre_est_ecarte(self):
        # Fenetre de 12 h depuis 5 h 30 : 23 h du soir est dehors.
        db = _timetable([_v("08:00", "Dedans"), _v("23:00", "Dehors")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert [v[1] for v in liste] == ["Dedans"]

    def test_fenetre_elargie_a_la_demande(self):
        db = _timetable([_v("23:00", "Tard le soir")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, heures=20,
                                          now_utc=NOW)
        assert [v[1] for v in liste] == ["Tard le soir"]

    # --- Factorisation : la raison d'etre de cette page ----------------

    def test_ouvertures_simultanees_factorisees(self):
        # LE comportement qui rend la page lisible au poignet : huit
        # parkings qui ouvrent a la meme heure font UNE ligne. Sans lui, la
        # journee de course des 24H Motos 2026 sort 65 vignettes -- neuf
        # apres factorisation.
        db = _timetable([_v("07:00", "Ouverture Parking %s" % nom, nom)
                         for nom in ["CHINETTI", "EXPO", "LMS", "P2"]])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert len(liste) == 1
        assert liste[0][3] == 4          # le compte est rendu a part
        assert "CHINETTI" in liste[0][2]  # et les lieux sont nommes

    def test_le_compte_sort_du_libelle(self):
        # pcorg_summary colle " (x4)" au libelle. On le retire pour rendre
        # le compte a part : sinon la montre l'afficherait deux fois, une
        # fois dans le texte et une fois dans sa pastille.
        db = _timetable([_v("07:00", "Ouverture Parking %s" % nom, nom)
                         for nom in ["A", "B", "C", "D"]])
        activite = watch_timeline.prochaines(db, "24H MOTOS", 2026,
                                             now_utc=NOW)[0][1]
        assert "(" not in activite
        assert activite == "Ouverture parkings"

    def test_deux_creneaux_differents_ne_se_melangent_pas(self):
        # La factorisation groupe par heure EXACTE : des parkings qui
        # ouvrent a 7 h et d'autres a 8 h font deux lignes, pas une.
        db = _timetable(
            [_v("07:00", "Ouverture Parking %s" % n, n) for n in ["A", "B"]]
            + [_v("08:00", "Ouverture Parking %s" % n, n) for n in ["C", "D"]])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert len(liste) == 2
        assert [v[3] for v in liste] == [2, 2]

    def test_une_seule_ouverture_n_est_pas_factorisee(self):
        db = _timetable([_v("07:00", "Ouverture Parking CHINETTI", "CHINETTI")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert liste[0][3] == 0
        assert liste[0][1] == "Ouverture Parking CHINETTI"

    # --- Bornes et robustesse -----------------------------------------

    def test_plafond_de_vignettes(self):
        db = _timetable([_v("0%d:%02d" % (7 + i // 60, i % 60), "Acte %d" % i)
                         for i in range(watch_timeline.MAX_VIGNETTES + 6)])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert len(liste) == watch_timeline.MAX_VIGNETTES

    def test_libelles_tronques(self):
        db = _timetable([_v("08:00", "A" * 80, "B" * 80)])
        _, activite, lieu, _ = watch_timeline.prochaines(
            db, "24H MOTOS", 2026, now_utc=NOW)[0]
        assert len(activite) == watch_timeline.ACTIVITE_MAX
        assert len(lieu) == watch_timeline.LIEU_MAX

    def test_heure_illisible_ecartee_sans_casser_le_reste(self):
        # "TBC" et les heures vides existent en base : elles ne doivent ni
        # lever ni produire une ligne a l'heure fausse.
        db = _timetable([_v("TBC", "Horaire a confirmer"),
                         _v("", "Sans horaire"),
                         _v("08:00", "Ferme")])
        liste = watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW)
        assert [v[1] for v in liste] == ["Ferme"]

    def test_sans_evenement_rend_vide(self):
        db = _timetable([_v("08:00", "X")])
        assert watch_timeline.prochaines(db, None, None, now_utc=NOW) == []
        assert watch_timeline.prochaines(db, "24H MOTOS", None, now_utc=NOW) == []

    def test_autre_evenement_ignore(self):
        db = _timetable([_v("08:00", "X")], event="GPF")
        assert watch_timeline.prochaines(db, "24H MOTOS", 2026, now_utc=NOW) == []

    def test_hors_evenement_rend_vide(self):
        # L'etat normal l'essentiel de l'annee : rien de prevu dans les
        # douze heures. Ce n'est pas une panne.
        db = _timetable([_v("08:00", "X")])
        aout = datetime(2026, 8, 16, 10, 0, tzinfo=TZ).astimezone(timezone.utc)
        assert watch_timeline.prochaines(db, "24H MOTOS", 2026,
                                         now_utc=aout) == []

    def test_source_cassee_rend_vide_sans_lever(self):
        # Regle commune a tous les blocs : une source abimee vide la page,
        # elle ne fait pas tomber les autres.
        class DbCassee:
            def __getitem__(self, _):
                raise RuntimeError("mongo injoignable")
        assert watch_timeline.prochaines(DbCassee(), "24H MOTOS", 2026,
                                         now_utc=NOW) == []

    def test_annee_entiere_ou_chaine(self):
        # `year` est stocke en CHAINE dans timetable, mais resolve_event
        # rend un entier. get_upcoming_timetable gere les deux -- ce test
        # verrouille que watch_timeline ne casse pas ce repli.
        db = _timetable([_v("08:00", "X")], year="2026")
        assert len(watch_timeline.prochaines(db, "24H MOTOS", 2026,
                                             now_utc=NOW)) == 1


class TestProchaine:
    def test_rend_la_premiere_seulement(self):
        db = _timetable([_v("09:00", "Tard"), _v("07:00", "Tot")])
        assert watch_timeline.prochaine(db, "24H MOTOS", 2026,
                                        now_utc=NOW)[1] == "Tot"

    def test_none_si_rien_de_prevu(self):
        db = _timetable([])
        assert watch_timeline.prochaine(db, "24H MOTOS", 2026,
                                        now_utc=NOW) is None

    def test_meme_forme_que_les_elements_de_la_liste(self):
        # La montre lit le meme code pour `nx` et pour une ligne de la
        # liste : deux formes differentes obligeraient a deux chemins de
        # rendu, donc a deux occasions de diverger.
        db = _timetable([_v("07:00", "Tot", "Ici")])
        assert (watch_timeline.prochaine(db, "24H MOTOS", 2026, now_utc=NOW)
                == watch_timeline.prochaines(db, "24H MOTOS", 2026,
                                             now_utc=NOW)[0])
