"""Diag rapide pour comprendre pourquoi door_reinforcement retourne None.

Lance sur la prod :
    E:\\TITAN\\production\\titan_prod\\Scripts\\python.exe scripts\\diag_doors.py
"""
import os
import sys
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Le script vit dans scripts/, on ajoute le parent (racine cockpit) au PYTHONPATH
# pour pouvoir importer les modules pcorg_summary et pcorg_doors_analysis.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pymongo import MongoClient

import pcorg_summary as ps

EVENT = "24H MOTOS"
YEAR = 2026
AS_OF = datetime(2026, 4, 18, 7, 0, tzinfo=ZoneInfo("Europe/Paris")).astimezone(timezone.utc)


def main():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    name = os.getenv("MONGO_DB", "titan")
    print("MONGO_URI=", uri)
    print("MONGO_DB =", name)
    db = MongoClient(uri)[name]

    print("\n[1] Race date N (2026) :", ps._load_race_dt(db, EVENT, YEAR))
    print("[2] Race date N-1 (2025) :", ps._load_race_dt(db, EVENT, YEAR - 1))

    print("\n[3] Doc historique_controle type=portes pour 24H MOTOS 2025 :")
    for y in (2025, "2025"):
        d = db["historique_controle"].find_one(
            {"type": "portes", "event": EVENT, "year": y},
            {"race": 1, "_id": 1},
        )
        print(f"   year={y!r}: {'EXISTE id=' + str(d['_id']) if d else 'absent'}")

    print("\n[4] Tentative compute_door_reinforcement...")
    try:
        import pcorg_doors_analysis as pda
        res = pda.compute_door_reinforcement(db, EVENT, YEAR, now_utc=AS_OF)
        if res is None:
            print("   -> None (pas de recos generees ou prerequis manquant)")
        else:
            print(f"   -> OK : {len(res['recommendations'])} recos, "
                  f"{len(res['families'])} familles, "
                  f"prev_year={res.get('year_prev')}")
            for r in res["recommendations"][:5]:
                print(f"      {r['family_label']:25} {r['slot_label_n']:20} "
                      f"{r['criticite']:7} pic_n1:{r['n1_scan_count']:>5} "
                      f"fiches:{r['n1_fiches_count']}")
    except Exception:
        print("   !! Exception levee :")
        traceback.print_exc()


if __name__ == "__main__":
    main()
