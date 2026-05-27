"""
Genere 5 documents `historique_controle` (un par nouveau type) a partir de la
collection `parking_scans`.

Schema cible (1 doc par type) :
{
  event: "24H AUTOS", year: 2025, type: <type>, race: "2025-06-14T16:00:00",
  granularity: "hourly+15min",
  <type>: [
    { name: "TRIBUNE 01",
      data:       [{ id, date, entree, sortie, present }, ...],   # horaire
      data_15min: [{ id, date, entree, sortie, present }, ...]    # 15 min
    },
    ...
  ]
}

Le champ `data` reprend strictement le format `frequentation` pour que cockpit
puisse lire ces nouveaux types via son pipeline existant ; `data_15min` apporte
la granularite fine pour exploitation future.

Idempotent : upsert sur (event, year, type).
"""

import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime

from pymongo import MongoClient

EVENT = '24H AUTOS'
YEAR = 2025
RACE_ISO = '2025-06-14T16:00:00'
SOURCE_COL = 'parking_scans'
TARGET_COL = 'historique_controle'

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'titan_dev'

# Source event/year tels qu'utilises a l'import xlsx -> parking_scans
SOURCE_EVENT = '24h_du_mans'
SOURCE_YEAR = 2025

# Mapping zone -> type cible. Chaque entree est (type_cible, predicate(name))
MAPPING_RULES = [
    ('tribunes',      lambda n: n.startswith('TRIBUNE')),
    ('parkings',      lambda n: n.startswith('P ')),
    ('aires_accueil', lambda n: n.startswith('AA ')),
    ('paddocks',      lambda n: n == 'PADDOCKS'),
    ('hospitalites',  lambda n: n in {'MAISON DES HUNAUDIERES', 'WELCOME', 'VISITES MUSEE'}),
]

# Decompte attendu par type (verification post-upsert)
EXPECTED_COUNTS = {
    'tribunes': 26,
    'parkings': 6,
    'aires_accueil': 7,
    'paddocks': 1,
    'hospitalites': 3,
}


def classify(zone_name):
    for type_target, predicate in MAPPING_RULES:
        if predicate(zone_name):
            return type_target
    return None


def build_15min_series(intervals_sorted):
    """Construit la serie 15 min avec cumul `present` brut depuis le debut."""
    out = []
    cumul = 0
    for it in intervals_sorted:
        ts = it['ts']
        entree = int(it.get('entree') or 0)
        sortie = int(it.get('sortie') or 0)
        cumul += entree - sortie
        out.append({
            'id': str(uuid.uuid4()),
            'date': ts.strftime('%Y-%m-%dT%H:%M:00'),
            'entree': entree,
            'sortie': sortie,
            'present': cumul,
        })
    return out


def aggregate_hourly(data_15min):
    """Agrege les slots 15 min en buckets horaires alignes."""
    buckets = defaultdict(lambda: {'entree': 0, 'sortie': 0, 'present': 0, 'last': None})
    for slot in data_15min:
        # date = 'YYYY-MM-DDTHH:MM:00' -> hour key = 'YYYY-MM-DDTHH:00:00'
        hour_key = slot['date'][:13] + ':00:00'
        b = buckets[hour_key]
        b['entree'] += slot['entree']
        b['sortie'] += slot['sortie']
        # `present` au top de l'heure suivante = dernier slot 15 min de l'heure
        b['present'] = slot['present']
        b['last'] = slot['date']
    return [
        {
            'id': str(uuid.uuid4()),
            'date': hour_key,
            'entree': b['entree'],
            'sortie': b['sortie'],
            'present': b['present'],
        }
        for hour_key, b in sorted(buckets.items())
    ]


def build_zone_item(zone_doc):
    """Pour 1 zone, construit le dict {name, data, data_15min}."""
    intervals_sorted = sorted(zone_doc.get('intervals') or [], key=lambda x: x['ts'])
    data_15min = build_15min_series(intervals_sorted)
    data = aggregate_hourly(data_15min)
    return {
        'name': zone_doc['zone'],
        'data': data,
        'data_15min': data_15min,
    }


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    src = db[SOURCE_COL]
    dst = db[TARGET_COL]

    cursor = src.find({'event': SOURCE_EVENT, 'year': SOURCE_YEAR}).sort('zone', 1)
    zones = list(cursor)
    if not zones:
        print(f'ERREUR : aucune zone trouvee dans {SOURCE_COL} pour '
              f'{SOURCE_EVENT}/{SOURCE_YEAR}', file=sys.stderr)
        sys.exit(1)
    print(f'Zones sources : {len(zones)}')

    by_type = defaultdict(list)
    unclassified = []
    for z in zones:
        t = classify(z['zone'])
        if t is None:
            unclassified.append(z['zone'])
        else:
            by_type[t].append(build_zone_item(z))

    if unclassified:
        print('Zones non classees (ignorees) :', unclassified)

    inserted = 0
    updated = 0
    for type_target, items in by_type.items():
        items.sort(key=lambda x: x['name'])
        expected = EXPECTED_COUNTS.get(type_target)
        if expected is not None and len(items) != expected:
            print(f'  !! {type_target} : {len(items)} items, attendu {expected}')
        else:
            print(f'  OK {type_target} : {len(items)} items')

        doc = {
            'event': EVENT,
            'year': YEAR,
            'type': type_target,
            'race': RACE_ISO,
            'granularity': 'hourly+15min',
            type_target: items,
        }
        res = dst.replace_one(
            {'event': EVENT, 'year': YEAR, 'type': type_target},
            doc,
            upsert=True,
        )
        if res.upserted_id is not None:
            inserted += 1
        elif res.modified_count:
            updated += 1

    print(f'Upserts : insert={inserted} update={updated}')

    # Verification rapide
    total = dst.count_documents({'event': EVENT, 'year': YEAR,
                                  'type': {'$in': list(EXPECTED_COUNTS)}})
    print(f'Docs en base pour {EVENT}/{YEAR} sur les 5 types : {total}')


if __name__ == '__main__':
    main()
