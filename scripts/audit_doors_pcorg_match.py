"""Audit read-only du matching texte porte <-> fiche pcorg.

Sert a decider quelle strategie de matching adopter pour le module
pcorg_doors_analysis (prediction de renforts par porte).

Lit MongoDB titan_dev (override via MONGO_URI / MONGO_DB), n'ecrit rien.

Usage :
    python scripts/audit_doors_pcorg_match.py
        --event "24H AUTOS" --year 2024
        --event "24H MOTOS" --year 2024
        --event "GPF" --year 2024

    Si aucun --event passe : essaie les couples par defaut.

Pour chaque (event, year), compare 2 SOURCES de texte cote fiche :
  A  area.desc seul (ancien comportement, hypothese de zone structuree)
  B  full text = area.desc + text + text_full + comment + comment_history[].text
     (l'intuition : les operateurs citent le nom de la porte dans les
     commentaires libres en cours d'intervention)

Et 3 STRATEGIES de matching :
  S1  substring case-insensitive
  S2  tokens normalises (NFD strip accents) avec >=1 token significatif >3 chars
  S3  fuzzy difflib SequenceMatcher >= 0.7

Critere de decision : >=60% de couverture.
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from pymongo import MongoClient


DEFAULT_TARGETS = [
    ("24H AUTOS", 2024),
    ("24H AUTOS", 2025),
    ("24H MOTOS", 2024),
    ("24H MOTOS", 2025),
    ("GPF", 2024),
    ("GPF", 2025),
]

CATEGORIES = ["PCO.Flux", "PCO.Securite", "PCO.Information", "PCO.MainCourante"]
STOPWORDS = {
    "porte", "portail", "acces", "access", "tribune", "zone", "secteur",
    "entree", "sortie", "pcs", "pco", "site", "circuit", "parking",
    "vehic", "vehicule", "vehicules", "pieton", "pietons",
    "control", "controle", "controlle", "controles",
    "le", "la", "les", "du", "de", "des", "un", "une", "et", "ou",
    "au", "aux", "vers", "sur", "dans", "pour", "avec",
    # NB: directions (nord/sud/est/ouest) NON incluses : sur du full
    # texte les operateurs citent souvent "porte nord" -> on veut matcher.
    # Le filtre min 4 chars protege deja contre des faux positifs courts.
}

FUZZY_THRESHOLD = 0.7
MIN_TOKEN_LEN = 4


# ---------- Normalisation ----------

def strip_accents(s: str) -> str:
    if not s:
        return ""
    nf = unicodedata.normalize("NFD", s)
    return "".join(c for c in nf if unicodedata.category(c) != "Mn")


def normalize(s: str) -> str:
    """lowercase + strip accents + collapse non-alnum to spaces + trim."""
    if not s:
        return ""
    out = strip_accents(s).lower()
    out = re.sub(r"[^a-z0-9]+", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def tokens(s: str) -> set[str]:
    n = normalize(s)
    if not n:
        return set()
    return {t for t in n.split() if len(t) >= MIN_TOKEN_LEN and t not in STOPWORDS}


# ---------- Strategies de matching ----------

def s1_substring(door_name: str, area_desc: str) -> bool:
    a = (door_name or "").lower().strip()
    b = (area_desc or "").lower().strip()
    if not a or not b:
        return False
    return a in b or b in a


def s2_tokens(door_name: str, area_desc: str) -> bool:
    ta = tokens(door_name)
    tb = tokens(area_desc)
    if not ta or not tb:
        return False
    return bool(ta & tb)


def s3_fuzzy(door_name: str, area_desc: str) -> bool:
    a = normalize(door_name)
    b = normalize(area_desc)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_THRESHOLD


STRATEGIES = [("S1 substring", s1_substring), ("S2 tokens", s2_tokens), ("S3 fuzzy>=0.7", s3_fuzzy)]


# ---------- Mongo ----------

def connect_db():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    name = os.getenv("MONGO_DB", "titan_dev")
    print(f"[INFO] Connexion : {uri}  (db={name})")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client[name], client


def load_doors(db, event: str, year: int) -> list[str]:
    """Retourne la liste deduplicate des noms de portes pour un event/year."""
    doc = db["historique_controle"].find_one({"type": "portes", "event": event, "year": year})
    if not doc:
        # Year peut etre stocke en string
        doc = db["historique_controle"].find_one({"type": "portes", "event": event, "year": str(year)})
    if not doc:
        return []
    names = []
    for d in doc.get("doors") or []:
        n = (d.get("name") or "").strip()
        if n:
            names.append(n)
    # Dedupe en preservant ordre
    seen = set()
    out = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


MAX_BLOB_CHARS = 10000


def _build_full_blob(doc) -> str:
    """Concatene tous les champs textuels exploitables d'une fiche.
    Tronque a 10k chars pour eviter les fiches monstres.
    """
    pieces = []
    area = doc.get("area") or {}
    if area.get("desc"):
        pieces.append(str(area["desc"]))
    for k in ("text", "text_full", "comment"):
        v = doc.get(k)
        if v:
            pieces.append(str(v))
    history = doc.get("comment_history") or []
    if isinstance(history, list):
        for h in history:
            if isinstance(h, dict) and h.get("text"):
                pieces.append(str(h["text"]))
    blob = "\n".join(pieces)
    if len(blob) > MAX_BLOB_CHARS:
        blob = blob[:MAX_BLOB_CHARS]
    return blob


def load_fiches(db, event: str, year: int) -> list[dict]:
    """Retourne la liste des fiches pcorg (categories cibles) avec un
    blob texte complet pour le matching.

    Chaque fiche est repsentee par {id, area_desc, blob}.
    """
    base = {
        "event": event,
        "year": year,
        "category": {"$in": CATEGORIES},
    }
    proj = {"_id": 1, "area": 1, "text": 1, "text_full": 1,
            "comment": 1, "comment_history": 1}
    docs = list(db["pcorg"].find(base, proj))
    if not docs:
        # Year peut etre stocke en string
        base["year"] = str(year)
        docs = list(db["pcorg"].find(base, proj))
    out = []
    for d in docs:
        out.append({
            "id": str(d.get("_id", "")),
            "area_desc": (d.get("area") or {}).get("desc", "") or "",
            "blob": _build_full_blob(d),
        })
    return out


# ---------- Audit ----------

def _evaluate(doors, fiches, source_field, strat_fn):
    """Pour une source ('area_desc' ou 'blob') et une strategie donnee,
    retourne (matches_by_door, fiches_matched_count).

    matches_by_door : dict[door_name] -> list de fiches matchees (id, snippet)
    fiches_matched_count : nb de fiches associees a >=1 porte
    """
    matches_by_door = defaultdict(list)
    fiches_matched = set()
    for f in fiches:
        text = f.get(source_field) or ""
        matched = [d for d in doors if strat_fn(d, text)]
        if matched:
            fiches_matched.add(f["id"])
        for d in matched:
            matches_by_door[d].append(f)
    return matches_by_door, len(fiches_matched)


def _snippet(text, door, max_chars=120):
    """Extrait court autour de la 1ere occurrence du nom de porte."""
    if not text:
        return ""
    n = normalize(text)
    nd = normalize(door)
    idx = n.find(nd)
    if idx == -1:
        # Fallback : retourne juste le debut
        return text[:max_chars].replace("\n", " ")
    # On localise dans le texte original (approx)
    half = max_chars // 2
    start = max(0, idx - half)
    end = min(len(text), idx + len(door) + half)
    return ("..." if start > 0 else "") + text[start:end].replace("\n", " ") + ("..." if end < len(text) else "")


def audit_event(db, event: str, year: int) -> dict:
    print()
    print("=" * 78)
    print(f" AUDIT  event={event}  year={year}")
    print("=" * 78)

    doors = load_doors(db, event, year)
    fiches = load_fiches(db, event, year)
    total_fiches = len(fiches)

    print(f"  Portes (historique_controle.type=portes) : {len(doors)}")
    print(f"  Fiches PCO.Flux/Securite/Information/MainCourante : {total_fiches}")

    if not doors:
        print("  [SKIP] Pas de portes pour cet event/year.")
        return {"event": event, "year": year, "skipped": True}
    if not fiches:
        print("  [SKIP] Pas de fiches pour cet event/year.")
        return {"event": event, "year": year, "skipped": True}

    sources = [("A area.desc", "area_desc"), ("B full_text", "blob")]
    results = {}
    for src_name, src_field in sources:
        results[src_name] = {}
        for strat_name, strat_fn in STRATEGIES:
            mbd, n_matched = _evaluate(doors, fiches, src_field, strat_fn)
            pct = (100.0 * n_matched / total_fiches) if total_fiches else 0.0
            results[src_name][strat_name] = {
                "matches_by_door": mbd,
                "fiches_count_covered": n_matched,
                "coverage_pct": pct,
            }

    # Tableau resume couverture
    print()
    print(f"  Couverture par (source, strategie) :")
    print(f"  {'Source':<14}  {'Strategie':<16}  {'Couverture':>20}  {'Verdict'}")
    for src_name, _ in sources:
        for strat_name, _ in STRATEGIES:
            r = results[src_name][strat_name]
            verdict = "OK >=60%" if r["coverage_pct"] >= 60 else ""
            print(f"  {src_name:<14}  {strat_name:<16}  "
                  f"{r['fiches_count_covered']:>5} / {total_fiches:<5} "
                  f"({r['coverage_pct']:5.1f}%)   {verdict}")

    # Detail : pour la combinaison la plus prometteuse (B + S2 par defaut),
    # on liste les portes avec leur nb de fiches et 2 exemples de match.
    best_src, best_strat = "B full_text", "S2 tokens"
    print()
    print(f"  Detail meilleure combo : {best_src} + {best_strat}")
    mbd = results[best_src][best_strat]["matches_by_door"]
    for d in doors:
        flist = mbd.get(d, [])
        print(f"    [{len(flist):>4}]  {d}")
        for f in flist[:2]:
            blob = f.get("blob") or ""
            sn = _snippet(blob, d)
            print(f"            id={f['id'][:8]}  : {sn}")
        if len(flist) > 2:
            print(f"            ... +{len(flist) - 2} autre(s)")

    # Echantillon de fiches NON matchees par (B + S2) -> a inspecter
    matched_set = set()
    for d, flist in mbd.items():
        for f in flist:
            matched_set.add(f["id"])
    unmatched = [f for f in fiches if f["id"] not in matched_set]
    if unmatched:
        print()
        print(f"  Echantillon fiches NON matchees ({len(unmatched)}) - 5 premieres :")
        for f in unmatched[:5]:
            blob = (f.get("blob") or "")[:240].replace("\n", " | ")
            print(f"    id={f['id'][:8]}  area={f['area_desc'][:40]!r}  "
                  f"blob={blob[:200]!r}")

    return {
        "event": event, "year": year, "skipped": False,
        "n_doors": len(doors), "total_fiches": total_fiches,
        "results": {
            src_name: {
                strat_name: {
                    "coverage_pct": results[src_name][strat_name]["coverage_pct"],
                    "fiches_count_covered": results[src_name][strat_name]["fiches_count_covered"],
                }
                for strat_name, _ in STRATEGIES
            }
            for src_name, _ in sources
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit matching porte <-> fiche pcorg")
    parser.add_argument("--event", action="append", default=[], help="Nom event (repetable)")
    parser.add_argument("--year", action="append", default=[], help="Annee (repetable, paire avec --event dans l'ordre)")
    args = parser.parse_args(argv)

    if args.event and args.year and len(args.event) == len(args.year):
        try:
            targets = list(zip(args.event, [int(y) for y in args.year]))
        except ValueError:
            print("[ERREUR] --year doit etre un entier", file=sys.stderr)
            return 2
    elif args.event or args.year:
        print("[ERREUR] --event et --year doivent etre fournis en paires de meme longueur", file=sys.stderr)
        return 2
    else:
        targets = DEFAULT_TARGETS
        print(f"[INFO] Aucun event specifie, utilisation des defauts : {targets}")

    db, client = connect_db()
    try:
        try:
            client.admin.command("ping")
        except Exception as e:
            print(f"[ERREUR] MongoDB injoignable : {e}", file=sys.stderr)
            return 3

        all_results = []
        for ev, yr in targets:
            res = audit_event(db, ev, yr)
            all_results.append(res)

        # Tableau global
        considered = [r for r in all_results if not r.get("skipped")]
        if not considered:
            print("\nAucune donnee a analyser. Verifier MONGO_URI/MONGO_DB et la presence de "
                  "historique_controle type=portes / fiches pcorg pour les events demandes.")
            return 0

        print()
        print("=" * 78)
        print(" SYNTHESE GLOBALE (couverture ponderee par volume de fiches)")
        print("=" * 78)
        total = sum(r["total_fiches"] for r in considered)
        print(f"  {'Source':<14}  {'Strategie':<16}  {'Couverture':>20}  {'Verdict'}")
        for src_name in ["A area.desc", "B full_text"]:
            for strat_name, _ in STRATEGIES:
                covered = sum(
                    r["results"][src_name][strat_name]["fiches_count_covered"]
                    for r in considered
                )
                pct = (100.0 * covered / total) if total else 0.0
                verdict = "OK >=60%" if pct >= 60 else "INSUFFISANT"
                print(f"  {src_name:<14}  {strat_name:<16}  "
                      f"{covered:>5} / {total:<5} ({pct:5.1f}%)   {verdict}")
        print()
        print("Critere : adopter la combinaison la plus simple qui depasse 60%.")
        print("Si aucune ne passe, Phase 0.5 = dictionnaire d'alias zone -> portes.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
