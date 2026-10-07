"""
dispatch_auto.py - Dispatch des fiches vers les unites terrain (Field).

Deux voies coexistent :

- **Proposition automatique** : la fiche est proposee a l'unite disponible la
  plus proche (meme categorie, metier compatible). L'unite a `timeout_s`
  secondes pour accepter ; refus ou silence -> unite suivante. Apres
  `max_attempts` propositions sans suite (ou sans candidat), la fiche tombe
  dans la **file du service**, traitee par ses responsables (utilisateurs
  cockpit) depuis /dispatch-service.
- **Engagement direct** : le PC Org (ou un responsable) choisit l'unite. Un
  engagement direct pendant une proposition l'annule.

Declenchement automatique regle par categorie (`cockpit_settings`,
_id "field_dispatch") : jamais, toujours, ou selon le niveau d'urgence
(IMP=1, UR=2, UA=3, EU=4).

Etat porte par la fiche (`pcorg.dispatch`) :
    state   : proposing | queued | assigned | manual | done | closed
    round   : numero de la tournee de propositions (relance = +1)
    current : proposition en cours {device_id, device_name, proposed_at,
              expires_at, distance_m, pos_age_s} ou None
    attempts: [{round, device_id, device_name, distance_m, proposed_at,
               answer: accepted|refused|timeout, answered_at}]
    queued_at, queue_reason, assigned_at, assigned_device_id, assigned_by

Horodatages d'intervention (`pcorg.intervention`) : engaged_at, arrived_at,
done_at, outcome, report, device_name. Ecrits une seule fois par etape
(`mark_step`), ils donnent les delais engagement / arrivee / resolution.

La tablette porte sa proposition en attente (`field_devices.pending_proposal`
{fiche_id, proposed_at, expires_at}) : une unite ne recoit qu'une
proposition a la fois.

Le planificateur (`start_scheduler`, tick 3 s) traite les propositions
expirees. Comme pmv.py et scan_report.py, il suppose UN seul process
(waitress) ; les transitions restent atomiques cote Mongo (filtre sur
l'etat attendu), donc un second process ne corromprait rien, il doublerait
seulement le travail.
"""

import logging
import math
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import event_courant as EC
import pcorg_history as PH

logger = logging.getLogger(__name__)

dispatch_bp = Blueprint("dispatch_auto", __name__)

SETTINGS_ID = "field_dispatch"
URGENCY_LEVELS = [("IMP", 1), ("UR", 2), ("UA", 3), ("EU", 4)]
URGENCY_CODES = [c for c, _ in URGENCY_LEVELS]
MODES = ("never", "always", "urgency")
OUTCOMES = {
    "resolu": "Resolu",
    "partiel": "Resolu partiellement",
    "materiel": "Besoin de materiel ou de renfort",
    "impossible": "Intervention impossible",
}
REPORT_MIN_CHARS = 3   # = FINISH_REPORT_MIN de field.js ("RAS", "ok")
REPORT_MAX_CHARS = 2000

# Delai de grace apres l'echeance : une acceptation partie a la derniere
# seconde sur un reseau lent reste valable.
ANSWER_GRACE_S = 4
# Au-dela, une tablette silencieuse n'est plus proposee (telephone eteint,
# hors couverture). Un telephone verrouille ne remonte plus sa position :
# la fraicheur de position est prise en compte dans le classement.
CANDIDATE_MAX_SILENCE_S = 15 * 60
FRESH_POSITION_S = 5 * 60
TICK_S = 3
BOT_OPERATOR = "Dispatch auto"
# Fiches closes renvoyees a la File du service (section "Terminees")
CLOSED_ON_BOARD = 100

DEFAULT_CATEGORY = {
    "mode": "never",
    "levels": ["UA", "EU"],
    "timeout_s": 30,
    "max_attempts": 3,
    "self_close": False,
}
DEFAULT_OVERRIDES = {
    "PCO.Technique": {"mode": "urgency", "levels": ["UA", "EU"], "self_close": True},
    "PCO.Securite": {"mode": "always"},
    "PCO.Secours": {"mode": "never"},
}
CATEGORIES = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique", "PCO.Flux",
    "PCO.Fourriere", "PCO.Information", "PCO.MainCourante",
]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt):
    dt = _aware(dt)
    return dt.isoformat() if isinstance(dt, datetime) else None


