"""Candidats fictifs du module Formation (recette sur dev).

    python scripts/formation_fictifs.py creer       # 5 candidats fictifs
    python scripts/formation_fictifs.py supprimer   # les efface, avec leurs tentatives et envois

Identification sur /test-pc-organisation : candidatN@test.invalid + 4 derniers
chiffres du mobile (0001 a 0005). `candidat5` a l'amenagement de temps. Les
fictifs sont exclus des envois de campagne, des statistiques et des exports.
Base : formation.store.get_db() (cockpit_formation_dev sauf TITAN_ENV=prod),
jamais titan / titan_dev.
"""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formation import store  # noqa: E402


def _fictifs():
    out = []
    for n in range(1, 6):
        tel = "060000000%d" % n
        out.append({"forms_id": 900000 + n, "nom": "Fictif%d" % n, "prenom": "Candidat",
                    "email": "candidat%d@test.invalid" % n, "tel": tel, "tel4": tel[-4:],
                    "amenagement": n == 5})
    return out


def creer(db, now=None):
    """Cree (ou remet a jour) les 5 fictifs ; renvoie le nombre de crees."""
    now = now or datetime.now(timezone.utc)
    crees = 0
    for c in _fictifs():
        res = db["candidats"].update_one(
            {"campagne": store.CAMPAGNE, "email": c["email"]},
            {"$set": dict(c, fictif=True, forms_repondu_le=now, maj_le=now),
             "$setOnInsert": {"importe_le": now}},
            upsert=True)
        crees += 1 if res.upserted_id else 0
    return crees


def supprimer(db):
    """Efface les fictifs et leurs tentatives et envois, rien d'autre. Renvoie les comptes."""
    filtre = {"campagne": store.CAMPAGNE, "fictif": True}
    ids = [str(d["_id"]) for d in db["candidats"].find(filtre, {"_id": 1})]
    par_id = {"campagne": store.CAMPAGNE, "candidat_id": {"$in": ids}}
    return {
        "tentatives": db["tentatives"].delete_many(par_id).deleted_count,
        "envois": db["envois"].delete_many(par_id).deleted_count,
        "candidats": db["candidats"].delete_many(filtre).deleted_count,
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("creer", "supprimer"):
        print(__doc__)
        return 2
    db = store.get_db()
    print("Base : %s" % db.name)
    if argv[0] == "creer":
        store.ensure_indexes(db)
        print("%d candidat(s) fictif(s) cree(s)." % creer(db))
    else:
        print("Supprime : %s" % supprimer(db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
