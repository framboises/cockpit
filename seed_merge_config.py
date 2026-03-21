"""
Seed initial de la collection merge_config.
Reproduit le comportement actuel de merge.py pour les categories existantes.

Usage : python seed_merge_config.py
"""

import pymongo

client = pymongo.MongoClient("mongodb://localhost:27017")
db = client["titan"]

CONFIGS = [
    {
        "data_key": "portesHoraires",
        "label": "Portes d'acces",
        "enabled": True,
        "mode": "schedule",
        "activity_label": "Porte {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public", "organisation"],
        "todos_type": "portes",
        "vignette_fields": ["dayControl", "dayComment"],
    },
    {
        "data_key": "parkingsHoraires",
        "label": "Parkings",
        "enabled": True,
        "mode": "addable_schedule",
        "activity_label": "Parking {name}",
        "timeline_category": "Parking",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public", "orga", "vip"],
        "todos_type": "parkings",
        "vignette_fields": ["dayControl", "dayComment", "capacite"],
    },
    {
        "data_key": "campingsHoraires",
        "label": "Aires d'accueil",
        "enabled": True,
        "mode": "addable_schedule",
        "activity_label": "Aire d'accueil {name}",
        "timeline_category": "AA",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public", "organisation", "vip"],
        "todos_type": "campings",
        "vignette_fields": ["dayControl", "dayComment"],
    },
    {
        "data_key": "hospiHoraires",
        "label": "Hospitalites",
        "enabled": True,
        "mode": "addable_schedule",
        "activity_label": "Hospitalite {name}",
        "timeline_category": "Hospi",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public"],
        "todos_type": None,
        "vignette_fields": [],
    },
    {
        "data_key": "tribunesHoraires",
        "label": "Tribunes",
        "enabled": True,
        "mode": "addable_schedule",
        "activity_label": "Tribune {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public", "vip"],
        "todos_type": "tribunes",
        "vignette_fields": ["dayControl", "dayComment"],
    },
    {
        "data_key": "campmentsHoraires",
        "label": "Campements commissaires",
        "enabled": True,
        "mode": "addable_schedule",
        "activity_label": "Campement {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public", "organisation"],
        "todos_type": None,
        "vignette_fields": ["dayControl", "dayComment"],
    },
    {
        "data_key": "boutiquesHoraires",
        "label": "Boutiques & Programme",
        "enabled": True,
        "mode": "schedule",
        "activity_label": "Boutique {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": ["public", "organisation"],
        "todos_type": None,
        "vignette_fields": ["dayControl", "dayComment"],
    },
    {
        "data_key": "acoServicesHoraires",
        "label": "Services ACO",
        "enabled": False,
        "mode": "schedule",
        "activity_label": "Service {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": [],
        "todos_type": None,
        "vignette_fields": [],
    },
    # Activation : pas de vignettes
    {
        "data_key": "passerellesHoraires",
        "label": "Passerelles",
        "enabled": False,
        "mode": "activation",
        "activity_label": "Passerelle {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": [],
        "todos_type": None,
        "vignette_fields": [],
    },
    {
        "data_key": "sanitairesActivation",
        "label": "Sanitaires",
        "enabled": False,
        "mode": "activation",
        "activity_label": "Sanitaire {name}",
        "timeline_category": "Controle",
        "timeline_type": "Organization",
        "department": "SAFE",
        "access_types": [],
        "todos_type": None,
        "vignette_fields": [],
    },
]


def seed():
    col = db.merge_config
    inserted = 0
    updated = 0
    for cfg in CONFIGS:
        result = col.update_one(
            {"data_key": cfg["data_key"]},
            {"$set": cfg},
            upsert=True
        )
        if result.upserted_id:
            inserted += 1
        elif result.modified_count:
            updated += 1

    print(f"merge_config: {inserted} inseres, {updated} mis a jour, "
          f"{len(CONFIGS)} total")


if __name__ == "__main__":
    seed()
