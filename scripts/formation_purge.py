"""Purge d'une campagne du module Formation (suppression prevue le 30/01/2027).

    python scripts/formation_purge.py --campagne pcorg-2026-12              # a blanc : comptes seuls
    python scripts/formation_purge.py --campagne pcorg-2026-12 --confirmer  # supprime

Supprime pour la campagne : candidats, tentatives, envois, imports,
essais_connexion, journal_admin et les reglages. Sans --confirmer, rien n'est
supprime. Base : formation.store.get_db() (cockpit_formation_dev sauf
TITAN_ENV=prod), jamais titan / titan_dev. Faire d'abord une sauvegarde
de la base si un export des resultats est encore attendu.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formation import store  # noqa: E402

COLLECTIONS = ("candidats", "tentatives", "envois", "imports", "essais_connexion", "journal_admin")


def _filtres(campagne):
    f = {c: {"campagne": campagne} for c in COLLECTIONS}
    f["reglages"] = {"_id": campagne}
    return f


def compter(db, campagne):
    return {c: db[c].count_documents(f) for c, f in _filtres(campagne).items()}


def purger(db, campagne, confirmer=False):
    """Comptes par collection ; supprime seulement si `confirmer` est vrai."""
    comptes = compter(db, campagne)
    if confirmer:
        for c, f in _filtres(campagne).items():
            db[c].delete_many(f)
    return comptes


def main(argv=None):
    p = argparse.ArgumentParser(description="Purge d'une campagne du module Formation.")
    p.add_argument("--campagne", required=True)
    p.add_argument("--confirmer", action="store_true", help="supprime reellement")
    a = p.parse_args(argv)
    db = store.get_db()
    print("Base : %s, campagne : %s" % (db.name, a.campagne))
    comptes = purger(db, a.campagne, a.confirmer)
    for c, n in comptes.items():
        print("  %-18s %d" % (c, n))
    if a.confirmer:
        print("Supprime.")
    else:
        print("A blanc : rien n'a ete supprime. Relancer avec --confirmer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
