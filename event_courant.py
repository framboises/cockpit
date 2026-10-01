"""Evenement(s) du jour : source UNIQUE de la question "sur quoi travaille-t-on
maintenant ?". Module pur (ni Flask ni Mongo propre : `db` en argument).

Regles (validees avec l'exploitation, 01/10/2026) :

- SAISON est la main courante permanente du site. Son parametrage ne porte
  AUCUNE date (ni montage, ni demontage, ni jours publics, ni course) : il est
  reconnu par son NOM, jamais par ses dates. Une annee civile = un SAISON/<annee>.
- Une epreuve est active de montage.start a demontage.end : le montage et le
  demontage appartiennent a l'epreuve, pas a la saison.
- Bascule : pendant la fenetre d'une epreuve, tout part dans l'epreuve (fiches,
  creation, tablettes). SAISON reprend la main en dehors.
- Plusieurs epreuves simultanees : la priorite est CHOISIE par un admin
  (`cockpit_settings{_id:"event_priority"}.order`, liste ordonnee de
  {event, year}, PUT /api/event/priority). Sans choix, l'epreuve deja en cours
  (fenetre ouverte la premiere) garde la main : rien ne bascule tout seul.
  (La premiere version priorisait les jours publics puis la course la plus
  proche : refusee par l'exploitation le 01/10/2026.) Les autres restent
  ACTIVES : tablettes et dispatch voient les fiches de toutes les epreuves
  actives, et un poste peut choisir explicitement une epreuve secondaire.
- Rien n'est jamais deplace : une fiche garde l'evenement qu'elle avait a sa
  creation (synchro Prysm comprise), meme si la priorite change ensuite.

`active_events` rend la liste ordonnee (prioritaire en tete), SAISON toujours
en dernier. `current_event` rend la tete.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
SAISON = "SAISON"
MAX_WINDOW_DAYS = 120  # au-dela, saisie aberrante : la fenetre est ignoree

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 60
_cache = {"at": 0.0, "db": None, "windows": None,
          "order": None, "order_at": 0.0, "order_db": None}
_lock = threading.Lock()

PRIORITY_SETTINGS_ID = "event_priority"


def load_priority_order(db):
    """Liste ordonnee [(event, year:int)] choisie par l'admin (vide = aucun choix)."""
    try:
        doc = db["cockpit_settings"].find_one({"_id": PRIORITY_SETTINGS_ID}) or {}
    except Exception:
        return []
    out = []
    for it in doc.get("order") or []:
        try:
            out.append((str(it.get("event")), int(it.get("year"))))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def priority_order(db):
    with _lock:
        if (_cache["order"] is not None and _cache["order_db"] is db
                and time.monotonic() - _cache["order_at"] < _CACHE_TTL_S):
            return _cache["order"]
    order = load_priority_order(db)
    with _lock:
        _cache.update(order=order, order_at=time.monotonic(), order_db=db)
    return order


def set_priority(db, event, year, user=None):
    """Place (event, year) en tete de l'ordre de priorite. Retourne l'ordre."""
    pair = (str(event), int(year))
    order = [p for p in load_priority_order(db) if p != pair]
    order.insert(0, pair)
    order = order[:30]
    db["cockpit_settings"].update_one(
        {"_id": PRIORITY_SETTINGS_ID},
        {"$set": {"order": [{"event": e, "year": y} for e, y in order],
                  "updated_at": datetime.now(timezone.utc), "updated_by": user}},
        upsert=True)
    invalidate()
    return order


def priority_key(win, order):
    """Cle de tri d'une epreuve active : rang choisi par l'admin d'abord, puis
    l'epreuve dont la fenetre a commence le plus tot (celle deja en cours garde
    la main tant que personne n'a choisi)."""
    pair = (win["event"], int(win["year"]))
    rank = order.index(pair) if pair in order else len(order)
    return (rank, win["start"])


def is_saison(event) -> bool:
    return str(event or "").strip().upper() == SAISON


