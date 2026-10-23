"""Pre-remplit l'optique de chaque canal (mobile = PTZ, fixe) dans cockpit_cameras.

    python scripts/cameras_optics_prefill.py            # a blanc : rapport seul
    python scripts/cameras_optics_prefill.py --apply    # ecrit en base

- LECTURE SEULE cote cameras : seule la liste des presets du canal est lue
  (la meme que la fiche), une camera a la fois. Rien n'est jamais envoye.
- Cameras de piste (is_piste_device) : ni interrogees ni modifiees.
- Regle : presets de vue presents (y compris le seul preset 90 "Wiper" des
  Hik 2SF / 2DF) = "mobile" ; aucun preset = "fixe", sauf canal 1 d'un modele
  de dome (PTZ sans preset enregistre) = "mobile" d'apres le modele.
  Camera injoignable ou marque non pilotable : deduction du modele seul.
- optics_src : "presets" (lu sur la camera), "modele" (a verifier),
  "manuel" (choisi dans la fiche : JAMAIS ecrase). Une lecture "presets"
  n'est pas remplacee par une deduction "modele" (camera injoignable ce jour).

Necessite COCKPIT_CAMERA_KEY (compte de Cockpit) et TITAN_ENV=prod pour la base titan.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import cameras as C  # noqa: E402


def from_model(doc, ch, first):
    name = (ch.get("name") or "") + " " + (doc.get("name") or "")
    if "[F" in name or "interphone" in name.lower():
        return "fixe"
    return "mobile" if first and C._PTZ_MODEL_RE.search(doc.get("model") or "") else "fixe"


def read_optics(doc, ch, first):
    """-> (optique, origine, detail)"""
    if doc.get("brand") not in C.CONTROLLABLE_BRANDS or doc.get("enabled") is False:
        return from_model(doc, ch, first), "modele", "non interrogee"
    try:
        cam = C.make_hik_camera(doc, ch.get("no"))
        presets = C.call_camera(doc, lambda: C._view_presets(doc, cam))
    except requests.exceptions.ConnectionError:
        return from_model(doc, ch, first), "modele", "injoignable"
    except Exception as e:
        return from_model(doc, ch, first), "modele", "erreur %s" % type(e).__name__
    if presets:
        return "mobile", "presets", "%d preset(s)" % len(presets)
    if from_model(doc, ch, first) == "mobile":
        return "mobile", "modele", "dome sans preset"
    return "fixe", "presets", "aucun preset"


def main():
    apply = "--apply" in sys.argv
    C._ensure_db()
    stats = collections.Counter()
    for doc in C._col_cameras.find().sort("name", 1):
        if C.is_piste_device(doc):
            stats["piste (ignoree)"] += 1
            continue
        chans = doc.get("channels")
        if not isinstance(chans, list) or not chans:
            stats["sans liste de canaux"] += 1
            print("  ? %s : pas de liste de canaux" % doc.get("name"))
            continue
        for i, ch in enumerate(chans):
            no = ch.get("no", i + 1)
            if ch.get("optics_src") == "manuel":
                stats["manuel (conserve)"] += 1
                continue
            optics, src, detail = read_optics(doc, ch, i == 0)
            if src == "modele" and ch.get("optics_src") == "presets":
                stats["lecture precedente conservee"] += 1
                continue
            stats["%s (%s)" % (optics, src)] += 1
            if ch.get("optics") == optics and ch.get("optics_src") == src:
                continue
            print("  %-45s ch%-2s %-6s %-7s %s%s" % ((doc.get("name") or "")[:45], no, optics, src, detail,
                                                    "" if not ch.get("optics") else " (avant : %s)" % ch.get("optics")))
            if apply:
                C._col_cameras.update_one({"_id": doc["_id"], "channels.%d.no" % i: no},
                                          {"$set": {"channels.%d.optics" % i: optics, "channels.%d.optics_src" % i: src}})
                stats["ecrits"] += 1
    print()
    for k, v in sorted(stats.items()):
        print("%-32s %d" % (k, v))
    if not apply:
        print("\nA blanc : rien n'est ecrit (--apply pour enregistrer).")


if __name__ == "__main__":
    main()
