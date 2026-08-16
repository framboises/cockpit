from datetime import datetime

import pytest

from conftest import FakeDb

import watch_guidage


TOKEN = "jeton-a"
AUTRE = "jeton-b"

# Porte Houx 5, coin nord-ouest du circuit.
LAT = 47.9503
LON = 0.2214


@pytest.fixture(autouse=True)
def _indexes_neufs():
    # Le drapeau d'index est un global de module : sans remise a zero, le
    # premier test qui l'arme empeche tous les suivants de verifier que
    # l'index est bien cree.
    watch_guidage.reset_indexes()


def _db():
    return FakeDb(watch_guidage=[])


class TestValidation:
    def test_point_nominal_accepte(self):
        assert watch_guidage.validate_point(LAT, LON, "Porte Houx 5") == (
            True, None)

    def test_chaines_numeriques_acceptees(self):
        # Un formulaire web envoie du texte : le refuser obligerait l'UI a
        # convertir, et une conversion oubliee cote UI se verrait par un
        # refus incomprehensible.
        assert watch_guidage.validate_point("47.95", "0.22", "X")[0] is True

    def test_coordonnees_non_numeriques_refusees(self):
        assert watch_guidage.validate_point("ici", LON, "X") == (
            False, "coordonnees_invalides")
        assert watch_guidage.validate_point(None, LON, "X") == (
            False, "coordonnees_invalides")

    def test_nan_et_infini_refuses(self):
        # LE cas qui motive math.isfinite : float("nan") passe float() sans
        # broncher et arriverait jusqu'a la montre, ou il produirait une
        # fleche pointant nulle part.
        for mauvais in (float("nan"), float("inf"), float("-inf")):
            assert watch_guidage.validate_point(mauvais, LON, "X") == (
                False, "coordonnees_invalides")
            assert watch_guidage.validate_point(LAT, mauvais, "X") == (
                False, "coordonnees_invalides")

    def test_bornes_geographiques(self):
        assert watch_guidage.validate_point(91.0, LON, "X") == (
            False, "latitude_hors_bornes")
        assert watch_guidage.validate_point(LAT, 181.0, "X") == (
            False, "longitude_hors_bornes")
        # Les bornes elles-memes sont valides.
        assert watch_guidage.validate_point(90.0, 180.0, "X")[0] is True
        assert watch_guidage.validate_point(-90.0, -180.0, "X")[0] is True

    def test_libelle_vide_refuse(self):
        # Un point sans nom s'afficherait comme une fleche vers nulle part de
        # nommable : l'operateur doit dire ou il envoie.
        assert watch_guidage.validate_point(LAT, LON, "") == (
            False, "libelle_vide")
        assert watch_guidage.validate_point(LAT, LON, "   ") == (
            False, "libelle_vide")
        assert watch_guidage.validate_point(LAT, LON, None) == (
            False, "libelle_vide")


class TestSetPoint:
    def test_premier_envoi_cree_le_point_a_seq_un(self):
        db = _db()
        doc = watch_guidage.set_point(db, TOKEN, LAT, LON, "Porte Houx 5")
        assert doc["seq"] == 1
        assert doc["lat"] == LAT
        assert doc["label"] == "Porte Houx 5"

    def test_second_envoi_remplace_et_incremente(self):
        # Le guidage repond a << ou dois-je aller MAINTENANT >> : un second
        # envoi remplace, il ne s'empile pas.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "Porte Houx 5")
        doc = watch_guidage.set_point(db, TOKEN, 47.94, 0.23, "Tribune 12")
        assert doc["seq"] == 2
        assert doc["label"] == "Tribune 12"
        assert len(db["watch_guidage"].docs) == 1

    def test_meme_point_renvoye_incremente_quand_meme(self):
        # LA regression a tenir : renvoyer le meme point est un acte
        # volontaire (<< je te le redis >>) qui doit refaire vibrer la
        # montre. Si seq ne bougeait pas, le rappel serait muet.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "Porte Houx 5")
        doc = watch_guidage.set_point(db, TOKEN, LAT, LON, "Porte Houx 5")
        assert doc["seq"] == 2

    def test_deux_montres_ont_des_compteurs_independants(self):
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A")
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A")
        doc = watch_guidage.set_point(db, AUTRE, LAT, LON, "B")
        assert doc["seq"] == 1
        assert watch_guidage.read_point(db, TOKEN)["s"] == 2

    def test_libelle_tronque_a_la_borne(self):
        db = _db()
        long = "x" * (watch_guidage.LABEL_MAX + 10)
        doc = watch_guidage.set_point(db, TOKEN, LAT, LON, long)
        assert len(doc["label"]) == watch_guidage.LABEL_MAX

    def test_libelle_debarasse_de_ses_espaces(self):
        db = _db()
        doc = watch_guidage.set_point(db, TOKEN, LAT, LON, "  Porte Sud  ")
        assert doc["label"] == "Porte Sud"

    def test_point_invalide_leve_et_n_ecrit_rien(self):
        db = _db()
        with pytest.raises(ValueError):
            watch_guidage.set_point(db, TOKEN, float("nan"), LON, "X")
        assert db["watch_guidage"].docs == []

    def test_index_unique_sur_token(self):
        # C'est l'index qui garantit qu'un second envoi remplace au lieu de
        # creer un doublon que personne ne lirait jamais.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "X")
        assert ("token_id", {"unique": True}) in db["watch_guidage"].indexes


