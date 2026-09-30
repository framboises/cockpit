#!/usr/bin/env python3
"""
momentus_sync.py - Synchro Momentus Elite (Open API, region EU) -> MongoDB.

LECTURE SEULE cote Momentus : aucune requete d'ecriture n'est jamais emise
(seuls GET et les POST de recherche /events/all). Les identifiants API
devraient d'ailleurs etre crees SANS "Write Permissions".

Lance par tache planifiee Windows toutes les heures
(scripts/install_momentus_task.ps1).

Modes :
    python momentus_sync.py           # incremental (horaire)
    python momentus_sync.py --full    # import complet + reconciliation des suppressions

L'incremental bascule seul en complet si le dernier complet a plus de
FULL_EVERY_HOURS heures : c'est le seul moyen de voir un evenement SUPPRIME
dans Momentus (l'API ne signale que les modifications).

Collections :
    momentus_events      un doc par evenement (_id = id Momentus), tel que rendu
                         par /v1/events/all (espaces reserves et live inclus)
    momentus_functions   un doc par fonction (_id = id Momentus)
    momentus_venues      sites
    momentus_rooms       espaces (inactifs compris)
    momentus_setup       referentiels, un doc par liste (_id = nom de la liste)
    momentus_sync_state  etat de la synchro (_id = "state") + verrou
    momentus_sync_runs   journal des executions (TTL 90 j)

Chaque doc synchronise porte `_sync = {first_seen, last_seen, deleted_at}`.
Un objet disparu de Momentus n'est jamais supprime de la base : il recoit
`_sync.deleted_at` (et le perd s'il reapparait).

Identifiants : MOMENTUS_CLIENT_ID / MOMENTUS_CLIENT_SECRET. La tache tourne
en SYSTEM : ils doivent etre definis au niveau Machine (comme SMTP_*). En
lancement manuel, le script relit aussi le registre (Machine puis User), ce
qui evite de redemarrer le shell apres un `setx`.
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import requests
from pymongo import MongoClient, ReplaceOne, UpdateOne

TASK_NAME = "Sync Momentus"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_TITAN_ENV = os.getenv("TITAN_ENV", "dev").strip().lower()
DB_NAME = "titan" if _TITAN_ENV in {"prod", "production"} else "titan_dev"

AUTH_URL = os.getenv("MOMENTUS_AUTH_URL", "https://auth-api.eu-venueops.com/token")
API_URL = os.getenv("MOMENTUS_API_URL", "https://api.eu-venueops.com").rstrip("/")
HTTP_TIMEOUT = int(os.getenv("MOMENTUS_TIMEOUT_SECONDS", "120"))

PAGE_SIZE = 500                  # accepte par /events/all (mesure : 1,5 s la page)
FULL_EVERY_HOURS = 20            # l'incremental declenche un complet au-dela
OVERLAP_MINUTES = 15             # recouvrement du filtre lastModifiedOn
FUNCTIONS_WINDOW = (-2, 45)      # fenetre (jours) des fonctions relues a chaque heure
PER_EVENT_FUNCTIONS_MAX = 300    # au-dela, on relit par fenetre plutot qu'evenement par evenement
LOCK_TTL_MINUTES = 30
RUNS_TTL_DAYS = 90

# Referentiels : nom de la liste -> chemin API
SETUP_LISTS = {
    "event_types": "/v1/event-setup/event-types",
    "event_tags": "/v1/event-setup/event-tags",
    "space_usages": "/v1/event-setup/space-usages",
    "function_types": "/v1/event-setup/function-types",
    "business_classifications": "/v1/event-setup/business-classifications",
    "event_staff_assignments": "/v1/event-setup/event-staff-assignments",
    "function_staff_assignments": "/v1/event-setup/function-staff-assignments",
    "genres": "/v1/event-setup/genres",
    "task_types": "/v1/general-setup/task-types",
    "users": "/v1/general-setup/users",
}

log = logging.getLogger("momentus_sync")


class MomentusError(Exception):
    pass


def _utcnow():
    """UTC naif : c'est ce que pymongo rend a la relecture."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Environnement
