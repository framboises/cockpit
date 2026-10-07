#!/usr/bin/env python3
"""
voisins_sync.py - Evenements des voisins du circuit -> MongoDB + timeline SAISON.

Ce qui se passe a cote du site et pese sur la circulation et le stationnement :
  - Antares (salle de spectacle) : concerts, spectacles, humour, sport ponctuel.
    Source : API REST WordPress publique du site antaresarena.com
    (/wp-json/wp/v2/event, champ ACF `sessions[].session_date`, heure de Paris).
  - MSB (Le Mans Sarthe Basket), matchs A DOMICILE a Antares.
    Source : API du site lnb.fr (api-prod.lnb.fr). Jeton anonyme de 15 min
    obtenu par GET https://lnb.fr/api/token (celui du site public), puis
    POST match/v3/getCalendar par equipe. L'identifiant d'equipe change a
    chaque saison : il est relu a chaque passage (getMainCompetition ->
    getCompetitionTeams). `match_time_utc` est en UTC au format jj-mm-aa.
  - Le Mans FC, matchs A DOMICILE au stade Marie-Marvingt (MMArena).
    Source : API publique ESPN (site.api.espn.com), equipe 2697, ligues
    fra.1 / fra.2 / coupe de France. `timeValid: false` = date posee mais
    horaire pas encore fixe par la LFP (diffuseurs) : "horaire a confirmer".

Aucune de ces sources n'est contractuelle : ce sont les donnees publiques des
sites. Chaque source est independante : l'echec de l'une n'empeche pas les
autres, et une source en echec ne supprime rien.

Collection `voisins_events` (un doc par seance / match) :
  {_id, source, venue ("antares"|"stade"), title, subtitle, kind, date,
   time ("HH:MM" ou ""), time_tbc, status ("prevu"|"annule"), url,
   competition, opponent, _sync {first_seen, last_seen, deleted_at}}
Rien n'est jamais supprime : un evenement A VENIR disparu de sa source recoit
`_sync.deleted_at` (garde-fou : pas de reconciliation si la source rend moins
de RECONCILE_MIN_RATIO de l'existant a venir). Les evenements passes ne sont
jamais reconcilies (ESPN ne rend plus les matchs joues en mode fixture).

Timeline SAISON : vignettes `origin: "voisins"`, `category: "Voisins"`,
memes regles d'ecriture que momentus_timeline (atomique par date, champs
operateur conserves, vignette editee en entier = sortie de la synchro).

Lance par tache planifiee (scripts/install_voisins_task.ps1, 2 fois par jour) :
    python voisins_sync.py              # synchro + timeline
    python voisins_sync.py --dry-run    # affiche sans rien ecrire
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TASK_NAME = "Sync Voisins"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
TZ_PARIS = ZoneInfo("Europe/Paris")

HTTP_TIMEOUT = int(os.getenv("VOISINS_TIMEOUT_SECONDS", "30"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Cockpit-ACO/1.0"
RECONCILE_MIN_RATIO = 0.5
MAX_FUTURE_DAYS = 400
KEEP_PAST_DAYS = 7

ANTARES_API = "https://www.antaresarena.com/wp-json/wp/v2/event"
LNB_TOKEN_URL = "https://lnb.fr/api/token"
LNB_API = "https://api-prod.lnb.fr"
LNB_TEAM_PATTERN = re.compile(r"\ble mans\b", re.IGNORECASE)
ESPN_TEAM_ID = "2697"
ESPN_LEAGUES = ("fra.1", "fra.2", "fra.coupe_de_france")
ESPN_SCHEDULE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{team}/schedule"

VENUES = {"antares": "Antarès", "stade": "Stade MMArena"}
SOURCES = ("antares", "msb", "fcmans")

ORIGIN = "voisins"
CATEGORY = "Voisins"
COLLECTION = "voisins_events"

ANTARES_KINDS = {
    "concert": "Concert", "spectacle": "Spectacle", "humour": "Humour",
    "one-man-show": "One-man-show", "comedie-musicale": "Comédie musicale",
    "danse": "Danse", "sport": "Sport", "evenement": "Evénement",
}

# Flux du public (minutes) : arrivees AVANT l'heure de l'evenement, fin
# estimee APRES. Ordres de grandeur pour le trafic, jamais affiches sans le
# mot "estime". Par source, puis par type pour Antares.
FLUX = {
    "fcmans": (120, 115),     # parkings ouverts ~2 h avant ; 90 min + mi-temps + arrets
    "msb": (75, 135),         # 4 x 10 min de jeu effectif ~ 2 h, + sortie
    "antares": (90, 150),     # ouverture des portes ~1 h 30 avant ; 1re partie + concert
}
FLUX_KIND = {"Humour": (60, 120), "One-man-show": (60, 120), "Spectacle": (75, 150)}

KIND_ICONS = {
    "Football": "sports_soccer", "Basket": "sports_basketball", "Concert": "music_note",
    "Spectacle": "theater_comedy", "Comédie musicale": "theater_comedy", "Danse": "theater_comedy",
    "Humour": "sentiment_very_satisfied", "One-man-show": "sentiment_very_satisfied",
    "Sport": "sports",
}

log = logging.getLogger("voisins_sync")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
_UNSAFE = re.compile(r"[<>\"`\x00-\x1f]")


def _txt(value, limit=160):
    """Texte affichable : entites HTML decodees, balises et caracteres
    dangereux retires (la timeline injecte activity / place en innerHTML)."""
    s = html.unescape(str(value or ""))
    s = re.sub(r"<[^>]*>", " ", s)
    s = _UNSAFE.sub("", s)
    return " ".join(s.split())[:limit]


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def paris_today(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(TZ_PARIS).date()


def _paris_from_utc(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_PARIS)


def _season_year(today):
    """Saison sportive (annee de debut) : juillet -> juin."""
    return today.year if today.month >= 7 else today.year - 1


# ---------------------------------------------------------------------------
# Parseurs (purs : JSON en entree, liste de docs en sortie)
# ---------------------------------------------------------------------------
def parse_antares(events):
    """Une seance = un doc. `session_date` est en heure de Paris."""
    out = []
    for ev in events or []:
        acf = ev.get("acf") or {}
        title = _txt((ev.get("title") or {}).get("rendered"))
        if not title:
            continue
        types = [c[5:] for c in ev.get("class_list") or []
                 if isinstance(c, str) and c.startswith("type-") and c != "type-event"]
        kind = ANTARES_KINDS.get(types[0], types[0].capitalize()) if types else ""
        for s in acf.get("sessions") or []:
            raw = str((s or {}).get("session_date") or "").strip()
            m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})", raw)
            if not m:
                continue
            st = str(s.get("status_event") or "")
            out.append({
                "_id": f"antares|{ev.get('id')}|{m.group(1)}T{m.group(2)}{m.group(3)}",
                "source": "antares", "venue": "antares",
                "title": title, "subtitle": _txt(acf.get("subtitle"), 120),
                "kind": kind, "date": m.group(1), "time": f"{m.group(2)}:{m.group(3)}",
                "time_tbc": False,
                "status": "annule" if st == "cancelled" else "prevu",
                "sale_status": _txt(st, 40),
                "url": str(ev.get("link") or "")[:300],
                "competition": "", "opponent": "",
            })
    return out


def _lnb_utc(value):
    """'2026-10-04T16:50:00.000Z' (ISO, UTC) -> datetime UTC. Accepte aussi
    'jj-mm-aa HH:MM:SS' (meme instant, autre rendu)."""
    s = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        y, mo, d, h, mi = (int(x) for x in m.groups())
    else:
        m = re.match(r"^(\d{2})-(\d{2})-(\d{2}) (\d{2}):(\d{2})", s)
        if not m:
            return None
        d, mo, y, h, mi = (int(x) for x in m.groups())
        y += 2000
    try:
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_lnb(calendar, team_external_id):
    """Matchs A DOMICILE de l'equipe (premiere equipe de `teams`, comme le
    nom du match 'Domicile - Exterieur')."""
    out = []
    for day in calendar or []:
        for g in (day or {}).get("data") or []:
            teams = g.get("teams") or []
            if len(teams) < 2 or str(teams[0].get("external_id")) != str(team_external_id):
                continue
            dt = _lnb_utc(g.get("match_time_utc"))
            if not dt:
                continue
            loc = _paris_from_utc(dt)
            opp = _txt(teams[1].get("team_name") or teams[1].get("club_name"), 80)
            status = str(g.get("match_status") or "").upper()
            comp = _txt(g.get("competition_name"), 60)
            rnd = _txt(g.get("round_description"), 40)
            out.append({
                "_id": f"msb|{g.get('external_id') or g.get('match_id')}",
                "source": "msb", "venue": "antares",
                "title": f"MSB - {opp}", "subtitle": " · ".join(p for p in (comp, rnd) if p),
                "kind": "Basket", "date": loc.date().isoformat(), "time": loc.strftime("%H:%M"),
                "time_tbc": False,
                "status": "annule" if status in ("CANCELLED", "CANCELED", "POSTPONED") else "prevu",
                "sale_status": "", "url": f"https://lnb.fr/match-center/{g.get('match_id')}" if g.get("match_id") else "",
                "competition": comp, "opponent": opp,
            })
    return out


def parse_espn(schedule, team_id=ESPN_TEAM_ID):
    """Matchs A DOMICILE du Mans FC. `timeValid: false` = horaire non fixe."""
    out = []
    league = _txt(((schedule or {}).get("league") or {}).get("name") or "", 60)
    for ev in (schedule or {}).get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not home or not away or str((home.get("team") or {}).get("id") or home.get("id")) != str(team_id):
            continue
        try:
            dt = datetime.fromisoformat(str(ev.get("date")).replace("Z", "+00:00"))
        except ValueError:
            continue
        loc = _paris_from_utc(dt)
        tbc = ev.get("timeValid") is False
        opp = _txt((away.get("team") or {}).get("displayName"), 80)
        st = (((comp.get("status") or ev.get("status") or {}).get("type") or {}).get("name") or "").upper()
        out.append({
            "_id": f"fcmans|{ev.get('id')}",
            "source": "fcmans", "venue": "stade",
            "title": f"Le Mans FC - {opp}", "subtitle": league,
            "kind": "Football", "date": loc.date().isoformat(), "time": "" if tbc else loc.strftime("%H:%M"),
            "time_tbc": tbc,
            "status": "annule" if st in ("STATUS_CANCELED", "STATUS_POSTPONED") else "prevu",
            "sale_status": "", "url": "",
            "competition": league, "opponent": opp,
        })
    return out


# ---------------------------------------------------------------------------
# Collecte (HTTP)
# ---------------------------------------------------------------------------
def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def fetch_antares(http):
    events, page, pages = [], 1, 1
    while page <= pages and page <= 20:
        r = http.get(ANTARES_API, timeout=HTTP_TIMEOUT, params={
            "per_page": 100, "page": page,
            "_fields": "id,link,title,class_list,acf.sessions,acf.subtitle"})
        r.raise_for_status()
        pages = int(r.headers.get("X-WP-TotalPages") or 1)
        events += r.json()
        page += 1
    return parse_antares(events)


def fetch_msb(http, today):
    tok = http.get(LNB_TOKEN_URL, timeout=HTTP_TIMEOUT, headers={"Referer": "https://lnb.fr/fr"})
    tok.raise_for_status()
    h = {"Authorization": "Bearer " + tok.json()["token"], "Origin": "https://lnb.fr",
         "Referer": "https://lnb.fr/", "language_code": "fr"}
    year = _season_year(today)
    comps = http.get(f"{LNB_API}/competition/getMainCompetition", timeout=HTTP_TIMEOUT, headers=h)
    comps.raise_for_status()
    team = None
    for c in comps.json().get("data") or []:
        if str(c.get("year")) != str(year):
            continue
        t = http.get(f"{LNB_API}/competition/getCompetitionTeams", timeout=HTTP_TIMEOUT, headers=h,
                     params={"competition_external_id": c.get("external_id")})
        t.raise_for_status()
        team = next((x for x in t.json().get("data") or []
                     if LNB_TEAM_PATTERN.search(str(x.get("team_name") or ""))), None)
        if team:
            break
    if not team:
        raise RuntimeError(f"equipe du Mans introuvable dans les competitions LNB {year}")
    body = {"year": year, "competition_abbrev": "", "division_external_id": 0,
            "team_external_id": int(team["external_id"]), "round_number": 0, "phase_id": 0,
            "tournament_number": 0}
    r = http.post(f"{LNB_API}/match/v3/getCalendar", json=body, timeout=HTTP_TIMEOUT, headers=h)
    r.raise_for_status()
    return parse_lnb(r.json().get("data"), team["external_id"])


def fetch_fcmans(http):
    out, seen, ok = [], set(), 0
    for league in ESPN_LEAGUES:
        for params in ({}, {"fixture": "true"}):
            try:
                r = http.get(ESPN_SCHEDULE.format(league=league, team=ESPN_TEAM_ID),
                             params=params, timeout=HTTP_TIMEOUT)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.warning("ESPN %s %s : %s", league, params, exc)
                continue
            ok += 1
            for doc in parse_espn(data):
                if doc["_id"] not in seen:
                    seen.add(doc["_id"])
                    out.append(doc)
    if not ok:
        raise RuntimeError("aucune ligue ESPN joignable")
    return out


def collect(today, http=None):
    """{source: [docs]} pour les sources joignables, {source: erreur} sinon."""
    http = http or _session()
    fetchers = {"antares": lambda: fetch_antares(http),
                "msb": lambda: fetch_msb(http, today),
                "fcmans": lambda: fetch_fcmans(http)}
    got, errors = {}, {}
    for src, fn in fetchers.items():
        try:
            got[src] = fn()
        except Exception as exc:
            log.exception("Source %s en echec", src)
            errors[src] = str(exc)[:300]
    return got, errors


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
def store(db, got, today, now=None):
    """Upsert des docs collectes + reconciliation des disparus A VENIR, par
    source. Rend les statistiques par source."""
    now = now or _utcnow()
    col = db[COLLECTION]
    stats = {}
    t = today.isoformat()
    for src, docs in got.items():
        st = {"fetched": len(docs), "new": 0, "deleted": 0, "reconciled": False}
        for d in docs:
            res = col.update_one(
                {"_id": d["_id"]},
                {"$set": {**{k: v for k, v in d.items() if k != "_id"},
                          "_sync.last_seen": now, "_sync.deleted_at": None},
                 "$setOnInsert": {"_sync.first_seen": now}},
                upsert=True)
            if getattr(res, "upserted_id", None) is not None:
                st["new"] += 1
        seen = {d["_id"] for d in docs}
        existing = [x["_id"] for x in col.find(
            {"source": src, "date": {"$gte": t}, "_sync.deleted_at": None}, {"_id": 1})]
        gone = [i for i in existing if i not in seen]
        future_seen = sum(1 for d in docs if d["date"] >= t)
        if gone and future_seen >= RECONCILE_MIN_RATIO * len(existing):
            col.update_many({"_id": {"$in": gone}}, {"$set": {"_sync.deleted_at": now}})
            st["deleted"] = len(gone)
            st["reconciled"] = True
        elif gone:
            log.warning("%s : %d evenements absents mais source trop maigre (%d/%d), pas de reconciliation",
                        src, len(gone), future_seen, len(existing))
        stats[src] = st
    return stats


def active_events(db, d_from, d_to, venues=None):
    """Evenements actifs (ni supprimes ni annules) sur [d_from, d_to]."""
    q = {"_sync.deleted_at": None, "status": {"$ne": "annule"},
         "date": {"$gte": d_from.isoformat(), "$lte": d_to.isoformat()}}
    if venues:
        q["venue"] = {"$in": sorted(venues)}
    out = []
    for d in db[COLLECTION].find(q):
        # Refiltre en Python (doubles de test sans filtre Mongo)
        if (d.get("_sync") or {}).get("deleted_at") or d.get("status") == "annule":
            continue
        if not d_from.isoformat() <= str(d.get("date") or "") <= d_to.isoformat():
            continue
        if venues and d.get("venue") not in venues:
            continue
        out.append(d)
    out.sort(key=lambda d: (d["date"], d.get("time") or "99", d.get("title") or ""))
    return out


# ---------------------------------------------------------------------------
# Timeline SAISON
# ---------------------------------------------------------------------------
def _mk_id(doc_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "voisins-timeline|" + doc_id))


def _shift(hhmm, minutes):
    """'20:45' + 115 -> '22:40' (modulo 24 h : l'heure seule, comme la timeline)."""
    m = re.match(r"^(\d{2}):(\d{2})$", str(hhmm or ""))
    if not m:
        return ""
    t = (int(m.group(1)) * 60 + int(m.group(2)) + minutes) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def flux(d):
    """(debut des arrivees, fin estimee) 'HH:MM', ou ('', '') sans horaire."""
    if not d.get("time") or d.get("time_tbc"):
        return "", ""
    before, after = FLUX.get(d.get("source"), (90, 150))
    if d.get("source") == "antares" and d.get("kind") in FLUX_KIND:
        before, after = FLUX_KIND[d["kind"]]
    return _shift(d["time"], -before), _shift(d["time"], after)


def public_event(d):
    """Projection affichable (API, indicateurs, bandeau)."""
    arr, end = flux(d)
    return {
        "id": d["_id"], "source": d.get("source"), "venue": d.get("venue"),
        "venue_label": VENUES.get(d.get("venue"), ""), "title": _txt(d.get("title")),
        "subtitle": _txt(d.get("subtitle"), 120), "kind": d.get("kind") or "",
        "icon": KIND_ICONS.get(d.get("kind"), "stadium"),
        "date": d.get("date"), "time": d.get("time") or "", "time_tbc": bool(d.get("time_tbc")),
        "arrivals": arr, "end_est": end,
        "url": d.get("url") if str(d.get("url") or "").startswith("https://") else "",
    }


def _duration(a, b):
    if not a or not b:
        return ""
    x = int(a[:2]) * 60 + int(a[3:])
    y = int(b[:2]) * 60 + int(b[3:])
    m = (y - x) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _vignettes(d):
    """Une vignette a l'heure de l'evenement (jusqu'a la fin estimee), plus
    deux vignettes de flux quand l'horaire est connu : arrivees du public et
    sortie. Pas de flux sans horaire (match a la date posee, heure non fixee)."""
    p = public_event(d)
    venue = p["venue_label"]
    title = p["title"].replace("/", "-")
    activity = f"{venue} : {title}" if d.get("source") == "antares" else f"{title} ({venue})"
    arr, end = p["arrivals"], p["end_est"]
    bits = ["Evenement voisin", venue, p["kind"]]
    if p["subtitle"]:
        bits.append(p["subtitle"])
    if p["time_tbc"]:
        bits.append("horaire a confirmer")
    if arr:
        bits.append(f"arrivees du public des {arr} environ, fin estimee vers {end}")
    if d.get("sale_status") == "soon":
        bits.append("billetterie pas encore ouverte")
    bits.append({"antares": "source antaresarena.com", "msb": "source lnb.fr",
                 "fcmans": "source ESPN"}.get(d.get("source"), ""))
    common = {"date": d["date"], "category": CATEGORY, "place": venue, "type": "Timetable",
              "origin": ORIGIN, "preparation_checked": "", "voisins_id": d["_id"],
              "voisins_venue": d.get("venue"), "voisins_source": d.get("source"),
              "voisins_icon": p["icon"]}
    out = [dict(common, **{
        "_id": _mk_id(d["_id"]), "start": p["time"], "end": end, "duration": _duration(p["time"], end),
        "activity": activity[:200], "department": p["kind"],
        "remark": " | ".join(b.replace("/", "-") for b in bits if b)[:600],
        "voisins_role": "evenement", "voisins_time_tbc": p["time_tbc"]})]
    if arr:
        out.append(dict(common, **{
            "_id": _mk_id(d["_id"] + "|arrivees"), "start": arr, "end": p["time"],
            "duration": _duration(arr, p["time"]),
            "activity": f"Arrivées du public {venue} - {title}"[:200], "department": "Flux public",
            "remark": f"Evenement voisin | {venue} | {title} a {p['time']} | afflux du public estime "
                      f"(ordre de grandeur, pas une donnee de l'organisateur)".replace("/", "-")[:600],
            "voisins_role": "arrivees"}))
        out.append(dict(common, **{
            "_id": _mk_id(d["_id"] + "|sortie"), "start": end, "end": _shift(end, 45),
            "duration": "00:45",
            "activity": f"Sortie du public {venue} - {title}"[:200], "department": "Flux public",
            "remark": f"Evenement voisin | {venue} | {title} a {p['time']} | sortie du public estimee "
                      f"(ordre de grandeur, pas une donnee de l'organisateur)".replace("/", "-")[:600],
            "voisins_role": "sortie"}))
    return out


def build_items(db, d_from, d_to):
    out = defaultdict(list)
    for d in active_events(db, d_from, d_to):
        out[d["date"]].extend(_vignettes(d))
    return out


def sync_timeline(db, now=None, dry_run=False):
    """Ecrit la veille -> dernier evenement voisin connu (borne a
    MAX_FUTURE_DAYS) dans SAISON/<annee>. Meme ecriture que Momentus."""
    import momentus_timeline as MT
    today = paris_today(now)
    d_from = today - timedelta(days=1)
    last = db[COLLECTION].find_one({"_sync.deleted_at": None}, {"date": 1}, sort=[("date", -1)])
    d_to = today + timedelta(days=14)
    if last and last.get("date"):
        try:
            d_to = max(d_to, date.fromisoformat(last["date"]))
        except ValueError:
            pass
    d_to = min(d_to, today + timedelta(days=MAX_FUTURE_DAYS))
    items = build_items(db, d_from, d_to)
    prune_before = (today - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    days_by_year = defaultdict(list)
    x = d_from
    while x <= d_to:
        days_by_year[x.year].append(x.isoformat())
        x += timedelta(days=1)
    docs = [MT._sync_year_doc(db, y, days, items, prune_before, dry_run, origin=ORIGIN)
            for y, days in sorted(days_by_year.items())]
    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "dry_run": dry_run,
            "items": items, "docs": docs}


# ---------------------------------------------------------------------------
# Lancement
# ---------------------------------------------------------------------------
def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logfile = os.path.join(LOG_DIR, f"voisins_sync-{datetime.now():%Y%m%d}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [VoisinsSync] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(logfile, encoding="utf-8")],
    )


def _update_cron_status(status, message=""):
    """Meme format que momentus_sync.py / pcorg_sync.py."""
    path = os.getenv("CRON_STATUS_FILE", "").strip() or os.path.join(SCRIPT_DIR, "cron_status.json")
    tasks = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            tasks = data if isinstance(data, list) else data.get("tasks", [])
        except Exception:
            tasks = []
    entry = next((t for t in tasks if t.get("name") == TASK_NAME), None)
    if entry is None:
        entry = {"name": TASK_NAME}
        tasks.append(entry)
    entry["status"] = status
    entry["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if message:
        entry["message"] = message
    else:
        entry.pop("message", None)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(tasks, handle, indent=2)
    os.replace(tmp_path, path)


def main(argv=None):
    from pymongo import MongoClient
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    ap = argparse.ArgumentParser(description="Evenements voisins (Antares, MSB, Le Mans FC)")
    ap.add_argument("--db", default="titan" if env in {"prod", "production"} else "titan_dev")
    ap.add_argument("--dry-run", action="store_true", help="Collecte et affiche, n'ecrit rien")
    args = ap.parse_args(argv)
    _setup_logging()
    today = paris_today()
    got, errors = collect(today)
    if args.dry_run:
        for src, docs in got.items():
            print(f"== {src} : {len(docs)} evenements")
            for d in sorted(docs, key=lambda d: (d["date"], d["time"])):
                if d["date"] >= today.isoformat():
                    print(f"  {d['date']} {d['time'] or '--:--'}{' (a conf.)' if d['time_tbc'] else ''}"
                          f"  {d['venue']:8} {d['status']:7} {d['title']}")
        for src, err in errors.items():
            print(f"!! {src} : {err}")
        return 0 if got else 1
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=5000)[args.db]
    try:
        db[COLLECTION].create_index([("date", 1), ("venue", 1)])
        stats = store(db, got, today)
        tl = sync_timeline(db)
        msg = ", ".join(f"{s} {st['fetched']}" for s, st in stats.items())
        msg += " | timeline " + ", ".join(f"{d['year']}: {d['items']} vignettes" for d in tl["docs"])
        if errors:
            msg += " | ECHEC " + ", ".join(errors)
        log.info("%s | %s", msg, json.dumps(stats))
        _update_cron_status("error" if errors else "ok", msg[:300])
        return 1 if errors and not got else 0
    except Exception as exc:
        log.exception("Echec synchro voisins")
        _update_cron_status("error", str(exc)[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
