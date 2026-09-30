"""Export quotidien des activites du site pour Claude Cowork (analyses de co-activite).

Produit un JSON des 60 prochains jours :
  - activites Momentus (seminaires, receptifs, roulages, installations
    partenaires...) avec leurs reservations d'espaces (reserve / exploitation /
    demontage / bloque, cf. momentus_lieux.PHASES : "reserve" n'est PAS un
    montage), leurs creneaux horaires, effectifs, pilotes ACO et prestataires ;
  - grands evenements Groundmaster (parametrages) qui recoupent la periode :
    montage, jours publics, demontage, lieux actives et leurs jours ;
  - referentiel des lieux avec coordonnees (proximite) ;
  - resume jour par jour ;
et le depose dans SharePoint (site SAFER, dossier COWORK) via Microsoft Graph,
en ecrasant le fichier de la veille (le lien reste le meme).

Authentification : application Entra "TITAN" (celle de bibler/onedrive.py), en
DELEGUE au nom d'un compte lemans.org, jeton en cache local
(COWORK_MSAL_CACHE). Premiere fois, en interactif :
    python scripts/export_cowork_activites.py --login
puis la tache planifiee tourne seule (le jeton se renouvelle a chaque usage).

Usage :
    python scripts/export_cowork_activites.py              # genere + depose
    python scripts/export_cowork_activites.py --no-upload  # genere en local seulement
    python scripts/export_cowork_activites.py --days 30

Aucune donnee personnelle de clients (ni contacts, ni montants) : les
prestataires sont identifies par leur societe, les pilotes ACO par leur nom.
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from pymongo import MongoClient  # noqa: E402

import momentus_lieux as ML  # noqa: E402

COCKPIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(COCKPIT_DIR, "exports")
FILENAME = "activites_site_60j.json"

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"

# Graph : application TITAN (locataire lemans.org), cf. bibler/onedrive.py
TENANT_ID = os.getenv("COWORK_TENANT_ID", "eef49d8b-1606-4b2a-ba0c-97b3522d7f6f")
CLIENT_ID = os.getenv("COWORK_CLIENT_ID", "d8a5ee42-96b3-4d15-b5d5-04d3c56e760b")
SCOPES = ["Files.ReadWrite.All"]
MSAL_CACHE = os.getenv("COWORK_MSAL_CACHE", os.path.join(COCKPIT_DIR, "msal_cowork_cache.bin"))
# Site SAFER, bibliotheque "Documents partages", dossier COWORK
DRIVE_ID = os.getenv("COWORK_DRIVE_ID", "b!dxxaeCirUE6gB7kDI-NH2T6N0XKN8iBFjYYopq6Q1k_uFxwF0vbAS4BxOnawKC_t")
FOLDER_ID = os.getenv("COWORK_FOLDER_ID", "01CYX3YANTQVWLDQD7BBALQTTWFNN3X3EF")

LONG_EVENT_DAYS = 60   # au-dela : reservation "enveloppe" (roulages recurrents, formations annuelles)
GM_CATEGORIES = ("hospitalites", "tribunes", "parkings", "campings", "fanzone", "boutiques",
                 "campements", "portes", "postesecours")

log = logging.getLogger("export_cowork")


# ---------------------------------------------------------------------------
# Construction du JSON
# ---------------------------------------------------------------------------

def _day(v):
    return ML._day(v)


def _status(ev):
    return ML.event_status(ev)


def _hhmm(v):
    return v if v else None


def build_payload(db, days=60):
    today = date.today()
    d_from, d_to = today, today + timedelta(days=days - 1)
    lo, hi = d_from.isoformat(), d_to.isoformat()

    rooms = {r["_id"]: r for r in db["momentus_rooms"].find({"_sync.deleted_at": None})}
    mapping = {m["_id"]: m for m in db[ML.COL_MAPPING].find({"status": "valide"})}

    # --- Activites Momentus -------------------------------------------------
    q = {"_sync.deleted_at": None, "isCanceled": {"$ne": True}, "isLost": {"$ne": True},
         "bookedSpaces": {"$elemMatch": {"startDate": {"$lte": hi}, "endDate": {"$gte": lo}}}}
    events = list(db["momentus_events"].find(q))
    ev_ids = [e["_id"] for e in events]
    fns_by_event = defaultdict(list)
    for fn in db["momentus_functions"].find({"_sync.deleted_at": None, "eventId": {"$in": ev_ids},
                                             "startDate": {"$lte": hi}, "endDate": {"$gte": lo}}
                                            ).sort([("startDate", 1), ("startTime", 1)]):
        fns_by_event[fn["eventId"]].append(fn)

    feature_ids = defaultdict(set)   # collection -> {feature_id} pour les coordonnees
    activites, par_jour = [], defaultdict(list)
    for ev in events:
        segs = []
        for bs in ev.get("bookedSpaces") or []:
            a, b = _day(bs.get("startDate")), _day(bs.get("endDate"))
            if not a or not b or b < d_from or a > d_to:
                continue
            segs.append((ML.PHASES.get(bs.get("usageType"), bs.get("usageType") or "autre"),
                         max(a, d_from), min(b, d_to),
                         None if bs.get("isAllDay") else bs.get("startTime"),
                         None if bs.get("isAllDay") else bs.get("endTime"),
                         (bs.get("roomName") or "").strip()))
        if not segs:
            continue
        room_by_name = {}
        for bs in ev.get("bookedSpaces") or []:
            room_by_name[(bs.get("roomName") or "").strip()] = bs.get("roomId")
        phases = []
        for p in ML._merge_segments(segs):
            rid = room_by_name.get(p["room"])
            m = mapping.get(rid)
            if m:
                feature_ids[m.get("collection")].add(m["feature_id"])
            phases.append({
                "espace": p["room"],
                "lieu_carto": m.get("feature_id") if m else None,
                "phase": p["phase"], "du": p["start"], "au": p["end"],
                "heure_debut": p["start_time"], "heure_fin": p["end_time"],
            })
        span = (_day(ev.get("end")) - _day(ev.get("start"))).days if _day(ev.get("end")) and _day(ev.get("start")) else 0
        staff = []
        for s in ev.get("staffAssignments") or []:
            role = s.get("staffAssignmentName") or s.get("name")
            for mbr in s.get("staffMembers") or []:
                staff.append({"role": role, "nom": mbr.get("staffMemberName") or mbr.get("name")})
            if not s.get("staffMembers"):
                staff.append({"role": role, "nom": s.get("staffMemberName")})
        prestataires = sorted({(c.get("accountName") or "").strip() for c in ev.get("contactRoles") or []
                               if (c.get("role") or "").strip().lower().startswith("prestataire")
                               and (c.get("accountName") or "").strip()})
        creneaux = [{
            "date": f.get("startDate"), "date_fin": f.get("endDate") if f.get("endDate") != f.get("startDate") else None,
            "heure_debut": None if f.get("isAllDay") else f.get("startTime"),
            "heure_fin": None if f.get("isAllDay") else f.get("endTime"),
            "nom": (f.get("name") or "").strip(), "type": (f.get("functionTypeName") or "").strip() or None,
            "espace": (f.get("roomName") or "").strip() or None,
            "effectif": f.get("guaranteedAttendance") or f.get("expectedAttendance") or f.get("agreedAttendance") or None,
            "configuration": (f.get("roomSetup") or "").strip() or None,
        } for f in fns_by_event.get(ev["_id"], [])]
        act = {
            "id": ev["_id"],
            "nom": (ev.get("name") or "").strip(),
            "client": (ev.get("accountName") or "").strip(),
            "type": (ev.get("eventTypeName") or "").strip(),
            "genre": ev.get("genreName"),
            "origine": ev.get("businessClassificationName"),
            "statut": _status(ev),
            "interne": bool(ev.get("isInternal")),
            "blocage": bool(ev.get("isBlackout")),
            "effectif_estime": ev.get("estimatedTotalAttendance") or ev.get("estimatedAttendance") or None,
            "reservation_longue": span > LONG_EVENT_DAYS,
            "debut_global": ev.get("start"), "fin_globale": ev.get("end"),
            "sites": ev.get("venueNames") or [],
            "pilotes_aco": [s for s in staff if s.get("nom") or s.get("role")],
            "prestataires": prestataires,
            "description": (ev.get("description") or "").strip() or None,
            "phases": phases,
            "creneaux": creneaux,
        }
        activites.append(act)
        # Resume journalier (hors reservations longues, qui noieraient tout)
        if not act["reservation_longue"]:
            for p in phases:
                x, end = _day(p["du"]), _day(p["au"])
                while x <= end:
                    par_jour[x.isoformat()].append((act, p))
                    x += timedelta(days=1)
    activites.sort(key=lambda a: (min(p["du"] for p in a["phases"]), a["nom"]))

    # --- Grands evenements Groundmaster -------------------------------------
    cats = {c["_id"]: c for c in db["groundmaster_categories"].find({"_id": {"$in": list(GM_CATEGORIES)}})}
    evenements = []
    for p in db["parametrages"].find({}, {"event": 1, "year": 1, "data": 1}):
        data = p.get("data") or {}
        gh = data.get("globalHoraires") or {}
        m_start = _day((gh.get("montage") or {}).get("start"))
        m_end = _day((gh.get("montage") or {}).get("end"))
        dm_start = _day((gh.get("demontage") or {}).get("start"))
        dm_end = _day((gh.get("demontage") or {}).get("end"))
        jours = [x for x in gh.get("dates") or [] if isinstance(x, dict) and _day(x.get("date"))]
        first = m_start or (_day(jours[0]["date"]) if jours else None)
        last = dm_end or (_day(jours[-1]["date"]) if jours else None)
        if not first or not last or last < d_from or first > d_to:
            continue
        lieux = []
        for cid in GM_CATEGORIES:
            cat = cats.get(cid)
            if not cat:
                continue
            items = data.get(cat.get("dataKey")) or []
            items = list(items.values()) if isinstance(items, dict) else items
            for it in items:
                if not isinstance(it, dict) or it.get("active") is False:
                    continue
                dts = sorted(d.get("date") for d in it.get("dates") or [] if isinstance(d, dict) and d.get("date"))
                if it.get("id") and cat.get("collection"):
                    feature_ids[cat["collection"]].add(it["id"])
                lieux.append({"categorie": cat.get("label") or cid, "lieu": it.get("name"),
                              "lieu_carto": it.get("id"), "jours": dts})
        evenements.append({
            "evenement": p.get("event"), "annee": p.get("year"),
            "montage": {"du": m_start.isoformat() if m_start else None, "au": m_end.isoformat() if m_end else None},
            "demontage": {"du": dm_start.isoformat() if dm_start else None, "au": dm_end.isoformat() if dm_end else None},
            "course": gh.get("race"),
            "jours_public": [{"date": x.get("date"), "ouverture": x.get("openTime"), "fermeture": x.get("closeTime"),
                              "ferme": bool(x.get("closed")), "h24": bool(x.get("is24h"))} for x in jours],
            "lieux_actives": lieux,
        })
    evenements.sort(key=lambda e: e["montage"]["du"] or (e["jours_public"][0]["date"] if e["jours_public"] else ""))

    # --- Referentiel des lieux carto (coordonnees) ---------------------------
    geo = ML._features_geo(db, feature_ids)
    lieux_carto = {fid: {"nom": g["name"], "lat": round(g["lat"], 6), "lng": round(g["lng"], 6)}
                   for fid, g in geo.items()}
    espaces = {}
    for rid, r in rooms.items():
        m = mapping.get(rid)
        espaces[(r.get("name") or "").strip()] = {
            "site": r.get("venueName"), "groupe": r.get("group") or None,
            "capacite": r.get("maxCapacity") or None,
            "lieu_carto": m.get("feature_id") if m else None,
            "espaces_en_conflit": sorted({(rooms[x].get("name") or "").strip()
                                          for x in r.get("conflictingRoomIds") or [] if x in rooms}) or None,
        }

    # --- Resume jour par jour -------------------------------------------------
    jours = []
    x = d_from
    while x <= d_to:
        iso = x.isoformat()
        items = par_jour.get(iso, [])
        public = {}
        for act, p in items:
            # Toute reservation non bloquee compte : "reserve" (moveIn) est le mode
            # par defaut, y compris pour la journee du seminaire elle-meme.
            if p["phase"] != "bloque" and act["type"] not in ("Interne", "Travaux / maintenance"):
                public[act["id"]] = act.get("effectif_estime") or 0
        ev_jour = []
        for e in evenements:
            tags = []
            if e["montage"]["du"] and e["montage"]["du"] <= iso <= (e["montage"]["au"] or ""):
                tags.append("montage")
            if any(j["date"] == iso for j in e["jours_public"]):
                tags.append("jour public")
            if e["demontage"]["du"] and e["demontage"]["du"] <= iso <= (e["demontage"]["au"] or ""):
                tags.append("demontage")
            if tags:
                ev_jour.append(f'{e["evenement"]} {e["annee"]} ({", ".join(tags)})')
        jours.append({
            "date": iso,
            "grands_evenements": ev_jour,
            "nb_activites": len({a["id"] for a, _ in items}),
            "phases": dict(Counter(p["phase"] for _, p in items)),
            "effectif_public_estime": sum(public.values()) or None,
            "activites": sorted({f'{a["nom"]} | {p["espace"]} | {p["phase"]}' +
                                 (f' {p["heure_debut"]}-{p["heure_fin"]}' if p["heure_debut"] else "")
                                 for a, p in items}),
        })
        x += timedelta(days=1)

    state = db["momentus_sync_state"].find_one({"_id": "state"}, {"last_success_at": 1}) or {}
    last = state.get("last_success_at")
    return {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "periode": {"du": lo, "au": hi},
        "donnees_momentus_du": last.isoformat() + "Z" if isinstance(last, datetime) else None,
        "notice": NOTICE,
        "grands_evenements": evenements,
        "activites_momentus": activites,
        "jours": jours,
        "espaces_momentus": espaces,
        "lieux_carto": lieux_carto,
    }


NOTICE = {
    "objet": "Activites prevues sur le site du circuit des 24 Heures du Mans (ACO) sur 60 jours, "
             "pour analyser la co-activite (public present, montages/demontages, activites dynamiques, "
             "entreprises exterieures) et preparer inspections communes prealables et plans de prevention.",
    "sources": {
        "activites_momentus": "Momentus Elite, outil de reservation commerciale (seminaires, receptifs, "
                              "visites, roulages, installations des partenaires pendant les grands evenements). "
                              "Synchronise toutes les heures.",
        "grands_evenements": "Groundmaster : parametrage des grands evenements (montage, jours publics, "
                             "demontage, lieux actives et leurs jours d'ouverture).",
    },
    "phases": {
        "reserve": "mode de reservation PAR DEFAUT dans Momentus (moveIn) : couvre aussi bien la journee du "
                   "seminaire elle-meme qu'une installation. Ne PAS l'interpreter comme un montage ; "
                   "considerer qu'il y a activite, et presence du client si des creneaux ou un effectif existent.",
        "exploitation": "activite explicitement saisie comme telle (event)",
        "demontage": "desinstallation explicitement saisie (moveOut)",
        "bloque": "espace bloque (dark) : reserve pour preparation ou indisponible, generalement sans public",
    },
    "statuts": {"confirme": "reservation ferme", "option": "reservation posee, peut encore bouger",
                "prospect": "simple piste commerciale, espace non reserve"},
    "champs": {
        "reservation_longue": "reservation etalee sur plus de 60 jours (roulages recurrents, formations annuelles) : "
                              "exclue du resume 'jours', a lire avec prudence",
        "lieu_carto": "identifiant d'un lieu de la carte (cf. 'lieux_carto' pour ses coordonnees), "
                      "permet d'evaluer la proximite entre deux activites",
        "espaces_en_conflit": "espaces que Momentus declare incompatibles entre eux (ex. Garden 1 et Village)",
        "pilotes_aco": "personnes ACO affectees (regie, production, service evenementiel)",
        "prestataires": "societes prestataires declarees dans Momentus (installateurs, traiteurs...)",
        "type Epreuve sportive": "le plus souvent une installation de partenaire pendant un grand evenement",
    },
    "limites": [
        "Les travaux de l'ACO et du service technique, le montage des grands evenements (tribunes, structures, "
        "reseaux), les livraisons et la circulation d'engins ne sont PAS dans Momentus.",
        "L'effectif n'est renseigne que pour environ un tiers des reservations ; horaires surtout dans 'creneaux'.",
        "Les sous-traitants des prestataires ne sont pas connus.",
        "Une absence dans ce fichier ne prouve pas l'absence d'activite.",
    ],
}


# ---------------------------------------------------------------------------
# Depot SharePoint (Microsoft Graph, delegue)
# ---------------------------------------------------------------------------

def _msal_app():
    import msal
    cache = msal.SerializableTokenCache()
    if os.path.exists(MSAL_CACHE):
        with open(MSAL_CACHE, "r", encoding="utf-8") as fh:
            cache.deserialize(fh.read())
    app = msal.PublicClientApplication(CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT_ID}",
                                       token_cache=cache)
    return app, cache


def _save_cache(cache):
    if cache.has_state_changed:
        tmp = MSAL_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(cache.serialize())
        os.replace(tmp, MSAL_CACHE)


def get_token(interactive=False):
    app, cache = _msal_app()
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result and interactive:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"connexion impossible : {flow.get('error_description') or flow}")
        print(flow["message"], flush=True)
        result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if not result or "access_token" not in result:
        raise RuntimeError("jeton Graph indisponible (relancer avec --login) : " +
                           str((result or {}).get("error_description") or "pas de compte en cache"))
    return result["access_token"]


def upload(token, content: bytes):
    """Ecrase FILENAME dans le dossier COWORK. Rend l'item Graph (webUrl...)."""
    h = {"Authorization": f"Bearer {token}"}
    base = f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/items/{FOLDER_ID}:/{FILENAME}:"
    if len(content) < 4 * 1024 * 1024:
        r = requests.put(base + "/content", headers={**h, "Content-Type": "application/json"},
                         data=content, timeout=120)
        r.raise_for_status()
        return r.json()
    # > 4 Mo : session d'upload par morceaux
    r = requests.post(base + "/createUploadSession", headers=h,
                      json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}, timeout=60)
    r.raise_for_status()
    url, size, chunk = r.json()["uploadUrl"], len(content), 5 * 320 * 1024
    item = None
    for start in range(0, size, chunk):
        part = content[start:start + chunk]
        r = requests.put(url, data=part, timeout=300, headers={
            "Content-Length": str(len(part)), "Content-Range": f"bytes {start}-{start + len(part) - 1}/{size}"})
        r.raise_for_status()
        if r.status_code in (200, 201):
            item = r.json()
    return item


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export des activites du site pour Claude Cowork")
    ap.add_argument("--login", action="store_true", help="Connexion interactive (code a saisir) puis sortie")
    ap.add_argument("--no-upload", action="store_true", help="Genere le fichier local sans le deposer")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--db", default=DB_NAME)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [ExportCowork] %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    if args.login:
        get_token(interactive=True)
        print("Connexion enregistree : la tache planifiee peut maintenant deposer le fichier.")
        return 0

    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[args.db]
    payload = build_payload(db, days=args.days)
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, FILENAME)
    with open(path + ".tmp", "wb") as fh:
        fh.write(content)
    os.replace(path + ".tmp", path)
    log.info("Fichier genere : %s (%d Ko, %d activites, %d grands evenements)", path, len(content) // 1024,
             len(payload["activites_momentus"]), len(payload["grands_evenements"]))
    if args.no_upload:
        return 0
    try:
        item = upload(get_token(), content)
        log.info("Depose sur SharePoint : %s", (item or {}).get("webUrl"))
        return 0
    except Exception as e:
        log.error("Depot SharePoint impossible : %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