# ---------------------------------------------------------------------------

def _env(name):
    """Variable d'environnement, avec repli sur le registre Windows.

    Le processus herite de l'environnement de son lanceur : une variable posee
    par `setx` apres l'ouverture du shell n'y est pas. Le registre, lui, est a
    jour (Machine d'abord, comme la tache planifiee qui tourne en SYSTEM).
    """
    val = os.getenv(name, "").strip()
    if val or sys.platform != "win32":
        return val
    try:
        import winreg
        for hive, path in (
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ):
            try:
                with winreg.OpenKey(hive, path) as key:
                    val = str(winreg.QueryValueEx(key, name)[0]).strip()
                    if val:
                        return val
            except OSError:
                continue
    except ImportError:
        pass
    return ""


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logfile = os.path.join(LOG_DIR, f"momentus_sync-{datetime.now():%Y%m%d}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [MomentusSync] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(logfile, encoding="utf-8")],
    )


def _status_path():
    path = os.getenv("CRON_STATUS_FILE", "").strip()
    return path or os.path.join(SCRIPT_DIR, "cron_status.json")


def _update_cron_status(status, message=""):
    """Meme format que pcorg_sync.py / live_controle.py."""
    path = _status_path()
    tasks = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            tasks = data if isinstance(data, list) else data.get("tasks", [])
        except Exception:
            tasks = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = next((t for t in tasks if t.get("name") == TASK_NAME), None)
    if entry is None:
        entry = {"name": TASK_NAME}
        tasks.append(entry)
    entry["status"] = status
    entry["last_run"] = now
    if message:
        entry["message"] = message
    else:
        entry.pop("message", None)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(tasks, handle, indent=2)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Client API
# ---------------------------------------------------------------------------

