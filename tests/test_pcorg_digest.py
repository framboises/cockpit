"""Fiches structurees par le modele local (pcorg_digest.py) : parties pures."""
import json

import pytest

import pcorg_digest as PD


FICHE = {"_id": "f1", "text": "Bagarre buvette", "comment": "12:00 , A. Dupont\n deux blesses",
         "category": "PCO.Securite", "niveau_urgence": None, "event": "24H AUTOS", "year": 2026,
         "date_local": "13/06/2026", "time_local": "12:00",
         "content_category": {"appelant": "Mme X", "telephone": "0600000000"}}


class TestReponseModele:
    def test_reponse_valide(self):
        r = PD.parse_reply(json.dumps({"gravite": "4", "justification": "deux blesses", "type": "rixe",
                                       "victimes": 2, "moyens_engages": "S3M", "issue": "evacues",
                                       "resume": "Rixe avec deux blesses."}))
        assert r["gravite"] == 4 and r["victimes"] == 2 and r["type"] == "rixe"

    def test_champs_manquants_non_precise(self):
        r = PD.parse_reply('{"gravite": 1}')
        assert r["resume"] == "non precise" and r["victimes"] == 0

    def test_agression_d_agent_et_chaleur(self):
        r = PD.parse_reply(json.dumps({"gravite": 3, "agression_agent": {"cible": "securite", "forme": "Verbale",
                                                                        "description": "insultes au filtrage"},
                                       "chaleur": {"impact": "inconnu"}}))
        assert r["agression_agent"] == {"cible": "securite", "forme": "verbale", "description": "insultes au filtrage"}
        assert r["chaleur"] is None                                  # valeur hors liste, rien d'autre

    def test_coup_de_chaleur_vaut_impact_chaleur(self):
        r = PD.parse_reply(json.dumps({"gravite": 3, "secours": {"nature": "coup_de_chaleur"}}))
        assert r["chaleur"]["impact"] == "personne"

    def test_age_et_sante_d_un_anonyme_ne_sont_pas_personnels(self):
        r = PD.parse_reply(json.dumps({"gravite": 3, "donnees_personnelles": ["age", "sante", "plaque"]}))
        assert r["donnees_personnelles"] == ["plaque"]
        r = PD.parse_reply(json.dumps({"gravite": 3, "donnees_personnelles": ["nom", "sante"]}))
        assert r["donnees_personnelles"] == ["nom", "sante"]

    @pytest.mark.parametrize("raw", ['{"gravite": 7}', '{"gravite": "x"}', "[1]", "pas du json"])
    def test_reponse_rejetee(self, raw):
        with pytest.raises(ValueError):
            PD.parse_reply(raw)


class TestSource:
    def test_jamais_l_appelant_ni_son_telephone(self):
        src = PD.source_text(FICHE)
        assert "Bagarre buvette" in src and "deux blesses" in src
        assert "0600000000" not in src and "Mme X" not in src

    def test_zone_de_saisie_nommee_comme_telle(self):
        src = PD.source_text(dict(FICHE, area={"desc": "_MC PCO/Interieur Dep./01-Grand Paddock"}))
        assert "Zone de saisie (approximative, ce n'est pas le lieu) : Grand Paddock" in src
        assert "Zone de saisie" not in PD.source_text(dict(FICHE, area={"desc": "_MC PCO"}))

    def test_empreinte_suit_le_texte(self):
        h = PD.src_hash(FICHE)
        assert h == PD.src_hash(dict(FICHE))
        assert h != PD.src_hash(dict(FICHE, comment="autre deroule"))


class TestAReprendre:
    def _digest(self, **kw):
        d = {"src_hash": PD.src_hash(FICHE), "prompt_version": PD.PROMPT_VERSION, "error": None, "attempts": 0}
        d.update(kw)
        return d

    def test_sans_digest(self):
        assert PD.needs_work(FICHE, None)

    def test_a_jour(self):
        assert not PD.needs_work(FICHE, self._digest())

    def test_texte_ou_consignes_changes(self):
        assert PD.needs_work(FICHE, self._digest(src_hash="autre"))
        assert PD.needs_work(FICHE, self._digest(prompt_version="v0"))

    def test_echec_retente_puis_abandonne(self):
        assert PD.needs_work(FICHE, self._digest(error="delai", attempts=1))
        assert not PD.needs_work(FICHE, self._digest(error="delai", attempts=PD.MAX_ATTEMPTS))

    def test_fiche_controlee_retraitee_seulement_pour_nouvelles_consignes(self):
        assert PD.needs_work(FICHE, self._digest(prompt_version="v0", review={"verdict": "ok"}))
        assert not PD.needs_work(FICHE, self._digest(src_hash="autre", review={"verdict": "ok"}))

    def test_fiche_ignoree_jamais_retraitee(self):
        assert not PD.needs_work(FICHE, self._digest(prompt_version="v0", review={"verdict": "ignoree"}))


def test_fenetre_de_nuit():
    from datetime import datetime
    def at(h, m=0):
        return datetime(2026, 10, 27, h, m, tzinfo=PD.PARIS)
    assert PD.in_window("01:00-06:00", at(3))
    assert not PD.in_window("01:00-06:00", at(6))
    assert not PD.in_window("01:00-06:00", at(0, 59))
    assert PD.in_window("23:00-05:00", at(23, 30)) and PD.in_window("23:00-05:00", at(4))
    assert not PD.in_window("23:00-05:00", at(12))


def test_echeance_de_fin_le_lendemain_si_passee():
    from datetime import datetime
    now = datetime(2026, 10, 27, 23, 0, tzinfo=PD.PARIS)
    assert PD._end_deadline("06:00", now) == datetime(2026, 10, 28, 6, 0, tzinfo=PD.PARIS)
    assert PD._end_deadline("23:30", now) == datetime(2026, 10, 27, 23, 30, tzinfo=PD.PARIS)
