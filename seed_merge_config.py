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
        "merge_access_types": True,
        "todos_type": "portes",
        "vignette_fields": ["dayControl", "dayComment"],
        "cluster_enabled": True,
        "cluster_icon": "meeting_room",
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
        "merge_access_types": True,
        "todos_type": "parkings",
        "vignette_fields": ["dayControl", "dayComment", "capacite"],
        "cluster_enabled": True,
        "cluster_icon": "local_parking",
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
        "merge_access_types": True,
        "todos_type": "campings",
        "vignette_fields": ["dayControl", "dayComment"],
        "cluster_enabled": True,
        "cluster_icon": "rv_hookup",
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
        "merge_access_types": False,
        "todos_type": None,
        "vignette_fields": [],
        "cluster_enabled": False,
        "cluster_icon": None,
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
        "merge_access_types": False,
        "todos_type": "tribunes",
        "vignette_fields": ["dayControl", "dayComment"],
        "cluster_enabled": False,
        "cluster_icon": None,
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
        "merge_access_types": False,
        "todos_type": None,
        "vignette_fields": ["dayControl", "dayComment"],
        "cluster_enabled": False,
        "cluster_icon": None,
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
        "merge_access_types": False,
        "todos_type": None,
        "vignette_fields": ["dayControl", "dayComment"],
        "cluster_enabled": False,
        "cluster_icon": None,
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
        "merge_access_types": False,
        "todos_type": None,
        "vignette_fields": [],
        "cluster_enabled": False,
        "cluster_icon": None,
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
        "merge_access_types": False,
        "todos_type": None,
        "vignette_fields": [],
        "cluster_enabled": False,
        "cluster_icon": None,
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
        "merge_access_types": False,
        "todos_type": None,
        "vignette_fields": [],
        "cluster_enabled": False,
        "cluster_icon": None,
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