def _norm(s):
    """Libelle compare sans accents ni casse ('Electricite' == 'Electricité')."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()


def _haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _fiche_point(fiche):
    coords = ((fiche or {}).get("gps") or {}).get("coordinates")
    if isinstance(coords, list) and len(coords) >= 2:
        try:
            return float(coords[1]), float(coords[0])
        except (TypeError, ValueError):
            return None
    return None


def _year_str(y):
    return str(y) if y is not None else ""


def _year_int(y):
    try:
        return int(y)
    except (TypeError, ValueError):
        return y


def _entry(text, operator=BOT_OPERATOR, origin="cockpit", ts=None):
    return PH.make_entry(operator, text, origin=origin, ts=ts, system=True)


def _field():
    import field
    return field


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _clean_category_cfg(raw, base):
    out = dict(base)
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("mode") in MODES:
        out["mode"] = raw["mode"]
    if isinstance(raw.get("levels"), list):
        out["levels"] = [lv for lv in URGENCY_CODES if lv in raw["levels"]]
    for key, lo, hi in (("timeout_s", 10, 600), ("max_attempts", 1, 10)):
        if key in raw:
            try:
                out[key] = max(lo, min(hi, int(raw[key])))
            except (TypeError, ValueError):
                pass
    if "self_close" in raw:
        out["self_close"] = bool(raw["self_close"])
    # Les responsables de service ne sont plus une liste d'e-mails ici : ce
    # sont les groupes coches "Responsable de service" (app._user_dispatch_categories).
    out.pop("managers", None)
    return out


def get_config(db):
    """Configuration complete, une entree par categorie, defauts appliques."""
    doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID}) or {}
    stored = doc.get("categories") or {}
    cats = {}
    for cat in CATEGORIES:
        base = dict(DEFAULT_CATEGORY)
        base.update(DEFAULT_OVERRIDES.get(cat, {}))
        cats[cat] = _clean_category_cfg(stored.get(cat), base)
    return {"categories": cats, "updated_at": _iso(doc.get("updated_at")),
            "updated_by": doc.get("updated_by")}


def save_config(db, payload, by):
    current = get_config(db)["categories"]
    incoming = (payload or {}).get("categories") or {}
    cats = {}
    for cat in CATEGORIES:
        cats[cat] = _clean_category_cfg(incoming.get(cat), current[cat])
    db["cockpit_settings"].update_one(
        {"_id": SETTINGS_ID},
        {"$set": {"categories": cats, "updated_at": _now(), "updated_by": by}},
        upsert=True,
    )
    return get_config(db)


def category_config(cfg, category):
    return (cfg.get("categories") or {}).get(category) or dict(DEFAULT_CATEGORY)


def managed_categories(payload):
    """Categories de la file du service accessibles a l'utilisateur : celles
    des groupes cockpit coches "Responsable de service" (config > groupes),
    toutes pour un admin. En echec de resolution : aucune (on ferme)."""
    try:
        from app import _user_dispatch_categories
        return [c for c in _user_dispatch_categories(payload) if c in CATEGORIES]
    except Exception as e:
        logger.warning("dispatch : categories du responsable indisponibles (%s)", e)
        return []


def wants_auto(cfg, fiche):
    """La fiche doit-elle etre proposee automatiquement a sa creation ?"""
    if not fiche or fiche.get("status_code") == 10:
        return False
    if ((fiche.get("content_category") or {}).get("patrouille") or "").strip():
        return False
    ccfg = category_config(cfg, fiche.get("category"))
    mode = ccfg.get("mode")
    if mode == "always":
        return True
    if mode == "urgency":
        return fiche.get("niveau_urgence") in (ccfg.get("levels") or [])
    return False


# ---------------------------------------------------------------------------
# Candidats
# ---------------------------------------------------------------------------

def _device_metiers_ok(device, metier):
    metiers = device.get("metiers") or []
    if not metier or not metiers:
        return True
    target = _norm(metier)
    return any(_norm(m) == target for m in metiers)


def _proposal_pending(device, now):
    pp = device.get("pending_proposal") or {}
    exp = _aware(pp.get("expires_at"))
    return bool(pp.get("fiche_id")) and isinstance(exp, datetime) and exp + timedelta(seconds=ANSWER_GRACE_S) > now


def find_candidates(db, fiche, exclude_ids=(), now=None):
    """Unites a qui proposer la fiche, la meilleure en premier.

    Eligibles : tablette qui voit l'evenement de la fiche (field.device_pairs :
    evenements actifs + son appairage ; une tablette SAISON est candidate sur
    une epreuve active), non revoquee, meme categorie, disponible
    (statut patrouille), vue depuis moins de 15 min, sans proposition en
    cours, metier compatible (une unite sans metier declare les couvre tous).
    Classement : position fraiche (< 5 min) d'abord, puis distance, puis
    fraicheur. Sans position ou sans GPS sur la fiche : en dernier.
    """
    F = _field()
    now = now or _now()
    exclude = {str(x) for x in exclude_ids}
    category = fiche.get("category")
    metier = (fiche.get("content_category") or {}).get("sous_classification")
    point = _fiche_point(fiche)
    group_cats = F._group_categories(db)
    groups = F._beacon_groups(db)
    ev, yr = fiche.get("event"), fiche.get("year")
    query = {"revoked": {"$ne": True}}
    query.update(F.devices_seeing_pair_filter(db, ev, yr, now))
    out = []
    for d in db["field_devices"].find(query):
        if str(d["_id"]) in exclude:
            continue
        if F._device_declarant(db, d, groups):     # declarant : jamais une unite
            continue
        if F._device_category(db, d, group_cats) != category:
            continue
        if (d.get("status") or "patrouille") != "patrouille":
            continue
        last_seen = _aware(d.get("last_seen"))
        if not isinstance(last_seen, datetime) or (now - last_seen).total_seconds() > CANDIDATE_MAX_SILENCE_S:
            continue
        if _proposal_pending(d, now):
            continue
        if not _device_metiers_ok(d, metier):
            continue
        # Evenement vu par la tablette, pas reserve a une homonyme, epreuve
        # de la tablette pas terminee (balayage paresseux).
        if F._device_expired(db, d) or not F.device_matches_pair(db, d, ev, yr, now):
            continue
        pos = d.get("last_position") or {}
        pos_ts = _aware(pos.get("ts"))
        pos_age = (now - pos_ts).total_seconds() if isinstance(pos_ts, datetime) else None
        dist = None
        if point and pos.get("lat") is not None and pos.get("lng") is not None:
            try:
                dist = _haversine_m(point[0], point[1], float(pos["lat"]), float(pos["lng"]))
            except (TypeError, ValueError):
                dist = None
        fresh = pos_age is not None and pos_age <= FRESH_POSITION_S
        out.append({
            "device": d,
            "distance_m": round(dist) if dist is not None else None,
            "pos_age_s": round(pos_age) if pos_age is not None else None,
            "_key": (
                0 if dist is not None else 1,
                0 if fresh else 1,
                dist if dist is not None else 0,
                pos_age if pos_age is not None else 1e9,
            ),
        })
    out.sort(key=lambda c: c["_key"])
    return out


def _describe(c):
    parts = []
    if c.get("distance_m") is not None:
        d = c["distance_m"]
        parts.append("%d m" % d if d < 1000 else "%.1f km" % (d / 1000.0))
    if c.get("pos_age_s") is not None and c["pos_age_s"] > FRESH_POSITION_S:
        parts.append("position d'il y a %d min" % round(c["pos_age_s"] / 60))
    return (" (" + ", ".join(parts) + ")") if parts else ""


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def _ensure_indexes(db):
    try:
        db["pcorg"].create_index([("dispatch.state", 1), ("dispatch.current.expires_at", 1)],
                                 name="dispatch_state_expiry", sparse=True)
    except Exception as e:  # pragma: no cover - index deja la ou droits
        logger.debug("index dispatch : %s", e)


def start(db, fiche_id, trigger="auto", operator=None, now=None):
    """Lance une tournee de propositions. Retourne (ok, code)."""
    now = now or _now()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return False, "not_found"
    if fiche.get("status_code") == 10:
        return False, "fiche_closee"
    if ((fiche.get("content_category") or {}).get("patrouille") or "").strip():
        return False, "deja_engagee"
    d = fiche.get("dispatch") or {}
    res = db["pcorg"].update_one(
        {"_id": fiche_id, "dispatch.state": {"$ne": "proposing"}},
        {"$set": {
            "dispatch.state": "proposing",
            "dispatch.round": int(d.get("round") or 0) + 1,
            "dispatch.current": None,
            "dispatch.started_at": now,
            "dispatch.trigger": trigger,
            "dispatch.queued_at": None,
            "dispatch.queue_reason": None,
        }},
    )
    if not res.modified_count:
        return False, "deja_en_cours"
    urg = fiche.get("niveau_urgence")
    label = (fiche.get("category") or "").replace("PCO.", "")
    who = (" par " + operator) if operator else ""
    PH.append_entry(db["pcorg"], fiche_id, _entry(
        "Recherche automatique d'une unite %s%s%s" % (label, (" (urgence %s)" % urg) if urg else "", who),
        ts=now))
    propose_next(db, fiche_id, now=now)
    return True, "ok"


def maybe_auto_start(db, fiche_id, trigger="creation"):
    """Appele apres creation / changement d'urgence : lance la proposition
    automatique si la configuration de la categorie le demande. Ne leve jamais."""
    try:
        fiche = db["pcorg"].find_one({"_id": fiche_id})
        if not fiche or (fiche.get("dispatch") or {}).get("state") in ("proposing", "queued", "assigned"):
            return False
        if wants_auto(get_config(db), fiche):
            return start(db, fiche_id, trigger=trigger)[0]
    except Exception as e:
        logger.warning("dispatch auto %s : %s", fiche_id, e)
    return False


def _release_device(db, device_id, fiche_id):
    try:
        from bson import ObjectId
        oid = ObjectId(str(device_id))
    except Exception:
        return
    db["field_devices"].update_one(
        {"_id": oid, "pending_proposal.fiche_id": fiche_id},
        {"$set": {"pending_proposal": None}},
    )


def _queue(db, fiche, reason, now):
    fid = fiche["_id"]
    res = db["pcorg"].update_one(
        {"_id": fid, "dispatch.state": "proposing", "dispatch.current": None},
        {"$set": {"dispatch.state": "queued", "dispatch.queued_at": now,
                  "dispatch.queue_reason": reason}},
    )
    if res.modified_count:
        label = (fiche.get("category") or "").replace("PCO.", "")
        PH.append_entry(db["pcorg"], fid, _entry(
            "Mise en file du service %s : %s" % (label, reason), ts=now), inc_bounce=True)
    return "queued"


def propose_next(db, fiche_id, now=None):
    """Propose la fiche a la meilleure unite suivante, ou la met en file."""
    now = now or _now()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return "not_found"
    d = fiche.get("dispatch") or {}
    if d.get("state") != "proposing" or d.get("current"):
        return "noop"
    if fiche.get("status_code") == 10:
        db["pcorg"].update_one({"_id": fiche_id, "dispatch.state": "proposing"},
                               {"$set": {"dispatch.state": "closed"}})
        return "closed"
    if ((fiche.get("content_category") or {}).get("patrouille") or "").strip():
        db["pcorg"].update_one({"_id": fiche_id, "dispatch.state": "proposing"},
                               {"$set": {"dispatch.state": "manual"}})
        return "manual"

    ccfg = category_config(get_config(db), fiche.get("category"))
    rnd = d.get("round") or 1
    tried = [a for a in (d.get("attempts") or []) if a.get("round") == rnd]
    if len(tried) >= ccfg["max_attempts"]:
        return _queue(db, fiche, "%d proposition(s) sans suite" % len(tried), now)

    candidates = find_candidates(db, fiche, exclude_ids=[a.get("device_id") for a in tried], now=now)
    timeout = ccfg["timeout_s"]
    expires = now + timedelta(seconds=timeout)
    chosen = None
    for c in candidates:
        dev = c["device"]
        # Reservation atomique de l'unite : une seule proposition a la fois.
        r = db["field_devices"].update_one(
            {"_id": dev["_id"], "status": {"$in": ["patrouille", None]},
             "$or": [{"pending_proposal": None},
                     {"pending_proposal.expires_at": {"$lt": now - timedelta(seconds=ANSWER_GRACE_S)}}]},
            {"$set": {"pending_proposal": {"fiche_id": fiche_id, "proposed_at": now, "expires_at": expires}}},
        )
        if r.modified_count:
            chosen = c
            break
    if not chosen:
        reason = "aucune unite disponible" if not tried else \
            "%d proposition(s) sans suite, plus d'unite disponible" % len(tried)
        return _queue(db, fiche, reason, now)

    dev = chosen["device"]
    current = {
        "device_id": str(dev["_id"]),
        "device_name": dev.get("name") or "?",
        "proposed_at": now,
        "expires_at": expires,
        "distance_m": chosen["distance_m"],
        "pos_age_s": chosen["pos_age_s"],
    }
    r = db["pcorg"].update_one(
        {"_id": fiche_id, "dispatch.state": "proposing", "dispatch.current": None},
        {"$set": {"dispatch.current": current}},
    )
    if not r.modified_count:
        _release_device(db, dev["_id"], fiche_id)
        return "race"
    PH.append_entry(db["pcorg"], fiche_id, _entry(
        "Proposee a %s%s, reponse attendue sous %d s" % (current["device_name"], _describe(chosen), timeout),
        ts=now), inc_bounce=True)
    try:
        label = (fiche.get("category") or "").replace("PCO.", "")
        urg = fiche.get("niveau_urgence")
        _field().send_push_to_device(
            db, dev["_id"],
            title="Intervention proposee : " + label + ((" " + urg) if urg else ""),
            body=((fiche.get("text") or "Nouvelle intervention")[:100] + _describe(chosen)),
            url="/field",
            tag="proposal-" + str(fiche_id),
        )
    except Exception:
        pass
    return "proposed"


def _close_attempt(db, fiche_id, device_id, answer, now):
    """Clot la proposition en cours pour `device_id`. Retourne la proposition
    close, ou None si elle n'etait plus en cours (deja tranchee)."""
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"dispatch": 1})
    cur = ((fiche or {}).get("dispatch") or {}).get("current") or {}
    if cur.get("device_id") != str(device_id):
        return None
    attempt = dict(cur, round=(fiche.get("dispatch") or {}).get("round") or 1,
                   answer=answer, answered_at=now)
    r = db["pcorg"].update_one(
        {"_id": fiche_id, "dispatch.state": "proposing", "dispatch.current.device_id": str(device_id)},
        {"$set": {"dispatch.current": None}, "$push": {"dispatch.attempts": attempt}},
    )
    if not r.modified_count:
        return None
    _release_device(db, device_id, fiche_id)
    return attempt


