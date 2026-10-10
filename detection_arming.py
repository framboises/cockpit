# detection_arming.py -- activation (armement) de la detection d'intrusion
#
# Partage par la page Detection (detection.py) et par le recepteur
# (PCA\SCRIPTS\intrusions.py, qui importe ce module depuis le dossier cockpit)
# pour qu'ils appliquent exactement la meme regle. Ne jamais la dupliquer.
#
# Groupes (collection detection_groups) :
#   {name, color, members: [{camera_id, channel}], arming: {
#      mode: "schedule" | "always" | "off",
#      weeks: [{id, name, grid: [7 chaines de 96 caracteres "0"/"1"]}],
#             grid[0] = lundi ; 1 caractere = 1 quart d'heure (00:00-00:15...)
#      default_week: id de la semaine type par defaut,
#      exceptions: [{id, label, kind: "dates" | "event",
#                    start, end: "AAAA-MM-JJ" (inclus), yearly: bool,   # dates
#                    event, span: "window" | "public",                  # epreuve
#                    action: "week" | "on" | "off", week_id}],
#      holidays: "normal" | "sunday" | "on" | "off",
#      override: {state: "on" | "off", until: datetime UTC, by} | null }}
#
# PRIORITE (affichee dans la page) :
#   forcage temporaire > mode (toujours actif / en pause) > exception datee
#   (la premiere de la liste qui couvre le jour) > jour ferie > semaine type.
# Une exception "event" suit les dates d'une epreuve de Cockpit
# (parametrages, via event_courant) : montage -> demontage ("window") ou jours
# publics ("public"), quelle que soit l'annee.
#
# Regle de canal : ACTIVE si au moins un de ses groupes l'est ; un canal
# "detection d'intrusion" dans aucun groupe est toujours actif. Hors
# activation, le recepteur jette tout (ni photo, ni intrusion, ni alerte).
#
# Compatibilite : l'ancien format {slots: [{days, start, end}]} est converti
# a la volee en semaine type (normalize).
import time
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
MODES = ("always", "off", "schedule")
HOLIDAY_RULES = ("normal", "sunday", "on", "off")
QUARTERS = 96
EMPTY_DAY = "0" * QUARTERS
_CACHE_TTL_S = 20
_cache = {"at": 0.0, "groups": None}
_events_cache = {"at": 0.0, "db": None, "windows": []}


# ---------------------------------------------------------------------------
# Jours feries francais (metropole)
# ---------------------------------------------------------------------------
def _easter(year):
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


_holiday_cache = {}


def holidays(year):
    """{date: nom} des jours feries de l'annee."""
    if year not in _holiday_cache:
        e = _easter(year)
        _holiday_cache[year] = {
            date(year, 1, 1): "Jour de l'an", e + timedelta(days=1): "Lundi de Paques",
            date(year, 5, 1): "Fete du travail", date(year, 5, 8): "Victoire 1945",
            e + timedelta(days=39): "Ascension", e + timedelta(days=50): "Lundi de Pentecote",
            date(year, 7, 14): "Fete nationale", date(year, 8, 15): "Assomption",
            date(year, 11, 1): "Toussaint", date(year, 11, 11): "Armistice",
            date(year, 12, 25): "Noel",
        }
    return _holiday_cache[year]


