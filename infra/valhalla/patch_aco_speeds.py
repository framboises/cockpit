#!/usr/bin/env python3
"""
patch_aco_speeds.py

Patche un PBF OSM clippe pour le circuit ACO du Mans en forcant maxspeed=40
sur tous les ways highway=service. Sans ce patch, Valhalla applique ses
vitesses defaut (7 km/h pour service=parking_aisle, 25 km/h pour
service_road) sur toute la voirie interne du circuit, ce qui donne des
ETA 2-3x plus longs que la realite operationnelle.

Le patch preserve les maxspeed deja explicites OSM (on ne touche que les
ways qui n'ont pas de maxspeed taggue). Idempotent : on relit toujours
le PBF backup, jamais le PBF deja patche, donc relancer N fois ne degrade
rien.

Cible : Ubuntu 24.04, pyosmium 4.x (installe via apt: python3-pyosmium).
Lance cote VM Valhalla (srv-safe-docker.aco.local), pas cote serveur Cockpit.

Usage :
    python3 patch_aco_speeds.py INPUT.osm.pbf OUTPUT.osm.pbf

Le INPUT.osm.pbf doit etre le backup *.osm.pbf.bak. L'OUTPUT ecrase le
fichier servi a Valhalla.

Voir infra/valhalla/README.md pour le workflow complet (rebuild tuiles,
restart container, etc.).
"""

import os
import sys

import osmium


TARGET_HIGHWAYS = {"service"}   # ajouter "track" ici si besoin
NEW_MAXSPEED = "40"             # km/h ; valeur arbitraire ~vitesse moyenne
                                # realiste sur les voies internes du circuit
PRESERVE_EXISTING = True        # ne touche pas aux maxspeed deja taggues OSM


class SpeedPatcher(osmium.SimpleHandler):
    def __init__(self, writer):
        super().__init__()
        self.writer = writer
        self.patched = 0
        self.preserved = 0
        self.service_total = 0

    def node(self, n):
        self.writer.add_node(n)

    def way(self, way):
        tags = dict(way.tags)
        is_target = tags.get("highway") in TARGET_HIGHWAYS
        if is_target:
            self.service_total += 1
            if PRESERVE_EXISTING and "maxspeed" in tags:
                self.preserved += 1
            else:
                tags["maxspeed"] = NEW_MAXSPEED
                self.patched += 1
        new_way = way.replace(tags=list(tags.items()))
        self.writer.add_way(new_way)

    def relation(self, r):
        self.writer.add_relation(r)


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 patch_aco_speeds.py INPUT.osm.pbf OUTPUT.osm.pbf")
        sys.exit(2)
    inp, out = sys.argv[1], sys.argv[2]

    if not os.path.exists(inp):
        print("ERREUR : fichier d'entree introuvable : {}".format(inp))
        sys.exit(1)
    if os.path.abspath(inp) == os.path.abspath(out):
        print("ERREUR : INPUT et OUTPUT ne peuvent pas etre identiques (toujours")
        print("repartir du backup *.bak pour idempotence).")
        sys.exit(1)
    if os.path.exists(out):
        os.remove(out)

    # pyosmium 4.x sur Ubuntu 24.04 ne deduit pas toujours le format depuis
    # l'extension (notamment .bak), donc on l'indique explicitement.
    input_file = osmium.io.File(inp, "osm.pbf")

    writer = osmium.SimpleWriter(out)
    patcher = SpeedPatcher(writer)
    patcher.apply_file(input_file)
    writer.close()

    print("OK")
    print("  ways highway=service total ......... {}".format(patcher.service_total))
    print("  deja maxspeed (preserves) .......... {}".format(patcher.preserved))
    print("  patches maxspeed={} ................ {}".format(NEW_MAXSPEED, patcher.patched))


if __name__ == "__main__":
    main()
