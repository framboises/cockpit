# replay_pcorg_digest.py -- rejoue deux versions des consignes de pcorg_digest
# sur les fiches controlees (/admin/gravite-fiches), SANS rien ecrire en base.
#
# Regle (docs/claude/patrouilles.md 6.0) : le modele varie d'un passage a
# l'autre, on rejoue TOUJOURS l'ancienne ET la nouvelle consigne avant de
# changer de version.
#
#   git show HEAD:pcorg_digest.py > %TEMP%\pcorg_digest_old.py
#   python scripts/replay_pcorg_digest.py --old %TEMP%\pcorg_digest_old.py [--limit 40]
#
# Mesure : gravite juste (= gravite validee), ecart moyen, et pour les resumes
# la part de phrases affirmant une issue non ecrite (pcorg_enrich.unsupported_claims).
import argparse
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import pcorg_digest as NEW  # noqa: E402
import pcorg_enrich as PE  # noqa: E402


def _load(path):
    spec = importlib.util.spec_from_file_location("pcorg_digest_old", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--old", required=True, help="chemin de l'ancienne version de pcorg_digest.py")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", help="fichier JSON du detail")
    p.add_argument("--skip-old", action="store_true",
                   help="ne rejoue que la nouvelle version (ancienne deja mesuree dans ce meme rejeu)")
    a = p.parse_args()
    old = _load(a.old)
    db = NEW._db()
    sc = NEW.load_sous_classifications(db)
    revs = [d for d in db[NEW.COLL].find({"review": {"$ne": None}}, {"review": 1})
            if (d["review"] or {}).get("verdict") != "ignoree" and (d["review"] or {}).get("gravite_validee")]
    if a.limit:
        revs = revs[:a.limit]
    sess = requests.Session()
    stats = {v: {"n": 0, "ok": 0, "ecart": 0, "claims": 0, "err": 0} for v in ("old", "new")}
    detail = []
    for i, d in enumerate(revs, 1):
        fiche = db["pcorg"].find_one({"_id": d["_id"]}, NEW.SOURCE_PROJECTION)
        if not fiche:
            continue
        valid = d["review"]["gravite_validee"]
        row = {"id": d["_id"], "valid": valid, "titre": fiche.get("text")}
        for key, mod in ((("new", NEW),) if a.skip_old else (("old", old), ("new", NEW))):
            st = stats[key]
            try:
                res, _ = mod.analyze(fiche, sess, sc)
            except Exception as e:  # reponse invalide, delai
                st["err"] += 1
                row[key] = {"error": str(e)[:120]}
                continue
            st["n"] += 1
            st["ok"] += res["gravite"] == valid
            st["ecart"] += abs(res["gravite"] - valid)
            bad = PE.unsupported_claims(res.get("resume"), PE.guard_source(fiche))
            st["claims"] += bool(bad)
            row[key] = {"gravite": res["gravite"], "resume": res.get("resume"), "claims": bad,
                        "famille": res.get("famille"), "titre": res.get("titre")}
        detail.append(row)
        print("%3d/%d %s valide %s | ancien %s | nouveau %s" % (
            i, len(revs), d["_id"][:8], valid, (row.get("old") or {}).get("gravite"),
            (row.get("new") or {}).get("gravite")), flush=True)
    for key, st in stats.items():
        n = st["n"] or 1
        print("%-6s %s : %d/%d justes (%.0f %%), ecart moyen %.2f, resumes avec issue non ecrite %d, echecs %d" % (
            key, (old if key == "old" else NEW).PROMPT_VERSION, st["ok"], st["n"], 100.0 * st["ok"] / n,
            st["ecart"] / n, st["claims"], st["err"]))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"stats": stats, "detail": detail}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
