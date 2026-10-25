"""Tests de portes_etat.py : mode d'une porte, passages valides, capacite par
sens, alertes du mur, alignement N-1. Calcul pur, aucune base."""

import os
from datetime import datetime, timedelta

os.environ.setdefault("TITAN_ENV", "dev")
import portes_etat as P  # noqa: E402

T = datetime(2026, 5, 10, 9, 30)


class TestMode:
    def test_entree_mixte_sortie(self):
        assert P.mode_de(95, 5) == "entree"
        assert P.mode_de(50, 50) == "mixte"
        assert P.mode_de(10, 90) == "sortie"

    def test_seuils_exclusifs(self):
        # 20 % pile de sorties : deja mixte ; 80 % pile : encore mixte
        assert P.mode_de(80, 20) == "mixte"
        assert P.mode_de(20, 80) == "mixte"

    def test_flux_faible_lu_sur_les_appareils(self):
        # 3 passages ne disent rien : le sens des appareils decide
        assert P.mode_de(2, 1, devices_e=0, devices_s=3) == "sortie"
        assert P.mode_de(2, 1, devices_e=2, devices_s=2) == "mixte"
        assert P.mode_de(0, 0) is None


class TestCompter:
    def test_champs_du_collecteur_font_foi(self):
        d = {"entrees": 100, "sorties": 40, "entrees_valides": 90, "sorties_valides": 38,
             "entrees_vehicules": 10, "sorties_vehicules": 0, "erreurs": 12}
        assert P.compter(d) == (80, 38, 10, 0, 10, 2)

    def test_repli_au_prorata_des_refus(self):
        # Ancienne tranche, pas d'archive des erreurs : 10 refus repartis 3/1
        d = {"entrees": 75, "sorties": 25, "erreurs": 8}
        pe_, ps_, _ve, _vs, re_, rs_ = P.compter(d)
        assert re_ + rs_ == 8 and re_ == 6 and rs_ == 2
        assert (pe_, ps_) == (69, 23)


class TestCapacites:
    def test_appareil_au_prorata_de_son_sens(self):
        devs = {"TRI-1": [100, 0, "tripode"], "TRI-2": [0, 50, "tripode"],
                "PDA-1": [30, 30, "pda"]}
        cap_e, cap_s, n_e, n_s, n_es = P._capacites_appareils(devs)
        assert round(cap_e) == 900 + 325
        assert round(cap_s) == 900 + 325
        assert (n_e, n_s, n_es) == (1, 1, 1)


def _porte(buckets, devices=None):
    return {"kind": "porte", "buckets": buckets, "devices": devices or {}}


class TestAnalyse:
    def _flux(self, e, s, devs):
        """Trois tranches de 5 min avant T avec e entrees et s sorties chacune."""
        b, d = {}, {}
        for i in range(1, 4):
            t = T - timedelta(minutes=5 * i)
            b[t] = [e, s, 0, 0, 0, 0]
            d[t] = devs
        return _porte(b, d)

    def test_porte_en_entree_saturee_face_a_la_securite(self):
        # 150 entrees / 5 min = 1800/h pour 4 agents (1400/h) : 129 %
        p = self._flux(150, 3, {"TRI-1": (150, 3, "tripode")})
        staffing = {"PORTE EST": {datetime(2026, 5, 10, 9, 30): 4, datetime(2026, 5, 10, 9, 0): 4}}
        e = P.analyser_porte("PORTE EST", p, T, staffing, None, True, set(), avec_series=False)
        assert e["mode"] == "entree"
        assert e["capacite"]["securite"] == 1400
        assert e["charge"]["source_entree"] == "securite"
        assert e["niveau"] == "saturation"
        al = P.alertes([e])
        assert al[0]["niveau"] == "critique"
        assert "prévoir +2 agents" in al[0]["texte"]

    def test_porte_en_sortie_jugee_sur_ses_appareils(self):
        # 720 sorties/h sur un tripode en sortie (~870/h) : vigilance ; la
        # securite n'entre pas en compte en sortie.
        p = self._flux(2, 60, {"TRI-1": (2, 60, "tripode")})
        staffing = {"PORTE EST": {datetime(2026, 5, 10, 9, 30): 1}}
        e = P.analyser_porte("PORTE EST", p, T, staffing, None, True, set(), avec_series=False)
        assert e["mode"] == "sortie"
        assert e["charge"]["sens_critique"] == "sortie"
        assert e["niveau"] == "vigilance"
        # Deux tripodes en sortie : rien a signaler
        p = self._flux(2, 60, {"TRI-1": (1, 30, "tripode"), "TRI-2": (1, 30, "tripode")})
        e = P.analyser_porte("PORTE EST", p, T, staffing, None, True, set(), avec_series=False)
        assert e["niveau"] == "normal"

    def test_fermee_sans_passage_depuis_30_min(self):
        p = _porte({T - timedelta(minutes=50): [10, 0, 0, 0, 0, 0]})
        e = P.analyser_porte("PORTE EST", p, T, {}, None, False, set(), avec_series=False)
        assert e["fermee"] and e["mode"] is None
        assert P.alertes([e]) == []

    def test_pda_en_renfort_des_tripodes(self):
        p = self._flux(50, 0, {"TRI-1": (40, 0, "tripode"), "PDA.12": (10, 0, "pda")})
        e = P.analyser_porte("PORTE EST", p, T, {}, None, True, set(), avec_series=False)
        assert e["pda_renfort"]
        # Le PDA d'une equipe mobile UAM n'est pas un debordement
        e = P.analyser_porte("PORTE EST", p, T, {}, None, True, {"PDA.12"}, avec_series=False)
        assert not e["pda_renfort"] and e["uam"] == ["PDA.12"]

    def test_n1_tous_sens_ne_prevoit_rien(self):
        p = self._flux(100, 0, {"TRI-1": (100, 0, "tripode")})
        n1 = {T - timedelta(minutes=15): [1000, 0], T: [3000, 0], T + timedelta(minutes=15): [3000, 0],
              T + timedelta(minutes=30): [3000, 0]}
        e = P.analyser_porte("PORTE EST", p, T, {}, n1, True, set(), avec_series=False)
        assert e["prevision"]["entree"]
        e = P.analyser_porte("PORTE EST", p, T, {}, n1, True, set(), avec_series=False,
                             n1_tous_sens=True)
        assert e["prevision"]["entree"] == [] and e["prevision"]["pire"] is None


class TestN1:
    def test_decalage_a_la_semaine(self):
        assert P.decalage_n1(datetime(2026, 5, 10, 14), datetime(2025, 5, 11, 14)) == 364
        assert P.decalage_n1(None, datetime(2025, 5, 11)) is None

    def test_debit_interpole_entre_milieux(self):
        q0, q1 = datetime(2026, 5, 10, 9, 0), datetime(2026, 5, 10, 9, 15)
        serie = {q0: [100, 0], q1: [200, 0]}
        # 9:15 = milieu exact entre 9:07:30 et 9:22:30 -> moyenne
        assert P._n1_rate(serie, datetime(2026, 5, 10, 9, 15), 0) == 600.0
