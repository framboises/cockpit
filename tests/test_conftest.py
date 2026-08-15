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
