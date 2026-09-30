"""Tests de pcorg_history : parsing Prysm, fusion SQL <-> Cockpit, chronologie."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pcorg_history as H  # noqa: E402

# Formes reelles (fiches 46330 et 46282, 27/09/2026)
C_46330 = (
    "27/09/2026 14:41:36 , PELLEMOINE Jerome \n  Appel nous signale une fuite d 'eau du niveau du S 20  Cam 031 \r\n"
    "Appel ST ACO  se rend sur place.\n27/09/2026 15:00:27 , PELLEMOINE Jerome \n  Arrivee du service technique sur place.\n"
    "27/09/2026 15:22:53 , PELLEMOINE Jerome \n Statut: En cours -> Terminé\n \n"
    "27/09/2026 16:03:43 , FOURMI Arnaud \n  \n27/09/2026 16:49:34 , PELLEMOINE Jerome \n  16h50 Depart du service technique ."
)
C_46282 = (
    "27/09/2026 05:38:05 , LEBERT Pierre \n  Barrieres tombees entre le rond point technoparc et beausejour. \r\n"
    "Demande aux appuis flux de les remonter\n27/09/2026 05:38:10 , LEBERT Pierre"
)


def test_parse_en_tete_final_sans_saut_de_ligne():
    entries = H.parse_comment(C_46282)
    assert len(entries) == 2  # l'ancienne regex perdait la derniere entree
    assert entries[1]["operator"] == "LEBERT Pierre"
    assert entries[1]["text"] == ""
    assert all(e["origin"] == "sql" for e in entries)


def test_parse_date_citee_dans_le_texte_ne_coupe_pas():
    c = "27/09/2026 10:00:00 , A B\n rdv le 12/10/2026 avec le chef\n27/09/2026 11:00:00 , C D\n ok"
    entries = H.parse_comment(c)
    assert [e["text"] for e in entries] == ["rdv le 12/10/2026 avec le chef", "ok"]


def test_parse_en_tete_colle_au_texte():
    c = "04/04/2026 16:51:37 , LASNIER Nicolas \n  hors du paddock04/04/2026 22:33:00 , \n Ouvert trop longtemps\n"
    entries = H.parse_comment(c)
    assert len(entries) == 2
    assert entries[1]["text"] == "Ouvert trop longtemps"


def test_decorate_statut_et_commentaire_separes():
    e = H.decorate_entry({"ts": "2026-09-27T15:22:53+02:00", "operator": "X",
                          "text": "Statut: En cours -> Terminé\n11h46 Fermeture du TGBT"})
    assert e["kind"] == "status"
    assert e["status_from"] == "En cours" and e["status_to"] == "Terminé"
    assert e["body"] == "11h46 Fermeture du TGBT"


def test_decorate_modifications_prysm():
    e = H.decorate_entry({"ts": "2026-09-27T15:22:53+02:00", "operator": "X",
                          "text": "Statut: En cours -> Termine\nTexte: a  -> b\nDescription  -> Parking"})
    assert e["status_to"] == "Terminé"
    assert e["changes"] == [{"field": "Texte", "old": "a", "new": "b"},
                            {"field": "Description", "old": "", "new": "Parking"}]
    assert e["body"] == ""


def test_decorate_texte_libre_a_espaces_reste_commentaire():
    e = H.decorate_entry({"ts": None, "operator": "X", "text": "Patrouille 4: renfort -> Patrouille 1"})
    assert e["kind"] == "comment"


def test_decorate_entree_vide_et_cloture_synthetique():
    doc = {"status_code": 10, "close_ts": datetime(2026, 9, 27, 3, 38, 5), "operator_close": "LEBERT Pierre"}
    items = H.decorate_history(H.parse_comment(C_46282), doc)
    kinds = [i["kind"] for i in items]
    assert "empty" in kinds
    synth = [i for i in items if i.get("synthetic")]
    assert len(synth) == 1
    assert synth[0]["operator"] == "LEBERT Pierre"
    assert synth[0]["ts"] == "2026-09-27T05:38:05+02:00"  # close_ts BSON (UTC) -> Paris


def test_decorate_pas_de_synthese_si_statut_present():
    doc = {"status_code": 10, "close_ts": datetime(2026, 9, 27, 13, 22, 53), "operator_close": "PELLEMOINE Jerome"}
    items = H.decorate_history(H.parse_comment(C_46330), doc)
    assert not [i for i in items if i.get("synthetic")]
    assert [i["kind"] for i in items] == ["comment", "comment", "status", "empty", "comment"]


def test_timestamps_heterogenes_normalises():
    hist = [
        {"ts": datetime(2026, 9, 27, 12, 0, 0), "operator": "field:V1", "text": "a"},          # BSON UTC
        {"ts": "2026-09-27T12:30:00+00:00", "operator": "field:V1", "text": "b"},              # ISO UTC
        {"ts": "2026-09-27T14:15:00.123456+02:00", "operator": "Jean", "text": "c"},           # ISO Paris
    ]
    items = H.decorate_history(hist, {"status_code": 0})
    assert [i["ts"] for i in items] == [
        "2026-09-27T14:00:00+02:00", "2026-09-27T14:15:00+02:00", "2026-09-27T14:30:00+02:00"]


# --- Fusion synchro ---------------------------------------------------------

def _sql_doc(comment, status=0, close_ts=None, **extra):
    d = {
        "_id": "x", "comment": comment, "comment_history": H.parse_comment(comment),
        "status_code": status, "close_ts": close_ts, "close_iso": None,
        "operator_close": None, "operator_id_close": None,
        "niveau_urgence": "UR", "gps": None, "text": "texte SQL",
        "content_category": {"sous_classification": "Fuite", "carroye": "AB12"},
    }
    d.update(extra)
    return d


def test_fusion_conserve_commentaires_cockpit_et_tablette():
    base = "27/09/2026 14:41:36 , PELLEMOINE Jerome \n  Appel fuite\n"
    existing = _sql_doc(base)
    existing["comment_history"] += [
        {"ts": "2026-09-27T14:50:12.345678+02:00", "operator": "Jean Dupont", "text": "note cockpit"},
        {"ts": datetime(2026, 9, 27, 12, 55), "operator": "field:V1", "text": "ASL", "photo": "/p.jpg"},
    ]
    new = _sql_doc(base + "27/09/2026 15:00:27 , PELLEMOINE Jerome \n  Arrivee ST\n")
    merged = H.merge_sync_doc(new, existing)
    texts = [e["text"] for e in merged["comment_history"]]
    assert texts == ["Appel fuite", "note cockpit", "ASL", "Arrivee ST"]  # ordre chronologique
    assert "note cockpit" in merged["comment"]
    assert merged["comment_sql"].endswith("Arrivee ST\n")
    origins = {e["text"]: e["origin"] for e in merged["comment_history"]}
    assert origins["note cockpit"] == "cockpit" and origins["ASL"] == "field"


def test_fusion_idempotente():
    base = "27/09/2026 14:41:36 , A \n  x\n"
    existing = _sql_doc(base)
    existing["comment_history"].append(H.make_entry("Jean", "note"))
    m1 = H.merge_sync_doc(_sql_doc(base), existing)
    m2 = H.merge_sync_doc(_sql_doc(base), m1)
    assert [e["text"] for e in m1["comment_history"]] == [e["text"] for e in m2["comment_history"]]


def test_fusion_ancienne_fiche_ligne_cockpit_deja_dans_comment():
    # Avant ce module, Cockpit ajoutait sa ligne au champ comment ET a l'historique
    existing = _sql_doc("27/09/2026 14:41:36 , A \n  x\n27/09/2026 14:50:12 , Jean Dupont\n note\n")
    existing["comment_history"][-1] = {"ts": "2026-09-27T14:50:12.345678+02:00", "operator": "Jean Dupont",
                                       "text": "note", "photo": "/p.jpg"}
    new = _sql_doc("27/09/2026 14:41:36 , A \n  x\n27/09/2026 14:50:12 , Jean Dupont\n note\n")
    merged = H.merge_sync_doc(new, existing)
    assert len(merged["comment_history"]) == 2          # pas de doublon
    assert merged["comment_history"][1].get("photo") == "/p.jpg"  # la locale l'emporte


def test_cloture_cockpit_non_annulee_par_la_synchro():
    close = datetime(2026, 9, 27, 13, 0, tzinfo=timezone.utc)
    existing = _sql_doc("", status=10, close_ts=close, operator_close="Jean", operator_id_close="j@x",
                        cockpit_owned=["status"], cockpit_status_at=close)
    merged = H.merge_sync_doc(_sql_doc("", status=5), existing)
    assert merged["status_code"] == 10 and merged["operator_close"] == "Jean"
    assert merged["cockpit_owned"] == ["status"]


def test_cloture_sql_posterieure_a_une_reouverture_cockpit():
    reopened_at = datetime(2026, 9, 27, 13, 0, tzinfo=timezone.utc)
    existing = _sql_doc("", status=0, cockpit_owned=["status"], cockpit_status_at=reopened_at)
    later = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)
    merged = H.merge_sync_doc(_sql_doc("", status=10, close_ts=later, operator_close="SQL"), existing)
    assert merged["status_code"] == 10 and merged["operator_close"] == "SQL"
    assert "cockpit_owned" not in merged or "status" not in merged["cockpit_owned"]


def test_champs_possedes_par_cockpit():
    existing = _sql_doc("", niveau_urgence="UA", gps={"type": "Point", "coordinates": [0.2, 47.9]},
                        cockpit_owned=["niveau_urgence", "gps", "content_category.patrouille", "area.desc"],
                        area={"id": 3, "desc": "Zone Cockpit"})
    existing["content_category"] = {"sous_classification": "Fuite", "patrouille": "V1"}
    new = _sql_doc("", area={"id": 3, "desc": "Zone SQL"})
    merged = H.merge_sync_doc(new, existing)
    assert merged["niveau_urgence"] == "UA"
    assert merged["gps"]["coordinates"] == [0.2, 47.9]
    assert merged["content_category"]["patrouille"] == "V1"
    assert merged["content_category"]["carroye"] == "AB12"   # SQL garde le reste
    assert merged["area"] == {"id": 3, "desc": "Zone Cockpit"}
    assert merged["text"] == "texte SQL"


def test_nouvelle_fiche_sql():
    new = _sql_doc("27/09/2026 14:41:36 , A \n  x\n")
    merged = H.merge_sync_doc(new, None)
    assert merged["comment_sql"] == new["comment"]
    assert "cockpit_owned" not in merged


# --- Ecriture atomique (MongoDB local, base de dev) --------------------------

@pytest.fixture
def col():
    pymongo = pytest.importorskip("pymongo")
    try:
        client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)
        client.server_info()
    except Exception:
        pytest.skip("MongoDB indisponible")
    c = client["titan_dev"]["_test_pcorg_history"]
    c.drop()
    yield c
    c.drop()


def test_append_entry_atomique(col):
    col.insert_one({"_id": "f1", "comment": "01/01/2026 10:00:00 , A\n x\n", "comment_history": [], "status_code": 0})
    e = H.make_entry("Jean", "suivi", photo="/p.jpg")
    assert H.append_entry(col, "f1", e, set_fields={"niveau_urgence": "UA"}, owned={"niveau_urgence"},
                          inc_bounce=True) == 1
    d = col.find_one({"_id": "f1"})
    assert d["comment"].startswith("01/01/2026 10:00:00 , A\n x\n")
    assert d["comment"].rstrip().endswith("suivi")
    assert d["comment_history"][-1]["photo"] == "/p.jpg"
    assert d["niveau_urgence"] == "UA" and d["cockpit_owned"] == ["niveau_urgence"]
    assert d["bounce_rev"] == 1 and d["cockpit_rev"] == 1


def test_append_entry_filtre_cloture(col):
    col.insert_one({"_id": "f2", "status_code": 10})
    n = H.append_entry(col, "f2", H.make_entry("J", "x"), extra_filter={"status_code": {"$ne": 10}})
    assert n == 0
    assert "comment_history" not in col.find_one({"_id": "f2"})


def test_append_entry_chemin_pointe_sur_champ_nul(col):
    col.insert_one({"_id": "f3", "content_category": None})
    H.append_entry(col, "f3", H.make_entry("J", "lib"), set_fields={"content_category.patrouille": ""})
    assert col.find_one({"_id": "f3"})["content_category"] == {"patrouille": ""}