def refuse(db, fiche_id, device, now=None, answer="refused"):
    now = now or _now()
    attempt = _close_attempt(db, fiche_id, device["_id"], answer, now)
    if not attempt:
        return False
    text = ("Refusee par %s" if answer == "refused" else "Sans reponse de %s") % attempt.get("device_name")
    PH.append_entry(db["pcorg"], fiche_id, _entry(text, ts=now), inc_bounce=True)
    propose_next(db, fiche_id, now=now)
    return True


def accept(db, fiche_id, device, now=None):
    """L'unite accepte : elle est engagee sur la fiche. Retourne (ok, code)."""
    now = now or _now()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return False, "not_found"
    d = fiche.get("dispatch") or {}
    cur = d.get("current") or {}
    if d.get("state") != "proposing" or cur.get("device_id") != str(device["_id"]):
        return False, "proposition_expiree"
    exp = _aware(cur.get("expires_at"))
    if isinstance(exp, datetime) and now > exp + timedelta(seconds=ANSWER_GRACE_S):
        return False, "proposition_expiree"
    fresh = db["field_devices"].find_one({"_id": device["_id"]}) or device
    if (fresh.get("status") or "patrouille") != "patrouille":
        return False, "unite_occupee"

    name = fresh.get("name") or "?"
    n = PH.append_entry(
        db["pcorg"], fiche_id,
        PH.make_entry("field:" + name, "Statut: Engagement confirme\nProposition acceptee" + _describe(cur),
                      origin="field", ts=now),
        set_fields={
            "content_category.patrouille": name,
            "dispatch.state": "assigned",
            "dispatch.current": None,
            "dispatch.assigned_at": now,
            "dispatch.assigned_device_id": str(fresh["_id"]),
            "dispatch.assigned_by": "proposition",
            "intervention.engaged_at": now,
            "intervention.device_name": name,
        },
        owned={"content_category.patrouille"}, inc_bounce=True,
        extra_filter={"dispatch.state": "proposing",
                      "dispatch.current.device_id": str(fresh["_id"]),
                      "status_code": {"$ne": 10}},
    )
    if not n:
        return False, "proposition_expiree"
    db["pcorg"].update_one({"_id": fiche_id}, {"$push": {"dispatch.attempts": dict(
        cur, round=d.get("round") or 1, answer="accepted", answered_at=now)}})
    db["field_devices"].update_one(
        {"_id": fresh["_id"]},
        {"$set": {"status": "intervention", "status_since": now,
                  "active_fiche_id": fiche_id, "pending_proposal": None},
         "$push": {"status_history": {"status": "intervention", "ts": now,
                                      "trigger": "proposal_accept", "fiche_id": fiche_id}}},
    )
    return True, "ok"