# ---------------------------------------------------------------------------
# Normalisation (ancien format -> semaines types)
# ---------------------------------------------------------------------------
def _hm(s):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def slots_to_grid(slots):
    """Ancien format {days 1-7, start, end} -> 7 chaines de 96 quarts d'heure."""
    rows = [list(EMPTY_DAY) for _ in range(7)]

    def fill(day_idx, a, b):
        for q in range(a // 15, min(QUARTERS, -(-b // 15))):
            rows[day_idx % 7][q] = "1"

    for s in slots or []:
        a, b = _hm(s.get("start")), _hm(s.get("end"))
        if a is None or b is None:
            continue
        for d in s.get("days") or []:
            i = int(d) - 1
            if a < b:
                fill(i, a, b)
            elif a > b:  # passe minuit
                fill(i, a, 1440)
                fill(i + 1, 0, b)
            else:  # debut = fin : journee entiere
                fill(i, 0, 1440)
    return ["".join(r) for r in rows]


def normalize(arming):
    """Arming complet (valeurs par defaut, conversion de l'ancien format)."""
    a = dict(arming or {})
    a["mode"] = a.get("mode") if a.get("mode") in MODES else "always"
    weeks = [w for w in a.get("weeks") or [] if isinstance(w, dict) and w.get("id")]
    if not weeks:
        weeks = [{"id": "w1", "name": "Semaine normale", "grid": slots_to_grid(a.get("slots"))}]
    for w in weeks:
        g = list(w.get("grid") or [])
        w["grid"] = [(str(g[i]) if i < len(g) else EMPTY_DAY).ljust(QUARTERS, "0")[:QUARTERS] for i in range(7)]
    a["weeks"] = weeks
    if a.get("default_week") not in [w["id"] for w in weeks]:
        a["default_week"] = weeks[0]["id"]
    a["exceptions"] = list(a.get("exceptions") or [])
    a["holidays"] = a.get("holidays") if a.get("holidays") in HOLIDAY_RULES else "normal"
    return a


# ---------------------------------------------------------------------------
# Epreuves de Cockpit (exceptions "event")
# ---------------------------------------------------------------------------
def _event_windows(db):
    if db is None:
        return []
    now = time.monotonic()
    if _events_cache["db"] is not db or now - _events_cache["at"] > 60:
        try:
            import event_courant
            wins = event_courant.windows(db)
        except Exception:
            wins = []
        _events_cache.update({"at": now, "db": db, "windows": wins})
    return _events_cache["windows"]


def _event_covers(db, exc, day):
    for w in _event_windows(db):
        if str(w.get("event")) != str(exc.get("event")):
            continue
        if exc.get("span") == "public":
            if day.isoformat() in (w.get("public_days") or set()):
                return True
        else:
            start = w["start"].astimezone(PARIS).date()
            end = (w["end"] - timedelta(seconds=1)).astimezone(PARIS).date()
            if start <= day <= end:
                return True
    return False


def _dates_cover(exc, day):
    try:
        s = date.fromisoformat(exc.get("start"))
        e = date.fromisoformat(exc.get("end") or exc.get("start"))
    except (TypeError, ValueError):
        return False
    if not exc.get("yearly"):
        return s <= day <= e
    md, ms, me = (day.month, day.day), (s.month, s.day), (e.month, e.day)
    return ms <= md <= me if ms <= me else (md >= ms or md <= me)  # ex. 24/12 -> 02/01


# ---------------------------------------------------------------------------
# Regle du jour et etat a un instant
# ---------------------------------------------------------------------------
def day_rule(group, day, db=None):
    """Ce qui s'applique au jour (date locale) hors forcage et hors mode :
    {kind: "week" | "on" | "off", week_id, source: "week" | "exception" | "holiday", label}."""
    a = normalize(group.get("arming"))
    for exc in a["exceptions"]:
        hit = _event_covers(db, exc, day) if exc.get("kind") == "event" else _dates_cover(exc, day)
        if hit:
            act = exc.get("action") if exc.get("action") in ("week", "on", "off") else "on"
            return {"kind": act, "week_id": exc.get("week_id") or a["default_week"],
                    "source": "exception", "label": exc.get("label") or "Exception"}
    hname = holidays(day.year).get(day)
    if hname and a["holidays"] != "normal":
        if a["holidays"] == "sunday":
            return {"kind": "week", "week_id": a["default_week"], "as_weekday": 6,
                    "source": "holiday", "label": hname + " (comme un dimanche)"}
        return {"kind": a["holidays"], "source": "holiday", "label": hname}
    week = next(w for w in a["weeks"] if w["id"] == a["default_week"])
    return {"kind": "week", "week_id": week["id"], "source": "week", "label": week["name"]}


def _day_grid(group, rule):
    """Chaine de 96 quarts d'heure du jour selon la regle."""
    if rule["kind"] == "on":
        return "1" * QUARTERS
    if rule["kind"] == "off":
        return EMPTY_DAY
    a = normalize(group.get("arming"))
    week = next((w for w in a["weeks"] if w["id"] == rule.get("week_id")), None) \
        or next(w for w in a["weeks"] if w["id"] == a["default_week"])
    return week["grid"][rule["weekday"]]


def day_grid(group, day, db=None):
    rule = day_rule(group, day, db)
    rule["weekday"] = rule.get("as_weekday", day.weekday())
    return _day_grid(group, rule), rule


def group_state(group, now_utc=None, db=None):
    """(active, source) ; source = "override" | "always" | "off" | "schedule"."""
    now_utc = now_utc or datetime.now(timezone.utc)
    arming = group.get("arming") or {}
    ov = arming.get("override") or None
    if ov and ov.get("until"):
        until = ov["until"]
        until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until
        if until > now_utc and ov.get("state") in ("on", "off"):
            return ov["state"] == "on", "override"
    mode = arming.get("mode", "always")
    if mode == "off":
        return False, "off"
    if mode != "schedule":
        return True, "always"
    loc = now_utc.astimezone(PARIS)
    grid, _rule = day_grid(group, loc.date(), db)
    return grid[(loc.hour * 60 + loc.minute) // 15] == "1", "schedule"


def explain(group, now_utc=None, db=None):
    """Libelle de ce qui s'applique maintenant (semaine type, exception, ferie)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    on, source = group_state(group, now_utc, db)
    if source != "schedule":
        return source
    _g, rule = day_grid(group, now_utc.astimezone(PARIS).date(), db)
    return rule["source"] + ":" + rule["label"]


def next_change(group, now_utc=None, horizon_days=8, db=None):
    """Instant UTC du prochain changement d'etat (minute pres), ou None."""
    now_utc = (now_utc or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    current, _src = group_state(group, now_utc, db)
    arming = group.get("arming") or {}
    if arming.get("mode") != "schedule" and not arming.get("override"):
        return None
    t = now_utc
    for _i in range(horizon_days * 96 + 96):
        # Pas d'un quart d'heure, aligne ; l'echeance d'un forcage est testee a part
        nxt = t + timedelta(minutes=15 - (t.astimezone(PARIS).minute % 15))
        ov = arming.get("override") or {}
        if ov.get("until"):
            u = ov["until"]
            u = u.replace(tzinfo=timezone.utc) if u.tzinfo is None else u
            if t < u <= nxt and group_state(group, u, db)[0] != current:
                return u
        t = nxt.replace(second=0, microsecond=0)
        if group_state(group, t, db)[0] != current:
            return t
    return None


def changes(group, start_utc, days=14, db=None, limit=8):
    """Prochains changements [(instant UTC, actif)] pour la page."""
    out = []
    t = start_utc
    end = start_utc + timedelta(days=days)
    state = group_state(group, t, db)[0]
    while len(out) < limit:
        n = next_change(group, t, horizon_days=days, db=db)
        if not n or n > end:
            break
        state = not state
        out.append((n, state))
        t = n
    return out


def month_view(group, year, month, db=None):
    """Calendrier : pour chaque jour, la regle et les plages actives."""
    a = normalize(group.get("arming"))
    first = date(year, month, 1)
    days = []
    d = first
    while d.month == month:
        if a["mode"] == "always":
            grid, rule = "1" * QUARTERS, {"source": "mode", "label": "Toujours actif", "kind": "on"}
        elif a["mode"] == "off":
            grid, rule = EMPTY_DAY, {"source": "mode", "label": "En pause", "kind": "off"}
        else:
            grid, rule = day_grid(group, d, db)
        spans, start = [], None
        for q, c in enumerate(grid + "0"):
            if c == "1" and start is None:
                start = q
            elif c != "1" and start is not None:
                spans.append(["%02d:%02d" % divmod(start * 15, 60), "%02d:%02d" % divmod(q * 15, 60) if q < QUARTERS else "24:00"])
                start = None
        days.append({"date": d.isoformat(), "source": rule.get("source"), "label": rule.get("label"),
                     "kind": rule.get("kind"), "week_id": rule.get("week_id"),
                     "active_quarters": grid.count("1"), "spans": spans,
                     "holiday": holidays(d.year).get(d)})
        d += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Groupes et canaux
# ---------------------------------------------------------------------------
def groups(db):
    """Groupes (cache court : appele a chaque message par le recepteur)."""
    now = time.monotonic()
    if _cache["groups"] is None or now - _cache["at"] > _CACHE_TTL_S:
        _cache["groups"] = list(db["detection_groups"].find())
        _cache["at"] = now
    return _cache["groups"]


def invalidate():
    _cache["groups"] = None


def channel_groups(all_groups, camera_id, channel):
    return [g for g in all_groups
            if any(str(m.get("camera_id")) == str(camera_id) and m.get("channel") == channel
                   for m in g.get("members") or [])]


def channel_armed(db, camera_id, channel, now_utc=None):
    """(active, groupes concernes). Hors de tout groupe : active."""
    gs = channel_groups(groups(db), camera_id, channel)
    if not gs:
        return True, []
    return any(group_state(g, now_utc, db)[0] for g in gs), gs