class MomentusClient:
    """Client lecture seule. Jeton renouvele sur 401, retry sur 429/5xx."""

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = requests.Session()
        self.token = None
        self.calls = 0

    def _authenticate(self):
        resp = self.session.post(
            AUTH_URL,
            json={"clientId": self.client_id, "clientSecret": self.client_secret},
            timeout=30,
        )
        if resp.status_code != 200:
            raise MomentusError(f"auth_http_{resp.status_code}")
        self.token = resp.json().get("accessToken")
        if not self.token:
            raise MomentusError("auth_sans_jeton")

    def _request(self, method, path, payload=None, params=None):
        if method not in ("GET", "POST"):
            raise MomentusError(f"methode interdite : {method}")
        if self.token is None:
            self._authenticate()
        url = f"{API_URL}{path}"
        reauth_done = False
        for attempt in range(4):
            try:
                resp = self.session.request(
                    method, url, json=payload, params=params, timeout=HTTP_TIMEOUT,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            except requests.RequestException as exc:
                if attempt == 3:
                    raise MomentusError(f"injoignable {path} : {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            self.calls += 1
            if resp.status_code == 401 and not reauth_done:
                reauth_done = True
                self._authenticate()
                continue
            if resp.status_code in self.RETRY_STATUSES and attempt < 3:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait and wait.isdigit() else 2 ** attempt)
                continue
            if resp.status_code != 200:
                raise MomentusError(f"http_{resp.status_code} {method} {path} : {resp.text[:300]}")
            return resp.json()
        raise MomentusError(f"echec apres retries : {method} {path}")

    def get(self, path, **params):
        return self._request("GET", path, params=params or None)

    def search_events(self, venue_ids, **filters):
        """Toutes les pages de POST /v1/events/all (venueIds obligatoire)."""
        out, page = [], 1
        while True:
            body = {
                "venueIds": venue_ids,
                "includeBookedSpaces": True,
                "includeLiveEntertainment": True,
                "page": page,
                "pageSize": PAGE_SIZE,
                **filters,
            }
            data = self._request("POST", "/v1/events/all", payload=body)
            results = data.get("results") or []
            out.extend(results)
            total = data.get("totalCount") or 0
            if not results or len(out) >= total or page >= 200:
                return out
            page += 1


# ---------------------------------------------------------------------------
# Ecriture MongoDB
# ---------------------------------------------------------------------------

def _ensure_indexes(db):
    ev = db["momentus_events"]
    ev.create_index("start")
    ev.create_index("end")
    ev.create_index("venueIds")
    ev.create_index("roomIds")
    ev.create_index("bookedSpaces.roomId")  # momentus_lieux.bookings_for_rooms
    ev.create_index("lastModifiedOn")
    ev.create_index("_sync.deleted_at")
    fn = db["momentus_functions"]
    fn.create_index("eventId")
    fn.create_index([("startDate", 1), ("endDate", 1)])
    fn.create_index("roomId")
    fn.create_index("_sync.deleted_at")
    db["momentus_rooms"].create_index("venueId")
    db["momentus_sync_runs"].create_index("started_at", expireAfterSeconds=RUNS_TTL_DAYS * 86400)


def _upsert_many(coll, items, now):
    """Remplace chaque doc par la version Momentus en gardant `first_seen`.

    Rend le nombre de docs crees.
    """
    if not items:
        return 0
    ids = [it["id"] for it in items if it.get("id")]
    first_seen = {
        d["_id"]: (d.get("_sync") or {}).get("first_seen")
        for d in coll.find({"_id": {"$in": ids}}, {"_sync.first_seen": 1})
    }
    ops = []
    for it in items:
        if not it.get("id"):
            continue
        doc = dict(it)
        doc["_id"] = it["id"]
        doc["_sync"] = {"first_seen": first_seen.get(it["id"]) or now, "last_seen": now, "deleted_at": None}
        ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
    for i in range(0, len(ops), 1000):
        coll.bulk_write(ops[i:i + 1000], ordered=False)
    return sum(1 for i in ids if i not in first_seen)


def _mark_missing(coll, flt, seen_ids, now):
    """Pose `_sync.deleted_at` sur les docs du filtre absents de `seen_ids`."""
    q = dict(flt)
    q["_id"] = {"$nin": list(seen_ids)}
    q["_sync.deleted_at"] = None
    res = coll.update_many(q, {"$set": {"_sync.deleted_at": now}})
    return res.modified_count


# ---------------------------------------------------------------------------
# Etapes
# ---------------------------------------------------------------------------

def sync_references(api, db, now, stats):
    venues = [v for v in api.get("/v1/general-setup/venues") if v.get("id")]
    stats["venues_new"] = _upsert_many(db["momentus_venues"], venues, now)
    stats["venues"] = len(venues)
    _mark_missing(db["momentus_venues"], {}, {v["id"] for v in venues}, now)

    rooms = [r for r in api.get("/v1/general-setup/rooms/true") if r.get("id")]
    stats["rooms_new"] = _upsert_many(db["momentus_rooms"], rooms, now)
    stats["rooms"] = len(rooms)
    _mark_missing(db["momentus_rooms"], {}, {r["id"] for r in rooms}, now)

    ops, errors = [], {}
    for name, path in SETUP_LISTS.items():
        try:
            items = api.get(path)
        except MomentusError as exc:
            errors[name] = str(exc)[:200]
            continue
        ops.append(UpdateOne({"_id": name},
                             {"$set": {"items": items, "count": len(items), "synced_at": now}},
                             upsert=True))
    if ops:
        db["momentus_setup"].bulk_write(ops, ordered=False)
    if errors:
        stats["setup_errors"] = errors
    return [v["id"] for v in venues]


def _month_windows(start, end):
    """Decoupe [start, end] en fenetres d'un mois (dates)."""
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(end, nxt - timedelta(days=1))
        cur = nxt


def _fetch_functions_window(api, start, end):
    items = {}
    for a, b in _month_windows(start, end):
        for fn in api.get("/v1/functions", startDate=a.isoformat(), endDate=b.isoformat()) or []:
            if fn.get("id"):
                items[fn["id"]] = fn
    return list(items.values())


def _parse_day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def full_sync(api, db, venue_ids, now, stats):
    """Tous les evenements + toutes les fonctions, puis reconciliation."""
    events = api.search_events(venue_ids)
    stats["events"] = len(events)
    active_before = db["momentus_events"].count_documents({"_sync.deleted_at": None})
    stats["events_new"] = _upsert_many(db["momentus_events"], events, now)
    # Garde-fou : une pagination tronquee ferait passer toute la base pour
    # supprimee. Un vrai menage de plus de 10 % d'un coup n'arrive pas.
    if len(events) >= 0.9 * active_before:
        stats["events_deleted"] = _mark_missing(db["momentus_events"], {}, {e["id"] for e in events}, now)
    else:
        stats["events_deleted_skipped"] = f"{len(events)} recus pour {active_before} en base"
        log.warning("Reconciliation evenements sautee : %s", stats["events_deleted_skipped"])

    days = [d for e in events for d in (_parse_day(e.get("start")), _parse_day(e.get("end"))) if d]
    if not days:
        return events
    # Bornes : l'API ne rend rien avant 2020 ; on borne aussi loin dans le
    # futur pour qu'un evenement saisi en 2099 par erreur ne coute pas 900 appels.
    lo = max(min(days), date(2020, 1, 1))
    hi = min(max(days), date.today() + timedelta(days=3 * 365))
    functions = _fetch_functions_window(api, lo, hi)
    stats["functions"] = len(functions)
    stats["functions_window"] = [lo.isoformat(), hi.isoformat()]
    fwin = {"startDate": {"$gte": lo.isoformat(), "$lte": hi.isoformat()}}
    active_fn = db["momentus_functions"].count_documents({**fwin, "_sync.deleted_at": None})
    stats["functions_new"] = _upsert_many(db["momentus_functions"], functions, now)
    if len(functions) >= 0.9 * active_fn:
        stats["functions_deleted"] = _mark_missing(db["momentus_functions"], fwin,
                                                   {f["id"] for f in functions}, now)
    else:
        stats["functions_deleted_skipped"] = f"{len(functions)} recues pour {active_fn} en base"
        log.warning("Reconciliation fonctions sautee : %s", stats["functions_deleted_skipped"])
    return events


def incremental_sync(api, db, venue_ids, since, now, stats):
    """Evenements modifies depuis `since` + leurs fonctions + fenetre proche."""
    since_s = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats["since"] = since_s
    events = api.search_events(venue_ids, lastModifiedOn=since_s)
    stats["events"] = len(events)
    stats["events_new"] = _upsert_many(db["momentus_events"], events, now)

    fcoll = db["momentus_functions"]
    changed_ids = [e["id"] for e in events if e.get("id")]
    fn_count = 0
    if len(changed_ids) <= PER_EVENT_FUNCTIONS_MAX:
        deleted = 0
        for eid in changed_ids:
            fns = [f for f in (api.get(f"/v1/functions/event/{eid}") or []) if f.get("id")]
            fn_count += len(fns)
            _upsert_many(fcoll, fns, now)
            deleted += _mark_missing(fcoll, {"eventId": eid}, {f["id"] for f in fns}, now)
        stats["functions_deleted"] = deleted
    else:
        stats["per_event_skipped"] = len(changed_ids)

    # Les fonctions proches sont relues a chaque passage : rien ne garantit
    # qu'un changement d'horaire de fonction fasse bouger lastModifiedOn de
    # l'evenement.
    today = date.today()
    lo, hi = today + timedelta(days=FUNCTIONS_WINDOW[0]), today + timedelta(days=FUNCTIONS_WINDOW[1])
    window = _fetch_functions_window(api, lo, hi)
    _upsert_many(fcoll, window, now)
    stats["functions"] = fn_count + len(window)
    return events


# ---------------------------------------------------------------------------
# Verrou et etat
# ---------------------------------------------------------------------------

def _acquire_lock(state, owner, now):
    res = state.update_one(
        {"_id": "state", "$or": [{"lock.expires": {"$lt": now}}, {"lock": None}, {"lock": {"$exists": False}}]},
        {"$set": {"lock": {"owner": owner, "expires": now + timedelta(minutes=LOCK_TTL_MINUTES)}}},
    )
    if res.matched_count:
        return True
    if state.count_documents({"_id": "state"}) == 0:
        try:
            state.insert_one({"_id": "state", "lock": {"owner": owner,
                                                       "expires": now + timedelta(minutes=LOCK_TTL_MINUTES)}})
            return True
        except Exception:
            return False
    return False


def _release_lock(state, owner):
    state.update_one({"_id": "state", "lock.owner": owner}, {"$set": {"lock": None}})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synchro Momentus Elite -> MongoDB (lecture seule)")
    parser.add_argument("--full", action="store_true", help="Import complet + reconciliation")
    parser.add_argument("--db", default=DB_NAME, help=f"Base MongoDB (defaut {DB_NAME})")
    args = parser.parse_args(argv)

    _setup_logging()
    client_id, client_secret = _env("MOMENTUS_CLIENT_ID"), _env("MOMENTUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        log.error("MOMENTUS_CLIENT_ID / MOMENTUS_CLIENT_SECRET absents (niveau Machine requis pour la tache)")
        _update_cron_status("error", "identifiants Momentus absents")
        return 2

    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo[args.db]
    _ensure_indexes(db)
    state = db["momentus_sync_state"]
    owner = uuid.uuid4().hex
    now = _utcnow()
    if not _acquire_lock(state, owner, now):
        log.warning("Synchro deja en cours (verrou pose), abandon")
        return 0

    st = state.find_one({"_id": "state"}) or {}
    last_full = st.get("last_full_at")
    watermark = st.get("watermark")
    mode = "full"
    if not args.full and watermark and last_full and now - last_full < timedelta(hours=FULL_EVERY_HOURS):
        mode = "incremental"

    run = {"_id": owner, "mode": mode, "db": args.db, "started_at": now, "status": "running"}
    db["momentus_sync_runs"].insert_one(run)
    stats = {}
    t0 = time.monotonic()
    api = MomentusClient(client_id, client_secret)
    try:
        log.info("Debut synchro %s (base %s)", mode, args.db)
        venue_ids = sync_references(api, db, now, stats)
        if mode == "full":
            full_sync(api, db, venue_ids, now, stats)
        else:
            since = watermark.replace(tzinfo=timezone.utc) - timedelta(minutes=OVERLAP_MINUTES)
            incremental_sync(api, db, venue_ids, since, now, stats)
        update = {"watermark": now, "last_success_at": now, "last_mode": mode, "last_stats": stats,
                  "last_error": None}
        if mode == "full":
            update["last_full_at"] = now
        state.update_one({"_id": "state"}, {"$set": update})
        duration = round(time.monotonic() - t0, 1)
        db["momentus_sync_runs"].update_one({"_id": owner}, {"$set": {
            "status": "ok", "ended_at": _utcnow(), "duration_s": duration,
            "api_calls": api.calls, "stats": stats}})
        msg = (f"{mode} : {stats.get('events', 0)} evenements, {stats.get('functions', 0)} fonctions, "
               f"{api.calls} appels, {duration} s")
        log.info("OK %s | %s", msg, json.dumps(stats, default=str))
        _update_cron_status("ok", msg)
        return 0
    except Exception as exc:
        log.exception("Echec synchro %s", mode)
        err = str(exc)[:500]
        state.update_one({"_id": "state"}, {"$set": {"last_error": err, "last_error_at": now}})
        db["momentus_sync_runs"].update_one({"_id": owner}, {"$set": {
            "status": "error", "ended_at": _utcnow(), "error": err,
            "api_calls": api.calls, "stats": stats}})
        _update_cron_status("error", err[:200])
        return 1
    finally:
        _release_lock(state, owner)
        mongo.close()


if __name__ == "__main__":
    sys.exit(main())