def on_manual_assign(db, fiche_id, by=None, now=None):
    """Engagement direct (PC Org, responsable) : annule une proposition en
    cours et marque la fiche comme engagee a la main."""
    now = now or _now()
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"dispatch": 1})
    d = (fiche or {}).get("dispatch") or {}
    if d.get("state") not in ("proposing", "queued"):
        return
    cur = d.get("current") or {}
    r = db["pcorg"].update_one(
        {"_id": fiche_id, "dispatch.state": d.get("state")},
        {"$set": {"dispatch.state": "manual", "dispatch.current": None,
                  "dispatch.assigned_at": now, "dispatch.assigned_by": by or "direct"}},
    )
    if r.modified_count and cur.get("device_id"):
        _release_device(db, cur["device_id"], fiche_id)


def on_close(db, fiche_id):
    """Fiche close : plus rien a proposer."""
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"dispatch": 1})
    d = (fiche or {}).get("dispatch") or {}
    if d.get("state") in ("proposing", "queued"):
        cur = d.get("current") or {}
        db["pcorg"].update_one({"_id": fiche_id, "dispatch.state": d.get("state")},
                               {"$set": {"dispatch.state": "closed", "dispatch.current": None}})
        if cur.get("device_id"):
            _release_device(db, cur["device_id"], fiche_id)


def mark_step(db, fiche_id, step, now=None, device_name=None):
    """Horodate une etape d'intervention (engaged_at, arrived_at...) une seule fois."""
    if not fiche_id:
        return
    now = now or _now()
    upd = {"intervention." + step: now}
    if device_name:
        upd["intervention.device_name"] = device_name
    try:
        db["pcorg"].update_one({"_id": fiche_id, "intervention." + step: {"$exists": False}}, {"$set": upd})
    except Exception:
        pass


def manual_assign(db, fiche_id, device, by_name, now=None):
    """Engagement choisi par un responsable depuis la file du service.

    Comme l'engagement PC Org : l'unite recoit la fiche et confirme elle-meme
    ("Prendre en charge"). Si elle est deja occupee, la fiche s'ajoute a ses
    missions sans ecraser sa fiche active.
    """
    now = now or _now()
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return False, "not_found"
    if fiche.get("status_code") == 10:
        return False, "fiche_closee"
    F = _field()
    if device.get("revoked") or not F.device_matches_pair(db, device, fiche.get("event"), fiche.get("year")):
        return False, "unite_invalide"
    if F._device_declarant(db, device):
        return False, "unite_invalide"
    if F._device_category(db, device) != fiche.get("category"):
        return False, "categorie_differente"
    name = device.get("name") or "?"
    cur = ((fiche.get("dispatch") or {}).get("current") or {})
    previous = ((fiche.get("content_category") or {}).get("patrouille") or "").strip()
    n = PH.append_entry(
        db["pcorg"], fiche_id,
        _entry("Engagee sur %s par %s (file du service)" % (name, by_name), operator=by_name, ts=now),
        set_fields={
            "content_category.patrouille": name,
            "dispatch.state": "assigned",
            "dispatch.current": None,
            "dispatch.assigned_at": now,
            "dispatch.assigned_device_id": str(device["_id"]),
            "dispatch.assigned_by": by_name,
        },
        owned={"content_category.patrouille"}, inc_bounce=True,
        extra_filter={"status_code": {"$ne": 10}},
    )
    if not n:
        return False, "fiche_closee"
    if cur.get("device_id") and cur.get("device_id") != str(device["_id"]):
        _release_device(db, cur["device_id"], fiche_id)
    if previous and previous != name:
        # Changement d'unite : l'ancienne, si c'etait sa fiche active,
        # redevient disponible (meme regle que la modification PC Org).
        # active_fiche_id suffit a la designer, quel que soit son evenement
        # (tablette SAISON engagee sur une epreuve).
        db["field_devices"].update_one(
            {"name": previous, "revoked": {"$ne": True}, "active_fiche_id": fiche_id},
            {"$set": {"status": "patrouille", "status_since": now, "active_fiche_id": None},
             "$push": {"status_history": {"status": "patrouille", "ts": now,
                                          "trigger": "service_reassign", "fiche_id": fiche_id}}},
        )
    busy =(device.get("status") or "patrouille") != "patrouille" or device.get("active_fiche_id")
    upd = {"$push": {"status_history": {"status": "dispatch", "ts": now,
                                        "trigger": "service_dispatch", "fiche_id": fiche_id}}}
    if not busy:
        upd["$set"] = {"active_fiche_id": fiche_id}
    db["field_devices"].update_one({"_id": device["_id"]}, upd)
    try:
        F.send_push_to_device(db, device["_id"],
                              title="Dispatch : " + (fiche.get("category") or "").replace("PCO.", ""),
                              body=(fiche.get("text") or "Nouvelle intervention")[:120],
                              url="/field", tag="dispatch-" + str(fiche_id))
    except Exception:
        pass
    return True, ("ajoutee_aux_missions" if busy else "ok")