class TestReadPoint:
    def test_sans_point_rend_none(self):
        assert watch_guidage.read_point(_db(), TOKEN) is None

    def test_token_none_rend_none(self):
        # Un appelant sans jeton (cas theorique, mais /state le lit sur la
        # requete) ne doit pas remonter le point de quelqu'un d'autre.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "X")
        assert watch_guidage.read_point(db, None) is None

    def test_bloc_compact_pour_le_payload(self):
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "Porte Houx 5")
        bloc = watch_guidage.read_point(db, TOKEN)
        assert set(bloc.keys()) == {"lat", "lon", "n", "s", "t"}
        assert bloc["n"] == "Porte Houx 5"
        assert bloc["s"] == 1
        assert isinstance(bloc["t"], int)

    def test_une_montre_ne_voit_pas_le_point_d_une_autre(self):
        # LE risque de cette fonctionnalite : le payload /state est mis en
        # cache et partage entre montres. La lecture, elle, doit rester
        # strictement par jeton.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "Pour A")
        assert watch_guidage.read_point(db, AUTRE) is None

    def test_document_malforme_rend_none_sans_lever(self):
        # Regle commune a tous les blocs : une source abimee met le bloc a
        # null, elle ne fait pas tomber les autres pages.
        db = FakeDb(watch_guidage=[{"token_id": TOKEN, "lat": "au nord",
                                    "lon": LON, "seq": 1}])
        assert watch_guidage.read_point(db, TOKEN) is None

    def test_lecture_ne_leve_jamais_meme_si_mongo_tombe(self):
        class DbCassee:
            def __getitem__(self, _):
                raise RuntimeError("mongo injoignable")
        assert watch_guidage.read_point(DbCassee(), TOKEN) is None


class TestClearPoint:
    def test_efface_et_rend_vrai(self):
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "X")
        assert watch_guidage.clear_point(db, TOKEN) is True
        assert watch_guidage.read_point(db, TOKEN) is None

    def test_sans_point_rend_faux(self):
        assert watch_guidage.clear_point(_db(), TOKEN) is False

    def test_n_efface_que_la_montre_visee(self):
        # On efface la SECONDE montre enregistree, pas la premiere : une
        # suppression qui ignorerait le filtre emporterait le document en
        # tete de collection, et effacer la premiere la rendrait juste par
        # hasard. Defaut trouve par sabotage -- la version precedente de ce
        # test visait TOKEN, ecrit en premier, et laissait passer un
        # `delete_one({})`.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A")
        watch_guidage.set_point(db, AUTRE, LAT, LON, "B")
        assert watch_guidage.clear_point(db, AUTRE) is True
        assert watch_guidage.read_point(db, AUTRE) is None
        assert watch_guidage.read_point(db, TOKEN)["n"] == "A"

    def test_effacer_une_montre_sans_point_ne_touche_pas_les_autres(self):
        # Meme piege, autre angle : la montre visee n'a rien, une suppression
        # non filtree emporterait le point de la voisine.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A")
        assert watch_guidage.clear_point(db, AUTRE) is False
        assert watch_guidage.read_point(db, TOKEN)["n"] == "A"

    def test_apres_effacement_le_compteur_repart_a_un(self):
        # Consequence assumee de la suppression du document : un nouvel
        # envoi apres effacement repart a seq=1. Cote montre, la vibration
        # se declenche sur un seq DIFFERENT du dernier connu, pas seulement
        # superieur -- sans quoi ce redemarrage serait silencieux.
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A")
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A")
        watch_guidage.clear_point(db, TOKEN)
        assert watch_guidage.set_point(db, TOKEN, LAT, LON, "B")["seq"] == 1


class TestListPoints:
    def test_liste_indexee_par_jeton(self):
        db = _db()
        watch_guidage.set_point(db, TOKEN, LAT, LON, "A", sent_by="ludo")
        watch_guidage.set_point(db, AUTRE, 47.9, 0.2, "B")
        liste = watch_guidage.list_points(db)
        assert set(liste.keys()) == {TOKEN, AUTRE}
        assert liste[TOKEN]["label"] == "A"
        assert liste[TOKEN]["sent_by"] == "ludo"

    def test_liste_vide_sans_point(self):
        assert watch_guidage.list_points(_db()) == {}
