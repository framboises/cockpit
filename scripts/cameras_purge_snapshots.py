"""Purge les anciennes captures horodatees de uploads/camera_snapshots.

    python scripts/cameras_purge_snapshots.py            # a blanc : rapport seul
    python scripts/cameras_purge_snapshots.py --apply    # supprime

Jusqu'au 24/10/2026, chaque capture (fiche, capture de masse, bascule des
masques) ecrivait `<id>_ch<N>_<AAAAMMJJ>_<HHMMSS>.jpg` en plus de la vignette,
sans jamais le supprimer (~500 fichiers par capture de masse). Depuis, seules
les vignettes `<id>_latest.jpg` / `<id>_ch<N>_latest.jpg` sont ecrites : ce
script ne sert qu'une fois. Les vignettes ne sont JAMAIS touchees ; les
fichiers `.tmp` laisses par une capture interrompue sont supprimes aussi.
Les photos jointes aux fiches de la main courante sont ailleurs
(uploads/field_photos) et ne sont pas concernees.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(ROOT, "uploads", "camera_snapshots")
STAMPED_RE = re.compile(r"^[0-9a-f]{24}_ch\d+_\d{8}_\d{6}\.jpg$")
TMP_RE = re.compile(r"^[0-9a-f]{24}(_ch\d+)?_latest\.jpg\.tmp$")


def main():
    apply = "--apply" in sys.argv
    if not os.path.isdir(SNAP_DIR):
        print("Dossier absent : %s" % SNAP_DIR)
        return
    n, size = 0, 0
    for name in os.listdir(SNAP_DIR):
        if not (STAMPED_RE.match(name) or TMP_RE.match(name)):
            continue
        path = os.path.join(SNAP_DIR, name)
        n += 1
        size += os.path.getsize(path)
        if apply:
            os.remove(path)
    print("%d fichier(s), %.1f Mo %s" % (n, size / 1e6, "supprimes" if apply else "a supprimer (relancer avec --apply)"))


if __name__ == "__main__":
    main()