AVAILABLE_MAX_AGE_DAYS = 30


def available_for_device(db, device, limit=100, now=None):
    """Missions que l'unite peut prendre elle-meme (onglet "Disponibles" de
    la tablette) : fiches ouvertes des evenements qu'elle voit
    (field.device_pairs) et de sa categorie, sans unite engagee, de son metier
    (unite sans metier : tous), creees depuis moins de 30 jours (SAISON
    accumule toute l'annee). Les plus urgentes puis les plus recentes d'abord.

    Avant : tri croissant + limit(300) -> on gardait les 300 PLUS ANCIENNES
    fiches ouvertes, les nouvelles n'apparaissaient plus."""
    F = _field()
    now = now or _now()
    cat = F._device_category(db, device)
    if not cat or F._device_declarant(db, device):
        return []
    out = []
    for f in db["pcorg"].find({
        "$and": [
            EC.pairs_filter(F.device_fiche_pairs(db, device, now)),
            {"$or": [{"content_category.patrouille": {"$in": ["", None]}},
                     {"content_category.patrouille": {"$exists": False}}]},
        ],
        "category": cat, "status_code": {"$ne": 10},
        "ts": {"$gte": now - timedelta(days=AVAILABLE_MAX_AGE_DAYS)},
    }).sort("ts", -1).limit(300):
        cc = f.get("content_category") or {}
        if not _device_metiers_ok(device, cc.get("sous_classification")):
            continue
        point = _fiche_point(f)
        d = f.get("dispatch") or {}
        cur = d.get("current") or {}
        out.append({
            "id": f["_id"],
            "category": f.get("category"),
            "text": f.get("text") or f.get("text_full") or "",
            "niveau_urgence": f.get("niveau_urgence"),
            "metier": cc.get("sous_classification"),
            "area": (f.get("area") or {}).get("desc") if isinstance(f.get("area"), dict) else None,
            "carroye": cc.get("carroye"),
            "ts": _iso(f.get("ts")),
            "lat": point[0] if point else None,
            "lng": point[1] if point else None,
            "dispatch_state": d.get("state"),
            "proposed_to_me": cur.get("device_id") == str(device["_id"]),
        })
    rank = {"EU": 0, "UA": 1, "UR": 2, "IMP": 3}
    out.sort(key=lambda x: rank.get(x["niveau_urgence"], 4))
    return out[:limit]


def self_assign(db, fiche_id, device, now=None):
    """L'unite prend elle-meme une mission disponible (engagement immediat).
    Retourne (ok, code). Une proposition automatique en cours vers une autre
    unite est annulee ; si elle lui etait adressee, c'est une acceptation."""
    now = now or _now()
    fresh = db["field_devices"].find_one({"_id": device["_id"]}) or device
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return False, "not_found"
    if fiche.get("status_code") == 10:
        return False, "fiche_closee"
    cur = ((fiche.get("dispatch") or {}).get("current") or {})
    if cur.get("device_id") == str(fresh["_id"]):
        return accept(db, fiche_id, fresh, now=now)
    if (fresh.get("status") or "patrouille") != "patrouille":
        return False, "unite_occupee"
    F = _field()
    if F._device_declarant(db, fresh):
        return False, "unite_invalide"
    if not F.device_matches_pair(db, fresh, fiche.get("event"), fiche.get("year"), now):
        return False, "evenement_different"
    if F._device_category(db, fresh) != fiche.get("category"):
        return False, "categorie_differente"
    if not _device_metiers_ok(fresh, (fiche.get("content_category") or {}).get("sous_classification")):
        return False, "metier_different"
    name = fresh.get("name") or "?"
    n = PH.append_entry(
        db["pcorg"], fiche_id,
        PH.make_entry("field:" + name, "Statut: Engagement confirme\nMission prise par l'unite (libre-service)",
                      origin="field", ts=now),
        set_fields={
            "content_category.patrouille": name,
            "dispatch.state": "assigned",
            "dispatch.current": None,
            "dispatch.assigned_at": now,
            "dispatch.assigned_device_id": str(fresh["_id"]),
            "dispatch.assigned_by": "libre-service",
            "intervention.engaged_at": now,
            "intervention.device_name": name,
        },
        owned={"content_category.patrouille"}, inc_bounce=True,
        extra_filter={"status_code": {"$ne": 10},
                      "$or": [{"content_category.patrouille": {"$in": ["", None]}},
                              {"content_category.patrouille": {"$exists": False}}]},
    )
    if not n:
        return False, "deja_prise"
    if cur.get("device_id"):
        _release_device(db, cur["device_id"], fiche_id)
    db["field_devices"].update_one(
        {"_id": fresh["_id"]},
        {"$set": {"status": "intervention", "status_since": now,
                  "active_fiche_id": fiche_id, "pending_proposal": None},
         "$push": {"status_history": {"status": "intervention", "ts": now,
                                      "trigger": "self_assign", "fiche_id": fiche_id}}},
    )
    return True, "ok"


def finish(db, fiche_id, device, outcome, report, now=None):
    """Fin d'intervention declaree par le technicien (cloture autorisee).

    - resolu : la fiche est close, l'unite redevient disponible.
    - autre issue : la fiche reste ouverte, retourne dans la file du service
      avec le compte-rendu ; l'unite redevient disponible.
    """
    now = now or _now()
    if outcome not in OUTCOMES:
        return False, "issue_invalide"
    report = (report or "").strip()[:REPORT_MAX_CHARS]
    if len(report) < REPORT_MIN_CHARS:
        return False, "compte_rendu_obligatoire"
    fiche = db["pcorg"].find_one({"_id": fiche_id})
    if not fiche:
        return False, "not_found"
    name = device.get("name") or "?"
    if ((fiche.get("content_category") or {}).get("patrouille") or "") != name:
        # Renvoi (file hors ligne) d'une fin deja enregistree
        if (fiche.get("intervention") or {}).get("finished_by") == str(device["_id"]):
            return True, "deja_termine"
        return False, "not_assigned"
    if not category_config(get_config(db), fiche.get("category")).get("self_close"):
        return False, "cloture_terrain_desactivee"

    operator = "field:" + name
    label = OUTCOMES[outcome]
    common = {
        "intervention.done_at": now,
        "intervention.outcome": outcome,
        "intervention.report": report,
        "intervention.device_name": name,
        "intervention.finished_by": str(device["_id"]),
    }
    if outcome == "resolu":
        if fiche.get("status_code") == 10:
            return True, "deja_termine"
        from zoneinfo import ZoneInfo
        n = PH.append_entry(
            db["pcorg"], fiche_id,
            PH.make_entry(operator, "Statut: En cours -> Terminé\nIntervention terminee (%s) : %s" % (label, report),
                          origin="field", ts=now),
            set_fields=dict(common, **{
                "status_code": 10,
                "close_ts": now,
                "close_iso": now.astimezone(ZoneInfo("Europe/Paris")).isoformat(),
                "operator_close": operator,
                "operator_id_close": "field:" + str(device["_id"]),
                "cockpit_status_at": now,
                "dispatch.state": "done",
            }),
            owned={"status"}, inc_bounce=True,
            extra_filter={"status_code": {"$ne": 10}},
        )
        if not n:
            return True, "deja_termine"
    else:
        cat_label = (fiche.get("category") or "").replace("PCO.", "")
        n = PH.append_entry(
            db["pcorg"], fiche_id,
            PH.make_entry(operator, "Statut: Fin d'intervention\n%s : %s\nRetour en file du service %s"
                          % (label, report, cat_label), origin="field", ts=now),
            set_fields=dict(common, **{
                "content_category.patrouille": "",
                "dispatch.state": "queued",
                "dispatch.current": None,
                "dispatch.queued_at": now,
                "dispatch.queue_reason": "Retour terrain : " + label,
            }),
            owned={"content_category.patrouille"}, inc_bounce=True,
            extra_filter={"status_code": {"$ne": 10}},
        )
        if not n:
            return False, "fiche_closee"

    db["field_devices"].update_one(
        {"_id": device["_id"]},
        {"$set": {"status": "patrouille", "status_since": now, "active_fiche_id": None,
                  "fin_comment": None},
         "$push": {"status_history": {"status": "patrouille", "ts": now,
                                      "trigger": "finish_" + outcome, "fiche_id": fiche_id}}},
    )
    return True, "ok"


