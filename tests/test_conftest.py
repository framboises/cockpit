"""Le double Mongo tient-il sa propre regle de conduite ?

conftest.py est partage par toutes les suites : s'il laisse passer en silence
un filtre ou un tri qu'il ne sait pas honorer, chaque test qui s'appuie dessus
devient tautologique -- vert avec le code correct comme avec le code fautif.
Ces quelques tests verrouillent la partie du double qui vient de changer.
"""

from datetime import datetime, timezone

import pytest

from conftest import FakeDb


class TestTriDuCurseur:
    def test_le_tri_suit_la_signature_pymongo(self):
        db = FakeDb(x=[{"n": 2}, {"n": 1}, {"n": 3}])
        assert [d["n"] for d in db["x"].find({}).sort("n", 1)] == [1, 2, 3]
        assert [d["n"] for d in db["x"].find({}).sort("n", -1)] == [3, 2, 1]

    def test_le_tri_est_chainable(self):
        # find(...).sort(...) doit se lire d'un trait, comme sur un curseur.
        db = FakeDb(x=[{"n": 2}, {"n": 1}])
        assert list(db["x"].find({}).sort("n", 1))[0]["n"] == 1

    def test_une_cle_absente_leve_au_lieu_de_trier_au_hasard(self):
        # Rendre un ordre arbitraire ferait passer le test quoi qu'il arrive.
        db = FakeDb(x=[{"n": 2, "m": 1}, {"n": 1}])
        with pytest.raises(NotImplementedError):
            list(db["x"].find({}).sort("m", 1))


class TestEgaliteDesDatetimes:
    def test_un_datetime_naif_se_retrouve_lui_meme(self):
        # pymongo relit en naif-UTC ce qu'il a stocke : filtrer sur une valeur
        # tout juste lue dans un document doit retrouver ce document.
        instant = datetime(2026, 8, 15, 11, 50)
        db = FakeDb(x=[{"run_at": instant, "n": 1}])
        assert db["x"].find_one({"run_at": instant})["n"] == 1

    def test_naif_et_conscient_se_correspondent(self):
        db = FakeDb(x=[{"run_at": datetime(2026, 8, 15, 11, 50), "n": 1}])
        conscient = datetime(2026, 8, 15, 11, 50, tzinfo=timezone.utc)
        assert db["x"].find_one({"run_at": conscient})["n"] == 1


class TestAggregate:
    def test_match_puis_group_compte_vraiment(self):
        # Verifie le travail reel du pipeline (filtrage + comptage par cle
        # composite), pas seulement une forme de sortie attendue par le test.
        db = FakeDb(x=[
            {"cat": "PCO.Secours", "s": 2}, {"cat": "PCO.Secours", "s": 2},
            {"cat": "PCO.Secours", "s": 10}, {"cat": "AUTRE", "s": 2},
        ])
        pipeline = [
            {"$match": {"cat": {"$regex": "^PCO"}}},
            {"$group": {"_id": {"cat": "$cat", "clos": {"$eq": ["$s", 10]}},
                        "n": {"$sum": 1}}},
        ]
        lignes = {(l["_id"]["cat"], l["_id"]["clos"]): l["n"]
                  for l in db["x"].aggregate(pipeline)}
        assert lignes == {("PCO.Secours", False): 2, ("PCO.Secours", True): 1}

    def test_match_regex_exclut_ce_qui_ne_matche_pas(self):
        db = FakeDb(x=[{"cat": "PCO.Secours"}, {"cat": "AUTRE"}])
        pipeline = [{"$match": {"cat": {"$regex": "^PCO"}}},
                    {"$group": {"_id": "$cat", "n": {"$sum": 1}}}]
        assert [l["_id"] for l in db["x"].aggregate(pipeline)] == [
            "PCO.Secours"]

    def test_etage_inconnu_leve(self):
        # Un etage ignore en silence rendrait le pipeline tautologique.
        db = FakeDb(x=[{"n": 1}])
        with pytest.raises(NotImplementedError):
            list(db["x"].aggregate([{"$project": {"n": 1}}]))

    def test_accumulateur_inconnu_leve(self):
        db = FakeDb(x=[{"n": 1}])
        with pytest.raises(NotImplementedError):
            list(db["x"].aggregate(
                [{"$group": {"_id": "$n", "moy": {"$avg": "$n"}}}]))


class TestFindOneAndUpdate:
    """Le double porte desormais `$inc`, qui compte les envois de guidage --
    donc qui declenche la vibration cote montre. Un double qui l'ignorerait
    en silence laisserait passer un compteur fige, la meme classe de bug que
    le faux `$gte` corrige plus haut dans ce fichier."""

    def test_incremente_un_champ_absent_depuis_zero(self):
        db = FakeDb(x=[{"k": 1}])
        doc = db["x"].find_one_and_update({"k": 1}, {"$inc": {"seq": 1}})
        assert doc["seq"] == 1

    def test_incremente_un_champ_existant(self):
        db = FakeDb(x=[{"k": 1, "seq": 6}])
        doc = db["x"].find_one_and_update({"k": 1}, {"$inc": {"seq": 1}})
        assert doc["seq"] == 7

    def test_upsert_cree_le_document_avec_le_filtre(self):
        db = FakeDb(x=[])
        doc = db["x"].find_one_and_update(
            {"k": 9}, {"$set": {"v": "a"}, "$inc": {"seq": 1}}, upsert=True)
        assert doc == {"k": 9, "v": "a", "seq": 1}
        assert len(db["x"].docs) == 1

    def test_sans_upsert_un_document_absent_rend_none(self):
        db = FakeDb(x=[])
        assert db["x"].find_one_and_update({"k": 9}, {"$inc": {"seq": 1}}) is None
        assert db["x"].docs == []

    def test_deux_appels_ne_creent_pas_deux_documents(self):
        # Le vrai index unique l'interdit en base ; le double doit se
        # comporter pareil, sinon le test de remplacement serait faux.
        db = FakeDb(x=[])
        for _ in range(3):
            db["x"].find_one_and_update({"k": 1}, {"$inc": {"seq": 1}},
                                        upsert=True)
        assert len(db["x"].docs) == 1
        assert db["x"].docs[0]["seq"] == 3

    def test_operateur_inconnu_leve(self):
        db = FakeDb(x=[{"k": 1}])
        with pytest.raises(NotImplementedError):
            db["x"].find_one_and_update({"k": 1}, {"$unset": {"k": ""}})


class TestDeleteOne:
    def test_rend_le_compte_supprime(self):
        db = FakeDb(x=[{"k": 1}, {"k": 2}])
        assert db["x"].delete_one({"k": 1}).deleted_count == 1
        assert [d["k"] for d in db["x"].docs] == [2]

    def test_rien_a_supprimer_rend_zero(self):
        # Ce zero est lu comme un booleen par l'appelant : le rendre a None
        # ferait passer un << rien a effacer >> pour un << efface >>.
        db = FakeDb(x=[{"k": 1}])
        assert db["x"].delete_one({"k": 9}).deleted_count == 0
        assert len(db["x"].docs) == 1

    def test_n_en_supprime_qu_un_seul(self):
        db = FakeDb(x=[{"k": 1}, {"k": 1}])
        db["x"].delete_one({"k": 1})
        assert len(db["x"].docs) == 1