def _parse_dt(value):
    """ISO (avec Z = UTC, naif = Paris) ou datetime -> datetime UTC aware."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = datetime.fromisoformat(s[:10])
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_PARIS)
    return dt.astimezone(timezone.utc)


def _public_days(gh):
    """Jours publics 'YYYY-MM-DD' (heure de Paris) depuis globalHoraires.dates."""
    out = set()
    for d in gh.get("dates") or []:
        raw = d.get("date") if isinstance(d, dict) else d
        dt = _parse_dt(raw)
        if dt is not None:
            # Une date sans heure est un jour Paris ; avec heure UTC on la ramene
            # au jour Paris correspondant.
            out.add(dt.astimezone(TZ_PARIS).strftime("%Y-%m-%d"))
        elif isinstance(raw, str) and len(raw) >= 10:
            out.add(raw[:10])
    return out


def _load_windows(db):
    """Fenetres des epreuves (hors SAISON et hors documents techniques)."""
    wins = []
    proj = {"event": 1, "year": 1, "data.globalHoraires.montage": 1,
            "data.globalHoraires.demontage": 1, "data.globalHoraires.dates": 1,
            "data.globalHoraires.race": 1, "data.race": 1}
    for doc in db["parametrages"].find({}, proj):
        ev = doc.get("event")
        if not ev or is_saison(ev) or str(ev).startswith("__"):
            continue
        try:
            yr = int(doc.get("year"))
        except (TypeError, ValueError):
            continue
        data = doc.get("data") or {}
        gh = data.get("globalHoraires") or {}
        start = _parse_dt((gh.get("montage") or {}).get("start"))
        end = _parse_dt((gh.get("demontage") or {}).get("end"))
        days = _public_days(gh)
        if not start or not end:
            # Repli sur les jours publics si montage/demontage manquent
            if not days:
                continue
            start = start or datetime.fromisoformat(min(days)).replace(tzinfo=TZ_PARIS).astimezone(timezone.utc)
            end = end or (datetime.fromisoformat(max(days)).replace(tzinfo=TZ_PARIS)
                          + timedelta(days=1)).astimezone(timezone.utc)
        if (end - start).days > MAX_WINDOW_DAYS or end <= start:
            # Saisie aberrante (ex. 24H AUTOS 2024 : demontage date de 2026,
            # fenetre de 760 j) : l'epreuve resterait active deux ans.
            logger.warning("event_courant: fenetre ignoree %s %s (%s -> %s)", ev, yr, start, end)
            continue
        race = _parse_dt(data.get("race") or gh.get("race"))
        wins.append({"event": ev, "year": yr, "start": start, "end": end,
                     "public_days": days, "race": race})
    return wins


def windows(db):
    with _lock:
        now = time.monotonic()
        if (_cache["windows"] is not None and _cache["db"] is db
                and now - _cache["at"] < _CACHE_TTL_S):
            return _cache["windows"]
    wins = _load_windows(db)
    with _lock:
        _cache.update(at=time.monotonic(), db=db, windows=wins)
    return wins


def invalidate():
    with _lock:
        _cache["windows"] = None
        _cache["order"] = None


def saison_year(now=None) -> int:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(TZ_PARIS).year


def _phase(win, now):
    day = now.astimezone(TZ_PARIS).strftime("%Y-%m-%d")
    if day in win["public_days"]:
        return "public"
    if win["race"] is not None and now >= win["race"]:
        return "demontage"
    if win["public_days"] and day > max(win["public_days"]):
        return "demontage"
    return "montage"


def active_events(db, now=None):
    """Liste ordonnee des evenements actifs a `now` :
    [{event, year, phase, race, kind}], epreuves d'abord (prioritaire en
    tete), SAISON toujours en dernier (kind='saison', phase=None)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    order = priority_order(db)
    wins = [w for w in windows(db) if w["start"] <= now <= w["end"]]
    wins.sort(key=lambda w: priority_key(w, order))
    act = [{"event": w["event"], "year": w["year"], "phase": _phase(w, now),
            "race": w["race"], "kind": "epreuve",
            "chosen": (w["event"], int(w["year"])) in order}
           for w in wins]
    act.append({"event": SAISON, "year": saison_year(now), "phase": None,
                "race": None, "kind": "saison"})
    return act


def current_event(db, now=None):
    """(event, year:int) prioritaire a `now` : epreuve active sinon SAISON."""
    a = active_events(db, now)[0]
    return a["event"], a["year"]


def event_for_datetime(db, dt):
    """Evenement auquel rattacher un fait date `dt` (fiche, scan...)."""
    return current_event(db, _parse_dt(dt))


def active_pairs(db, now=None, include_previous_saison=False):
    """[(event, year:int)] des evenements actifs. Avec include_previous_saison,
    ajoute SAISON/<annee-1> les premiers jours de janvier pour ne pas perdre les
    fiches ouvertes la veille du changement d'annee."""
    pairs = [(a["event"], a["year"]) for a in active_events(db, now)]
    if include_previous_saison:
        pairs.append((SAISON, saison_year(now) - 1))
    return pairs


def is_active(db, event, year, now=None) -> bool:
    try:
        y = int(year)
    except (TypeError, ValueError):
        return False
    return any(e == event and yr == y for e, yr in active_pairs(db, now))


def pairs_filter(pairs):
    """Filtre Mongo couvrant des paires (event, year) quel que soit le type
    stocke pour year (int dans pcorg, chaine dans parametrages/field_devices)."""
    ors = []
    seen = set()
    for ev, yr in pairs:
        key = (ev, str(yr))
        if key in seen:
            continue
        seen.add(key)
        try:
            yi = int(yr)
            years = [yi, str(yi)]
        except (TypeError, ValueError):
            years = [yr]
        ors.append({"event": ev, "year": {"$in": years}})
    if not ors:
        return {"_id": {"$exists": False}}
    return ors[0] if len(ors) == 1 else {"$or": ors}


def payload(db, now=None):
    """Forme JSON pour l'API /api/event/current."""
    acts = active_events(db, now)
    epreuves = [a for a in acts if a["kind"] == "epreuve"]
    return {
        "current": {"event": acts[0]["event"], "year": acts[0]["year"],
                    "phase": acts[0]["phase"], "kind": acts[0]["kind"]},
        "active": [{"event": a["event"], "year": a["year"], "phase": a["phase"],
                    "kind": a["kind"], "chosen": bool(a.get("chosen"))} for a in acts],
        # Conflit = plusieurs epreuves actives. `priority_chosen` : la tete vient
        # d'un choix admin (sinon : l'epreuve deja en cours garde la main).
        "conflict": len(epreuves) > 1,
        "priority_chosen": bool(epreuves and epreuves[0].get("chosen")),
    }