def tick(db, now=None):
    """Traite les propositions expirees et les tournees bloquees."""
    now = now or _now()
    handled = 0
    limit = now - timedelta(seconds=ANSWER_GRACE_S)
    for f in list(db["pcorg"].find(
            {"dispatch.state": "proposing", "dispatch.current.expires_at": {"$lt": limit}},
            {"dispatch.current": 1}).limit(50)):
        cur = (f.get("dispatch") or {}).get("current") or {}
        try:
            from bson import ObjectId
            dev = {"_id": ObjectId(cur.get("device_id"))}
        except Exception:
            continue
        if refuse(db, f["_id"], dev, now=now, answer="timeout"):
            handled += 1
    # Tournee sans proposition en cours (arret entre deux etapes) : on relance.
    for f in list(db["pcorg"].find(
            {"dispatch.state": "proposing", "dispatch.current": None,
             "dispatch.started_at": {"$lt": now - timedelta(seconds=10)}},
            {"_id": 1}).limit(50)):
        if propose_next(db, f["_id"], now=now) in ("proposed", "queued"):
            handled += 1
    return handled


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler(db_getter=None):
    """Thread de fond : tick toutes les TICK_S secondes. Idempotent."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        db = (db_getter or _app_db)()
        _ensure_indexes(db)
        while True:
            try:
                tick(db)
            except Exception as e:
                logger.warning("dispatch tick : %s", e)
            time.sleep(TICK_S)

    threading.Thread(target=_loop, name="dispatch-auto", daemon=True).start()
    logger.info("Planificateur dispatch auto demarre (tick %d s)", TICK_S)


# ---------------------------------------------------------------------------
# Vue tablette
# ---------------------------------------------------------------------------

def proposal_for_device(db, device, now=None):
    """Proposition en attente pour une tablette (payload de /field/my-fiches)."""
    now = now or _now()
    pp = device.get("pending_proposal") or {}
    fid = pp.get("fiche_id")
    if not fid:
        return None
    exp = _aware(pp.get("expires_at"))
    if not isinstance(exp, datetime) or exp + timedelta(seconds=ANSWER_GRACE_S) < now:
        return None
    fiche = db["pcorg"].find_one({"_id": fid})
    cur = ((fiche or {}).get("dispatch") or {}).get("current") or {}
    if not fiche or cur.get("device_id") != str(device["_id"]):
        return None
    point = _fiche_point(fiche)
    cc = fiche.get("content_category") or {}
    return {
        "fiche_id": fid,
        "category": fiche.get("category"),
        "text": fiche.get("text") or fiche.get("text_full") or "",
        "niveau_urgence": fiche.get("niveau_urgence"),
        "metier": cc.get("sous_classification"),
        "area": (fiche.get("area") or {}).get("desc") if isinstance(fiche.get("area"), dict) else None,
        "carroye": cc.get("carroye"),
        "lat": point[0] if point else None,
        "lng": point[1] if point else None,
        "distance_m": cur.get("distance_m"),
        "expires_at": _iso(exp),
        "remaining_s": max(0, round((exp - now).total_seconds())),
    }


# ---------------------------------------------------------------------------
# Routes cockpit
# ---------------------------------------------------------------------------

def _app_db():
    from app import db
    return db


def _role_required(role):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _err(code, status=400, **extra):
    body = {"ok": False, "error": code}
    body.update(extra)
    return jsonify(body), status


def _user():
    return getattr(request, "user_payload", None) or {}


def _is_admin(u):
    return bool(u.get("is_super_admin") or u.get("app_role") == "admin")


def _user_name(u):
    name = ("%s %s" % (u.get("firstname") or "", u.get("lastname") or "")).strip()
    return name or u.get("email") or "?"


def _can_manage(db, category):
    return category in managed_categories(_user())


SOURCE_LABELS = {
    "initiative": "Initiative PC Org",
    "externe": "Appel externe",
    "operateur": "Operateur interne",
    "hierarchie": "Hierarchie",
}
CANAL_LABELS = {"telephone": "telephone", "radio": "radio", "presentiel": "presentiel", "mail": "mail",
                "application": "application"}


def _fiche_source(cc):
    """Source de la fiche en clair (meme modele que l'assistant de creation)."""
    st = cc.get("source_type") or ""
    who = (cc.get("appelant") or cc.get("emetteur_interne") or cc.get("donneur_ordre")
           or cc.get("source_origine") or "")
    canal = cc.get("canal") or ""
    if not canal:
        if cc.get("telephone"):
            canal = "telephone"
        elif cc.get("radio"):
            canal = "radio"
    return {
        "type": st,
        "label": SOURCE_LABELS.get(st, ""),
        "who": str(who) if who and who is not True else "",
        "canal": CANAL_LABELS.get(canal, canal if isinstance(canal, str) else ""),
        "radio_canal": cc.get("radio_canal") or "",
    }


def _last_action(history):
    """Derniere action humaine de la chronologie (a defaut, la derniere entree)."""
    hist = [e for e in (history or []) if isinstance(e, dict) and str(e.get("text") or "").strip()]
    if not hist:
        return None
    human = [e for e in hist if not e.get("system")]
    e = (human or hist)[-1]
    text = str(e.get("text") or "").strip()
    return {
        "text": text[:400] + ("..." if len(text) > 400 else ""),
        "operator": str(e.get("operator") or ""),
        "ts": _iso(e.get("ts")) if isinstance(e.get("ts"), datetime) else (e.get("ts") or None),
        "count": len(hist),
    }


# Projection de la file du service : pas de documents complets (la
# chronologie d'une fiche SAISON peut etre longue) ; seules les dernieres
# entrees servent a "Derniere action", le total vient de $size.
BOARD_FICHE_PROJECTION = {
    "event": 1, "year": 1, "operator": 1, "content_category": 1, "sql_id": 1,
    "category": 1, "text": 1, "text_full": 1, "niveau_urgence": 1, "area": 1,
    "ts": 1, "gps": 1, "status_code": 1, "close_ts": 1, "operator_close": 1,
    "dispatch": 1, "intervention": 1,
    "comment_history": {"$slice": -30},
    "_hist_n": {"$size": {"$ifNull": ["$comment_history", []]}},
}
BOARD_DEVICE_PROJECTION = {
    "name": 1, "event": 1, "year": 1, "category": 1, "beacon_group_id": 1,
    "metiers": 1, "status": 1, "status_since": 1, "active_fiche_id": 1,
    "last_seen": 1, "last_position": 1, "pending_proposal": 1, "restoredAt": 1,
}


def _pub_fiche(fiche, devices_by_name):
    d = fiche.get("dispatch") or {}
    cur = d.get("current") or {}
    cc = fiche.get("content_category") or {}
    point = _fiche_point(fiche)
    patr = cc.get("patrouille") or ""
    dev = devices_by_name.get(patr)
    last_action = _last_action(fiche.get("comment_history"))
    if last_action and isinstance(fiche.get("_hist_n"), int):
        last_action["count"] = fiche["_hist_n"]
    return {
        "event": fiche.get("event"),
        "year": fiche.get("year"),
        "operator": fiche.get("operator") or "",
        "source": _fiche_source(cc),
        "last_action": last_action,
        "sql_id": fiche.get("sql_id"),
        "id": fiche["_id"],
        "category": fiche.get("category"),
        "text": fiche.get("text") or fiche.get("text_full") or "",
        "niveau_urgence": fiche.get("niveau_urgence"),
        "metier": cc.get("sous_classification"),
        "area": (fiche.get("area") or {}).get("desc") if isinstance(fiche.get("area"), dict) else None,
        "carroye": cc.get("carroye"),
        "ts": _iso(fiche.get("ts")),
        "lat": point[0] if point else None,
        "lng": point[1] if point else None,
        "patrouille": patr,
        "unit_status": (dev or {}).get("status"),
        "status_code": fiche.get("status_code"),
        "close_ts": _iso(fiche.get("close_ts")) if fiche.get("status_code") == 10 else None,
        "operator_close": fiche.get("operator_close"),
        "dispatch": {
            "state": d.get("state"),
            "round": d.get("round"),
            "queued_at": _iso(d.get("queued_at")),
            "queue_reason": d.get("queue_reason"),
            "current": {
                "device_name": cur.get("device_name"),
                "expires_at": _iso(cur.get("expires_at")),
                "distance_m": cur.get("distance_m"),
            } if cur else None,
            "attempts": [
                {"device_name": a.get("device_name"), "answer": a.get("answer"),
                 "answered_at": _iso(a.get("answered_at")), "round": a.get("round")}
                for a in (d.get("attempts") or [])
            ],
            "assigned_by": d.get("assigned_by"),
        },
        "intervention": {k: (_iso(v) if isinstance(v, datetime) else v)
                         for k, v in (fiche.get("intervention") or {}).items()},
    }


def _pub_unit(db, d, now, group_cats):
    F = _field()
    pos = d.get("last_position") or {}
    pos_ts = _aware(pos.get("ts"))
    last_seen = _aware(d.get("last_seen"))
    pp = d.get("pending_proposal") or {}
    return {
        "id": str(d["_id"]),
        "name": d.get("name"),
        "category": F._device_category(db, d, group_cats),
        "metiers": d.get("metiers") or [],
        "status": d.get("status") or "patrouille",
        "status_since": _iso(d.get("status_since")),
        "active_fiche_id": d.get("active_fiche_id"),
        "online": isinstance(last_seen, datetime) and (now - last_seen).total_seconds() < 120,
        "last_seen": _iso(last_seen),
        "lat": pos.get("lat"),
        "lng": pos.get("lng"),
        "pos_age_s": round((now - pos_ts).total_seconds()) if isinstance(pos_ts, datetime) else None,
        "battery": pos.get("battery"),
        "pending_fiche_id": pp.get("fiche_id") if _proposal_pending(d, now) else None,
    }


@dispatch_bp.route("/dispatch-service")
@_role_required("user")
def dispatch_service_page():
    u = _user()
    cats = managed_categories(u)
    return render_template("dispatch_service.html", managed_categories=cats,
                           user_is_admin=_is_admin(u), user=u)


@dispatch_bp.route("/api/dispatch/board")
@_role_required("user")
def dispatch_board():
    """File du service : fiches ouvertes et unites des categories gerees."""
    db = _app_db()
    u = _user()
    cfg = get_config(db)
    cats = managed_categories(u)
    if not cats:
        return _err("not_manager", 403)
    only = request.args.get("category")
    if only:
        if only not in cats:
            return _err("not_manager", 403)
        cats = [only]
    # Par defaut : tous les evenements actifs (epreuves en cours + SAISON, +
    # SAISON N-1). event/year deviennent un filtre optionnel.
    event = request.args.get("event") or ""
    year = request.args.get("year") or ""
    now = _now()
    F = _field()
    active = EC.active_events(db, now)
    if event and year:
        pairs = [(event, _year_int(year))]
    else:
        pairs = EC.active_pairs(db, now, include_previous_saison=True)
    pair_keys = {F._pair_key(e, y) for e, y in pairs}
    group_cats = F._group_categories(db)
    groups = F._beacon_groups(db)
    devices = []
    for d in db["field_devices"].find({"revoked": {"$ne": True}}, BOARD_DEVICE_PROJECTION):
        if F._device_expired(db, d):
            continue
        if F._device_declarant(db, d, groups):     # pas une unite engageable
            continue
        dp = F.device_pairs(db, d, now)
        if pair_keys & {F._pair_key(e, y) for e, y in dp}:
            devices.append(d)
    units = []
    for d in devices:
        x = _pub_unit(db, d, now, group_cats)
        if x["category"] in cats:
            x["event"] = d.get("event")
            x["year"] = d.get("year")
            units.append(x)
    by_name = {}
    for d in devices:
        # Homonymes (noms uniques par appairage seulement) : on garde la plus recente.
        prev = by_name.get(d.get("name"))
        if prev is None or (_aware(d.get("last_seen")) or datetime.min.replace(tzinfo=timezone.utc)) \
                > (_aware(prev.get("last_seen")) or datetime.min.replace(tzinfo=timezone.utc)):
            by_name[d.get("name")] = d
    # SAISON accumule toute l'annee : fiches ouvertes bornees a 30 jours,
    # les plus anciennes sont seulement comptees (older_open).
    cutoff = now - timedelta(days=AVAILABLE_MAX_AGE_DAYS)
    saison_pairs = [p for p in pairs if EC.is_saison(p[0])]
    not_old_saison = {"$or": [{"event": {"$ne": EC.SAISON}}, {"ts": {"$gte": cutoff}}]}
    open_q = {"$and": [EC.pairs_filter(pairs), not_old_saison],
              "category": {"$in": cats}, "status_code": {"$ne": 10}}
    fiches = [_pub_fiche(f, by_name) for f in db["pcorg"].find(open_q, BOARD_FICHE_PROJECTION)
              .sort("ts", -1).limit(300)]
    older_open = 0
    if saison_pairs:
        older_open = db["pcorg"].count_documents({
            "$and": [EC.pairs_filter(saison_pairs), {"ts": {"$lt": cutoff}}],
            "category": {"$in": cats}, "status_code": {"$ne": 10}})
    # Section "Terminees" : les dernieres fiches closes des evenements affiches
    closed = [_pub_fiche(f, by_name) for f in db["pcorg"].find({
        "$and": [EC.pairs_filter(pairs)], "category": {"$in": cats},
        "status_code": 10,
    }, BOARD_FICHE_PROJECTION).sort("close_ts", -1).limit(CLOSED_ON_BOARD)]
    shown = []
    for e, y in pairs:
        if any(F._pair_key(e, y) == F._pair_key(s["event"], s["year"]) for s in shown):
            continue
        shown.append({"event": e, "year": _year_int(y)})
    try:
        from app import _user_can_edit_fiche, _user_can_create_fiche
        can_edit = bool(_user_can_edit_fiche(u))
        can_create = bool(_user_can_create_fiche(u))
    except Exception:
        can_edit = can_create = False
    return jsonify({
        "ok": True,
        "now": _iso(now),
        "categories": cats,
        "config": {c: cfg["categories"][c] for c in cats},
        # Droits sur les fiches depuis cette page : un responsable clot
        # toujours les fiches de ses categories (et y ajoute des actions) ;
        # la modification des elements initiaux n'y est jamais proposee.
        "rights": {"can_close": True, "can_comment": True, "can_edit": can_edit,
                   "can_create": can_create},
        "fiches": fiches,
        "closed": closed,
        "units": units,
        # Evenements affiches (filtre ou actifs), evenement courant (cible de
        # "Nouvelle fiche" sans filtre) et fiches ouvertes SAISON non listees.
        "events": shown,
        "multi_event": len({F._pair_key(f.get("event"), f.get("year"))
                            for f in fiches + closed}) > 1,
        "current": {"event": active[0]["event"], "year": active[0]["year"]},
        "active": [{"event": a["event"], "year": a["year"], "phase": a["phase"],
                    "kind": a["kind"]} for a in active],
        "filtered": bool(event and year),
        "older_open": older_open,
        "older_days": AVAILABLE_MAX_AGE_DAYS,
    })


@dispatch_bp.route("/api/dispatch/<fiche_id>/assign", methods=["POST"])
@_role_required("user")
def dispatch_assign(fiche_id):
    db = _app_db()
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"category": 1})
    if not fiche:
        return _err("not_found", 404)
    if not _can_manage(db, fiche.get("category")):
        return _err("not_manager", 403)
    try:
        from bson import ObjectId
        oid = ObjectId(str((request.get_json(silent=True) or {}).get("device_id") or ""))
    except Exception:
        return _err("invalid_device")
    device = db["field_devices"].find_one({"_id": oid})
    if not device:
        return _err("invalid_device", 404)
    ok, code = manual_assign(db, fiche_id, device, _user_name(_user()))
    if not ok:
        return _err(code, 409)
    return jsonify({"ok": True, "result": code})


@dispatch_bp.route("/api/dispatch/<fiche_id>/auto", methods=["POST"])
@_role_required("user")
def dispatch_relaunch(fiche_id):
    """Lance (ou relance) la proposition automatique. Autorise pour les
    responsables de la categorie et pour le PC Org (categorie autorisee)."""
    db = _app_db()
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"category": 1})
    if not fiche:
        return _err("not_found", 404)
    cat = fiche.get("category")
    allowed = _can_manage(db, cat)
    if not allowed:
        try:
            from app import _pcorg_cat_allowed
            allowed = _pcorg_cat_allowed(_user(), cat)
        except Exception:
            allowed = False
    if not allowed:
        return _err("forbidden", 403)
    ok, code = start(db, fiche_id, trigger="manuel", operator=_user_name(_user()))
    if not ok:
        return _err(code, 409)
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"dispatch": 1})
    return jsonify({"ok": True, "state": ((fiche or {}).get("dispatch") or {}).get("state")})


@dispatch_bp.route("/api/dispatch/config", methods=["GET"])
@_role_required("admin")
def dispatch_config_get():
    db = _app_db()
    cfg = get_config(db)
    cfg["ok"] = True
    cfg["urgency_levels"] = [{"code": c, "level": n} for c, n in URGENCY_LEVELS]
    cfg["modes"] = list(MODES)
    return jsonify(cfg)


@dispatch_bp.route("/api/dispatch/config", methods=["PUT"])
@_role_required("admin")
def dispatch_config_put():
    db = _app_db()
    cfg = save_config(db, request.get_json(silent=True) or {}, _user().get("email"))
    cfg["ok"] = True
    return jsonify(cfg)


@dispatch_bp.route("/api/dispatch/metiers")
@_role_required("user")
def dispatch_metiers():
    """Metiers d'une categorie = ses sous-classifications (pcorg_lists),
    dedoublonnes sans accents ('Electricite' et 'Electricité')."""
    db = _app_db()
    cat = request.args.get("category") or ""
    doc = db["pcorg_config"].find_one({"_id": "pcorg_lists"}, {"sous_classifications": 1}) or {}
    items = (doc.get("sous_classifications") or {}).get(cat) or []
    out, seen = [], set()
    for it in items:
        label = it.get("label") if isinstance(it, dict) else str(it)
        key = _norm(label)
        if label and key not in seen:
            seen.add(key)
            out.append(label)
    return jsonify({"ok": True, "category": cat, "metiers": sorted(out, key=_norm)})
