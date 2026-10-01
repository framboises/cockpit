"""alert_ai.py - Explication d'une alerte de la centrale par Claude.

POST /api/alerts/<alert_id>/explain (role user, CSRF actif)

Pour une alerte active (cockpit_active_alerts), archivee
(cockpit_active_alerts_archive) ou historisee (cockpit_alert_history, par
alert_id ou par _id), rassemble le contexte PROPRE AU TYPE de detection de la
definition, puis fait UN appel Claude court (JSON strict a 5 cles) :
contexte, cause_probable, evolution, action_suggeree, confiance.

Cache par alerte dans `alert_explanations` (TTL 7 jours) : plusieurs
operateurs qui cliquent la meme alerte ne paient qu'un appel. ?refresh=1
regenere, mais seulement si l'explication a plus de 2 minutes.

Les fonctions de ce module recoivent `db` en argument (testables avec un
double Mongo) ; seule la route importe `app`, tardivement.
"""

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bson.objectid import ObjectId
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

alert_ai_bp = Blueprint("alert_ai", __name__)

# Route /explain coupee par defaut (cout par clic operateur), cf. explain_route.
EXPLAIN_ENABLED = __import__("os").getenv("ALERT_AI_EXPLAIN", "0").strip().lower() in ("1", "true", "yes")

PARIS = ZoneInfo("Europe/Paris")
COL_EXPLAIN = "alert_explanations"
EXPLAIN_TTL_S = 7 * 24 * 3600
REFRESH_MIN_AGE_S = 120        # ?refresh=1 refuse avant 2 min
PENDING_STALE_S = 90           # un calcul en cours plus vieux est repris
MAX_TOKENS = 700
MAX_CONTEXT_CHARS = 12000
EXPLAIN_KEYS = ("contexte", "cause_probable", "evolution", "action_suggeree", "confiance")
FEATURE = "alert_explain"

SYSTEM_PROMPT = (
    "Tu es l'assistant du PC Organisation d'un circuit automobile (Le Mans) "
    "pendant un evenement. Un operateur te demande d'expliquer UNE alerte de "
    "la centrale d'alerte. Tu recois l'alerte et les donnees de contexte "
    "rassemblees au moment de la question.\n\n"
    "REGLES STRICTES :\n"
    "- N'utilise QUE les donnees fournies. N'invente aucun chiffre, aucun "
    "lieu, aucune cause. Si une information manque pour repondre, ecris "
    "exactement \"donnee insuffisante\" (eventuellement suivi de ce qui "
    "manque).\n"
    "- Distingue ce qui est constate (donnees) de ce qui est suppose (cause "
    "probable). Une cause probable est formulee au conditionnel.\n"
    "- Heures en heure de Paris (HH:MM). Francais, phrases courtes, style "
    "operationnel, pas de markdown, pas d'emoji.\n"
    "- 60 mots maximum par cle.\n\n"
    "Reponds par un objet JSON STRICT, sans texte autour, avec exactement ces "
    "5 cles (valeurs : chaines) :\n"
    "{\"contexte\": \"ce qui se passe, en 1 a 2 phrases\", "
    "\"cause_probable\": \"cause la plus probable d'apres les donnees\", "
    "\"evolution\": \"tendance depuis le declenchement : s'aggrave / stable / "
    "se resorbe / donnee insuffisante, avec les chiffres qui le montrent\", "
    "\"action_suggeree\": \"1 a 3 actions concretes et conditionnelles\", "
    "\"confiance\": \"haute, moyenne ou faible, suivi d'une justification "
    "courte\"}"
)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _aware_utc(dt):
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _paris(dt):
    """datetime Mongo (naif = UTC) -> 'JJ/MM HH:MM' heure de Paris."""
    dt = _aware_utc(dt)
    return dt.astimezone(PARIS).strftime("%d/%m %H:%M") if dt else None


def _paris_naive(dt_utc):
    return _aware_utc(dt_utc).astimezone(PARIS).replace(tzinfo=None)


def _jsonable(v, depth=0):
    """Rend un contexte serialisable et borne (dates en heure de Paris,
    listes tronquees, pas de NaN)."""
    if depth > 6:
        return None
    if isinstance(v, datetime):
        return _paris(v)
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 3)
    if isinstance(v, dict):
        return {str(k): _jsonable(x, depth + 1) for k, x in v.items()
                if not str(k).startswith("_") or str(k) == "_id"}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x, depth + 1) for x in list(v)[:40]]
    if isinstance(v, str):
        return v[:600]
    return v


def _hav_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Chargement de l'alerte
# ---------------------------------------------------------------------------

def load_alert(db, alert_id):
    """L'alerte, normalisee, ou None. Cherche dans les alertes actives, les
    archives, puis l'historique (par alert_id puis par _id de l'entree)."""
    alert_id = str(alert_id or "").strip()
    if not re.match(r"^[0-9a-fA-F]{24}$", alert_id):
        return None
    oid = ObjectId(alert_id)
    doc = db["cockpit_active_alerts"].find_one({"_id": oid})
    source = "active"
    if not doc:
        doc = db["cockpit_active_alerts_archive"].find_one({"_id": oid})
        source = "archive"
    if doc:
        return {
            "key": alert_id,
            "source": source,
            "slug": doc.get("definition_slug") or "",
            "title": doc.get("title") or "",
            "message": doc.get("message") or "",
            "timeStr": doc.get("timeStr") or "",
            "actionData": doc.get("actionData") or {},
            "triggeredAt": _aware_utc(doc.get("triggeredAt")),
            "event": doc.get("event") or "",
            "year": doc.get("year") or "",
            "taken_by_name": doc.get("taken_by_name"),
            "taken_at": _aware_utc(doc.get("taken_at")),
        }
    hist = db["cockpit_alert_history"].find_one({"alert_id": alert_id})
    if not hist:
        hist = db["cockpit_alert_history"].find_one({"_id": oid})
    if not hist:
        return None
    key = hist.get("alert_id") or str(hist.get("_id"))
    return {
        "key": key,
        "source": "historique",
        "slug": hist.get("type") or "",
        "title": hist.get("title") or "",
        "message": hist.get("message") or "",
        "timeStr": hist.get("timeStr") or "",
        "actionData": hist.get("actionData") or {},
        "triggeredAt": _aware_utc(hist.get("createdAt")),
        "event": "",
        "year": "",
        "taken_by_name": None,
        "taken_at": None,
    }


def load_definition(db, slug):
    return db["cockpit_alert_definitions"].find_one(
        {"slug": slug}, {"slug": 1, "name": 1, "detection_type": 1, "params": 1,
                         "display_mode": 1, "description": 1}) or {}


def detection_kind(alert, definition):
    """Type de contexte a rassembler."""
    dt = definition.get("detection_type") or ""
    ad = alert.get("actionData") or {}
    slug = alert.get("slug") or ""
    if slug in ("field_sos", "field-sos") or dt == "field_sos":
        return "field_sos"
    if (ad.get("event_type") and ad.get("camera_path")) or dt in ("camera_event", "anpr_watchlist"):
        return "camera"
    if dt.startswith("meteo") or slug.startswith("meteo"):
        return "meteo"
    if dt in ("traffic_cluster", "pcorg_urgency", "checkpoint_error_burst",
              "checkpoint_reassign", "door_saturation_forecast"):
        return dt
    if slug == "traffic-cluster" or ad.get("pins"):
        return "traffic_cluster"
    if ad.get("pcorg_id"):
        return "pcorg_urgency"
    return "generique"


# ---------------------------------------------------------------------------
# Contexte par type
# ---------------------------------------------------------------------------

def ctx_traffic(db, alert, definition, now):
    ad = alert.get("actionData") or {}
    pins = [p for p in (ad.get("pins") or []) if _num(p.get("lat")) is not None and _num(p.get("lon")) is not None]
    out = {"pins_au_declenchement": len(pins)}
    if not pins:
        out["note"] = "aucune position dans l'alerte"
        return out
    clat = sum(float(p["lat"]) for p in pins) / len(pins)
    clon = sum(float(p["lon"]) for p in pins) / len(pins)
    radius = float((definition.get("params") or {}).get("radius_m") or 500) + 200

    def _near(alerts):
        res = []
        for a in alerts or []:
            loc = (a or {}).get("location") or {}
            y, x = _num(loc.get("y")), _num(loc.get("x"))
            if y is None or x is None:
                continue
            d = _hav_m(clat, clon, y, x)
            if d <= radius:
                res.append((a, d))
        return res

    latest = db["waze_alerts"].find_one({"_id": "latest"}) or {}
    near = _near(latest.get("data"))
    now_ms = now.timestamp() * 1000
    out["alertes_waze_actuelles"] = [{
        "type": a.get("type"), "sous_type": a.get("subtype") or "",
        "rue": a.get("street") or "", "distance_m": int(d),
        "age_min": int((now_ms - a["pubMillis"]) / 60000) if isinstance(a.get("pubMillis"), (int, float)) else None,
        "fiabilite": a.get("reliability"),
    } for a, d in sorted(near, key=lambda z: z[1])[:25]]
    out["releve_waze"] = _paris(latest.get("fetched_at"))

    # Evolution : comptes par type dans le meme rayon, sur les releves
    # historiques (un toutes les 16-18 min), de 90 min avant l'alerte a
    # 60 min apres (une alerte ancienne ne doit pas etre expliquee par les
    # releves de l'apres-midi).
    trig = alert.get("triggeredAt") or now
    t0 = trig - timedelta(minutes=90)
    t1 = min(now, trig + timedelta(minutes=60))
    win = {"$gte": t0.replace(tzinfo=None), "$lte": t1.replace(tzinfo=None)}
    if now - trig > timedelta(minutes=60):
        out["avertissement"] = ("alerte ancienne : 'alertes_waze_actuelles' et 'axes_proches' "
                                "decrivent la situation ACTUELLE, pas celle du declenchement")
    evol = []
    for h in db["waze_alerts_history"].find({"fetched_at": win},
                                            {"fetched_at": 1, "data": 1}).sort("fetched_at", 1):
        c = {}
        for a, _ in _near(h.get("data")):
            t = str(a.get("type") or "?").upper()
            c[t] = c.get(t, 0) + 1
        evol.append({"heure": _paris(h.get("fetched_at")), "comptes": c})
    out["evolution_releves"] = evol[-10:]

    try:
        import trafic_etat
        routes = trafic_etat.lire_routes(db)
        alertes = trafic_etat.lire_alertes(db)
        comptes = trafic_etat.compter_alertes(alertes)
        out["verdict_mur"] = trafic_etat.verdict_global(comptes, trafic_etat.pire_severite_mur(routes))
        out["verdict_echelle"] = "0 fluide, 1 vigilance, 2 tension, 3 critique"
        axes = []
        for axe in trafic_etat.axes_mur(routes):
            d = trafic_etat.distance_point_axe_m(clat, clon, axe)
            if d is not None and d <= 1500:
                axes.append({"axe": axe["nom"], "direction": axe["direction"],
                             "temps_s": axe["currentTime"], "habituel_s": axe["historicTime"],
                             "severite_0_4": axe["severity"], "distance_m": int(d)})
        axes.sort(key=lambda a: a["distance_m"])
        out["axes_proches"] = axes[:6]
        names = {a["axe"] for a in axes[:6]}
        if names:
            serie = []
            for h in db["waze_trafic_history"].find(
                    {"fetched_at": win},
                    {"fetched_at": 1, "data.routes": 1}).sort("fetched_at", 1):
                pts = {a["nom"]: a["currentTime"] for a in trafic_etat.axes_mur(
                    (h.get("data") or {}).get("routes")) if a["nom"] in names}
                serie.append({"heure": _paris(h.get("fetched_at")), "temps_s": pts})
            out["evolution_axes"] = serie[-8:]
    except Exception as exc:
        logger.warning("alert_ai trafic : %s", exc)
        out["trafic_etat"] = "indisponible"
    return out


def ctx_pcorg(db, alert, definition, now):
    ad = alert.get("actionData") or {}
    fid = ad.get("pcorg_id")
    out = {}
    fiche = db["pcorg"].find_one({"_id": fid}) if fid else None
    if not fiche and ad.get("sql_id"):
        fiche = db["pcorg"].find_one({"sql_id": ad.get("sql_id")})
    if not fiche:
        out["fiche"] = "introuvable"
        return out
    cc = {k: v for k, v in (fiche.get("content_category") or {}).items()
          if isinstance(v, (str, int, float, bool)) and v not in ("", None)}
    area = (fiche.get("area") or {}).get("desc") if isinstance(fiche.get("area"), dict) else None
    out["fiche"] = {
        "categorie": fiche.get("category"), "niveau_urgence": fiche.get("niveau_urgence"),
        "texte": (fiche.get("text_full") or fiche.get("text") or "")[:600],
        "zone": area, "carroye": fiche.get("carroye"),
        "statut": "close" if fiche.get("status_code") == 10 else "ouverte",
        "ouverte_a": _paris(fiche.get("ts")), "close_a": _paris(fiche.get("close_ts")),
        "operateur": fiche.get("operator"), "details": cc,
    }
    hist = fiche.get("comment_history") or []
    out["chronologie"] = [{
        "heure": _paris(h.get("ts")) if isinstance(h.get("ts"), datetime) else str(h.get("ts") or "")[:16],
        "auteur": h.get("operator") or h.get("author") or "",
        "texte": str(h.get("text") or "")[:200],
    } for h in hist[-10:] if isinstance(h, dict)]
    anchor = _aware_utc(fiche.get("ts")) or alert.get("triggeredAt") or now
    lo = (anchor - timedelta(minutes=60)).replace(tzinfo=None)
    hi = (anchor + timedelta(minutes=30)).replace(tzinfo=None)
    ors = [{"category": fiche.get("category")}]
    if area:
        ors.append({"area.desc": area})
    voisines = []
    for f in db["pcorg"].find({"ts": {"$gte": lo, "$lte": hi}, "$or": ors,
                               "_id": {"$ne": fiche.get("_id")}}).sort("ts", -1).limit(15):
        voisines.append({
            "heure": _paris(f.get("ts")), "categorie": f.get("category"),
            "niveau": f.get("niveau_urgence"),
            "zone": (f.get("area") or {}).get("desc") if isinstance(f.get("area"), dict) else None,
            "texte": str(f.get("text") or "")[:140],
            "statut": "close" if f.get("status_code") == 10 else "ouverte",
        })
    out["fiches_meme_zone_ou_categorie_60min"] = voisines
    return out


def ctx_meteo(db, alert, definition, now):
    out = {}
    trig = alert.get("triggeredAt")
    if trig and (now - trig) > timedelta(hours=3):
        out["avertissement"] = ("donnees meteo ACTUELLES, pas celles du moment de "
                                "l'alerte (declenchee il y a plus de 3 h)")
    try:
        import meteo_etat
        etat = meteo_etat.etat_mur(db, _paris_naive(now))
        for k in ("actuel", "prochaine_pluie", "consignes", "contraintes", "verdict", "vigilance"):
            out[k] = etat.get(k)
        out["prochaines_heures"] = (etat.get("prochaines") or [])[:6]
    except Exception as exc:
        logger.warning("alert_ai meteo : %s", exc)
        out["meteo_etat"] = "indisponible"
    return out


def _utid_kind(utid):
    u = str(utid or "")
    if u.startswith("ACO_"):
        return "logique ACO"
    if u.isdigit() and len(u) == 16:
        return "RFID 16 chiffres"
    if u.isdigit() and len(u) >= 24:
        return "code-barres long"
    if "://" in u or u.lower().startswith("http"):
        return "QR/URL parasite"
    return "autre"


def ctx_checkpoint(db, alert, definition, now):
    ad = alert.get("actionData") or {}
    cp = ad.get("checkpoint") or ad.get("checkpoint_name") or ""
    anchor = alert.get("triggeredAt") or now
    anchor_p = _paris_naive(anchor)
    lo = (anchor_p - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    hi = (anchor_p + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    out = {"checkpoint": cp}
    if cp:
        errs = list(db["hsh_erreurs"].find(
            {"checkpoint.Name": cp, "date_paris": {"$gte": lo, "$lte": hi}},
            {"status_label": 1, "direction": 1, "utid": 1, "gate": 1, "area": 1, "date_paris": 1}).limit(500))
        by_status, by_dir, by_fmt = {}, {}, {}
        for e in errs:
            s = e.get("status_label") or "?"
            by_status[s] = by_status.get(s, 0) + 1
            d = e.get("direction") or "?"
            by_dir[d] = by_dir.get(d, 0) + 1
            f = _utid_kind(e.get("utid"))
            by_fmt[f] = by_fmt.get(f, 0) + 1
        out["erreurs_checkpoint_20min"] = {"total": len(errs), "par_statut": by_status,
                                           "par_sens": by_dir, "format_titre": by_fmt}
        gate = ad.get("gate") or (errs[0].get("gate") or {}).get("Name") if errs else ad.get("gate")
        if gate:
            n_gate = db["hsh_erreurs"].count_documents(
                {"gate.Name": gate, "date_paris": {"$gte": lo, "$lte": hi}})
            out["erreurs_porte_20min"] = {"porte": gate, "total": n_gate}
        struct = db["hsh_structure"].find_one({"location_type": "Checkpoint", "location_name": cp},
                                              {"parent_gate": 1, "parent_area": 1, "derniere_transaction": 1})
        if struct:
            out["structure"] = {"porte": (struct.get("parent_gate") or {}).get("name"),
                                "zone": (struct.get("parent_area") or {}).get("name"),
                                "derniere_transaction": struct.get("derniere_transaction")}
        tr = list(db["hsh_transactions_agg"].find(
            {"checkpoint_name": cp,
             "tranche": {"$gte": anchor_p - timedelta(minutes=30), "$lte": anchor_p + timedelta(minutes=10)}},
            {"tranche": 1, "ok": 1, "erreurs": 1, "entrees": 1, "sorties": 1}).sort("tranche", 1))
        out["flux_checkpoint_5min"] = [{
            "tranche": t["tranche"].strftime("%H:%M") if isinstance(t.get("tranche"), datetime) else None,
            "ok": t.get("ok"), "erreurs": t.get("erreurs"),
            "entrees": t.get("entrees"), "sorties": t.get("sorties")} for t in tr]
    if ad.get("old_gate") or ad.get("new_gate"):
        out["reaffectation"] = {"ancienne_porte": ad.get("old_gate"), "nouvelle_porte": ad.get("new_gate")}
        for key in ("old_gate", "new_gate"):
            g = ad.get(key)
            if g:
                out["checkpoints_" + key] = [c.get("location_name") for c in db["hsh_structure"].find(
                    {"location_type": "Checkpoint", "parent_gate.name": g}, {"location_name": 1}).limit(30)]
    return out


def ctx_sos(db, alert, definition, now):
    ad = alert.get("actionData") or {}
    out = {"tablette": ad.get("device_name"), "position_sos": None,
           "batterie_sos": ad.get("battery"), "pris_en_charge_par": alert.get("taken_by_name"),
           "pris_en_charge_a": _paris(alert.get("taken_at"))}
    lat, lng = _num(ad.get("lat")), _num(ad.get("lng"))
    if lat is not None and lng is not None:
        out["position_sos"] = {"lat": round(lat, 5), "lng": round(lng, 5)}
    dev = None
    if ad.get("device_id") and re.match(r"^[0-9a-fA-F]{24}$", str(ad["device_id"])):
        dev = db["field_devices"].find_one({"_id": ObjectId(ad["device_id"])})
    if dev:
        lp = dev.get("last_position") or {}
        out["tablette_maintenant"] = {
            "derniere_position": _paris(lp.get("ts")), "batterie": lp.get("battery"),
            "precision_m": lp.get("accuracy"), "vitesse": lp.get("speed"),
            "statut": dev.get("patrol_status") or dev.get("status"),
            "derniere_activite": _paris(dev.get("last_seen")),
        }
        if lat is not None and _num(lp.get("lat")) is not None:
            out["tablette_maintenant"]["deplacement_depuis_sos_m"] = int(
                _hav_m(lat, lng, float(lp["lat"]), float(lp["lng"])))
    if lat is not None and lng is not None:
        q = {"last_position.lat": {"$exists": True}}
        if dev is not None:
            q["_id"] = {"$ne": dev["_id"]}
            if dev.get("event"):
                q["event"] = dev.get("event")
        voisins = []
        fresh = (now - timedelta(minutes=30)).replace(tzinfo=None)
        for d in db["field_devices"].find(q, {"name": 1, "last_position": 1, "patrol_status": 1,
                                              "status": 1, "revoked": 1}).limit(300):
            lp = d.get("last_position") or {}
            if d.get("revoked") or _num(lp.get("lat")) is None or _num(lp.get("lng")) is None:
                continue
            ts = lp.get("ts")
            if isinstance(ts, datetime) and ts.replace(tzinfo=None) < fresh:
                continue
            voisins.append({"tablette": d.get("name"),
                            "distance_m": int(_hav_m(lat, lng, float(lp["lat"]), float(lp["lng"]))),
                            "statut": d.get("patrol_status") or d.get("status"),
                            "position_a": _paris(ts)})
        voisins.sort(key=lambda v: v["distance_m"])
        out["tablettes_les_plus_proches"] = voisins[:5]
    if ad.get("pcorg_id"):
        f = db["pcorg"].find_one({"_id": ad["pcorg_id"]}, {"status_code": 1, "comment_history": 1})
        if f:
            out["fiche_sos"] = {"statut": "close" if f.get("status_code") == 10 else "ouverte",
                                "chronologie": [str(h.get("text") or "")[:160]
                                                for h in (f.get("comment_history") or [])[-6:]
                                                if isinstance(h, dict)]}
    return out


def ctx_camera(db, alert, definition, now):
    ad = alert.get("actionData") or {}
    out = {"evenement_camera": {k: ad.get(k) for k in (
        "event_type", "event_label", "camera_label", "camera_location", "camera_path",
        "plate", "watchlist_reason", "target_type", "confidence", "has_snapshot") if ad.get(k) not in (None, "")}}
    path = ad.get("camera_path")
    if path:
        lo = ((alert.get("triggeredAt") or now) - timedelta(minutes=60)).replace(tzinfo=None)
        rec = list(db["cockpit_alert_history"].find(
            {"actionData.camera_path": path, "createdAt": {"$gte": lo}},
            {"createdAt": 1, "title": 1, "actionData.event_type": 1}).sort("createdAt", -1).limit(20))
        out["alertes_meme_camera_60min"] = [{"heure": _paris(r.get("createdAt")),
                                             "type": (r.get("actionData") or {}).get("event_type"),
                                             "titre": r.get("title")} for r in rec]
    return out


def ctx_door(db, alert, definition, now):
    ad = alert.get("actionData") or {}
    out = {"prevision": {k: ad.get(k) for k in (
        "door", "current_rate", "predicted_rate", "predicted_at", "capacity", "capacity_source",
        "threshold_pct", "devices", "method", "n1_year", "n1_source", "n1_rate_now", "sens",
        "error_pct")},
        "note_methode": ("debit par porte = passages HSH par tranche de 5 min ; capacite = "
                         "somme des appareils actifs (tripode ~900/h, PDA ~650/h) sauf "
                         "surcharge ; methode profil_n1 = debit courant x profil de la meme "
                         "porte l'an dernier au meme jour de course")}
    door = ad.get("door")
    if door:
        anchor = _paris_naive(alert.get("triggeredAt") or now)
        serie = {}
        for t in db["hsh_transactions_agg"].find(
                {"gate_name": door, "tranche": {"$gte": anchor - timedelta(minutes=60),
                                                "$lte": _paris_naive(now)}},
                {"tranche": 1, "entrees": 1, "erreurs": 1}):
            k = t["tranche"].strftime("%H:%M") if isinstance(t.get("tranche"), datetime) else None
            if k:
                s = serie.setdefault(k, {"entrees": 0, "erreurs": 0})
                s["entrees"] += int(t.get("entrees") or 0)
                s["erreurs"] += int(t.get("erreurs") or 0)
        out["entrees_par_5min"] = [dict(tranche=k, **v) for k, v in sorted(serie.items())][-24:]
    return out


def ctx_generic(db, alert, definition, now):
    return {"donnees_alerte": alert.get("actionData") or {}}


CONTEXT_BUILDERS = {
    "traffic_cluster": ctx_traffic,
    "pcorg_urgency": ctx_pcorg,
    "meteo": ctx_meteo,
    "checkpoint_error_burst": ctx_checkpoint,
    "checkpoint_reassign": ctx_checkpoint,
    "field_sos": ctx_sos,
    "camera": ctx_camera,
    "door_saturation_forecast": ctx_door,
    "generique": ctx_generic,
}


def build_explain_context(db, alert, definition, now):
    kind = detection_kind(alert, definition)
    try:
        specific = CONTEXT_BUILDERS.get(kind, ctx_generic)(db, alert, definition, now)
    except Exception as exc:
        logger.warning("alert_ai contexte %s : %s", kind, exc)
        specific = {"erreur": "contexte indisponible", "donnees_alerte": alert.get("actionData") or {}}
    trig = alert.get("triggeredAt")
    return kind, _jsonable({
        "maintenant": _paris(now),
        "alerte": {
            "definition": definition.get("name") or alert.get("slug"),
            "type_detection": definition.get("detection_type") or kind,
            "parametres_definition": definition.get("params") or {},
            "titre": alert.get("title"), "message": alert.get("message"),
            "heure_affichee": alert.get("timeStr"),
            "declenchee_a": _paris(trig),
            "age_min": int((now - trig).total_seconds() // 60) if trig else None,
            "evenement": alert.get("event"), "annee": alert.get("year"),
            "prise_en_compte_par": alert.get("taken_by_name"),
        },
        "contexte": specific,
    })


def build_prompts(context):
    blob = json.dumps(context, ensure_ascii=False, default=str)
    if len(blob) > MAX_CONTEXT_CHARS:
        blob = blob[:MAX_CONTEXT_CHARS] + " ...(tronque)"
    user = ("Explique cette alerte a l'operateur du PC. Donnees disponibles "
            "(JSON) :\n" + blob)
    return SYSTEM_PROMPT, user


def parse_explanation(raw):
    """Dict des 5 cles (chaines) ou None si rien d'exploitable."""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1] if "\n" in txt else ""
        if txt.rstrip().endswith("```"):
            txt = txt.rstrip()[:-3]
    a, b = txt.find("{"), txt.rfind("}")
    data = None
    if a >= 0 and b > a:
        try:
            data = json.loads(txt[a:b + 1])
        except ValueError:
            data = None
    if not isinstance(data, dict):
        data = {}
        for k, v in re.findall(r'"([a-z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', txt, re.DOTALL):
            try:
                data[k] = json.loads('"' + v + '"')
            except ValueError:
                data[k] = v
    out = {}
    for k in EXPLAIN_KEYS:
        v = data.get(k)
        if isinstance(v, list):
            v = "\n".join("- " + str(x) for x in v)
        if v is not None:
            out[k] = str(v).strip()[:1200]
    if not out:
        return None
    for k in EXPLAIN_KEYS:
        out.setdefault(k, "donnee insuffisante")
    return out


# ---------------------------------------------------------------------------
# Appel modele + cache
# ---------------------------------------------------------------------------

def _pcorg_summary():
    import pcorg_summary
    return pcorg_summary


def api_key_configured():
    try:
        return bool(getattr(_pcorg_summary(), "ANTHROPIC_API_KEY", ""))
    except Exception:
        return False


def default_model():
    try:
        return getattr(_pcorg_summary(), "CLAUDE_MODEL", None) or "claude-sonnet-4-6"
    except Exception:
        return "claude-sonnet-4-6"


def call_model(system, user, db=None):
    """(raw_text, usage, model). Leve pcorg_summary.ClaudeError.

    N'utilise que les arguments que _claude_stream_request accepte
    reellement : `db` (garde de budget IA) et `effort` ("low", pour que la
    reflexion adaptative des modeles recents ne consomme pas le petit budget
    de sortie) sont passes seulement s'ils existent dans sa signature.
    """
    import inspect
    ps = _pcorg_summary()
    model = default_model()
    fn = ps._claude_stream_request
    try:
        accepted = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        accepted = set()
    kwargs = {"model": model}
    if "db" in accepted and db is not None:
        kwargs["db"] = db
    if "effort" in accepted:
        kwargs["effort"] = "low"
    raw, usage, _stop = fn(system, user, MAX_TOKENS, **kwargs)
    return raw, usage, model


def record_usage(db, model, usage, meta):
    try:
        fn = getattr(_pcorg_summary(), "record_ai_usage", None)
    except Exception:
        fn = None
    if not callable(fn):
        return
    try:
        fn(db, FEATURE, model, usage, meta=meta)
    except Exception as exc:
        logger.warning("alert_ai record_ai_usage : %s", exc)


_index_ok = False


def ensure_indexes(db):
    global _index_ok
    if _index_ok:
        return
    try:
        db[COL_EXPLAIN].create_index("created_at", expireAfterSeconds=EXPLAIN_TTL_S)
        _index_ok = True
    except Exception as exc:
        logger.warning("alert_ai index : %s", exc)


def _public(doc, cached, now):
    created = _aware_utc(doc.get("created_at"))
    age = int((now - created).total_seconds()) if created else None
    return {
        "ok": True,
        "alert_id": doc.get("_id"),
        "sections": doc.get("sections") or {},
        "kind": doc.get("kind"),
        "model": doc.get("model"),
        "created_at": created.isoformat() if created else None,
        "created_by_name": doc.get("created_by_name"),
        "cached": cached,
        "age_s": age,
        "can_refresh": age is not None and age >= REFRESH_MIN_AGE_S,
    }


def explain_alert(db, alert_id, user=None, refresh=False, now=None, caller=None,
                  allowed_slugs=None):
    """Logique de la route, sans Flask. Rend (code HTTP, corps).

    caller(system, user) -> (raw, usage, model) : injectable pour les tests.
    allowed_slugs : None = tout, sinon liste des slugs visibles.
    """
    now = now or datetime.now(timezone.utc)
    user = user or {}
    alert = load_alert(db, alert_id)
    if not alert:
        return 404, {"ok": False, "error": "alerte_introuvable"}
    if allowed_slugs is not None and alert["slug"] not in allowed_slugs:
        return 404, {"ok": False, "error": "alerte_introuvable"}
    key = alert["key"]
    col = db[COL_EXPLAIN]
    ensure_indexes(db)

    cur = col.find_one({"_id": key})
    if cur and cur.get("status") == "done":
        created = _aware_utc(cur.get("created_at"))
        if not refresh:
            return 200, _public(cur, True, now)
        if created and (now - created).total_seconds() < REFRESH_MIN_AGE_S:
            body = _public(cur, True, now)
            body["refresh_refused"] = True
            body["retry_after_s"] = int(REFRESH_MIN_AGE_S - (now - created).total_seconds())
            return 200, body
    if cur and cur.get("status") == "pending":
        started = _aware_utc(cur.get("started_at"))
        if started and (now - started).total_seconds() < PENDING_STALE_S:
            return 202, {"ok": False, "error": "en_cours", "retry_after_s": 3}

    if caller is None:
        if not api_key_configured():
            return 503, {"ok": False, "error": "cle_api_absente"}

        def caller(system, user_prompt):
            return call_model(system, user_prompt, db=db)

    # Reservation : un seul appel par alerte, meme sous clics simultanes.
    name = ("%s %s" % (user.get("firstname", ""), user.get("lastname", ""))).strip() or user.get("email", "")
    pending = {"status": "pending", "started_at": now.replace(tzinfo=None),
               "created_at": now.replace(tzinfo=None)}
    if cur is None:
        try:
            col.insert_one(dict(_id=key, **pending))
        except Exception:
            return 202, {"ok": False, "error": "en_cours", "retry_after_s": 3}
    else:
        flt = {"_id": key, "status": cur.get("status")}
        if cur.get("status") == "pending":
            flt["started_at"] = cur.get("started_at")
        else:
            flt["created_at"] = cur.get("created_at")
        res = col.update_one(flt, {"$set": pending})
        if getattr(res, "modified_count", 1) == 0 and getattr(res, "matched_count", 1) == 0:
            return 202, {"ok": False, "error": "en_cours", "retry_after_s": 3}

    definition = load_definition(db, alert["slug"])
    kind, context = build_explain_context(db, alert, definition, now)
    system, user_prompt = build_prompts(context)
    try:
        raw, usage, model = caller(system, user_prompt)
    except Exception as exc:
        code = str(exc) or exc.__class__.__name__
        logger.warning("alert_ai appel modele (%s) : %s", key, code)
        col.delete_one({"_id": key, "status": "pending"})
        if "ANTHROPIC_API_KEY" in code:
            return 503, {"ok": False, "error": "cle_api_absente"}
        err = code if re.match(r"^[a-z0-9_]{3,60}$", code) else "claude_unreachable"
        return 502, {"ok": False, "error": err}
    record_usage(db, model, usage, {"alert_id": key, "detection_kind": kind,
                                    "slug": alert["slug"]})
    sections = parse_explanation(raw)
    if not sections:
        col.delete_one({"_id": key, "status": "pending"})
        return 502, {"ok": False, "error": "reponse_illisible"}
    doc = {
        "_id": key, "status": "done", "sections": sections, "kind": kind,
        "slug": alert["slug"], "model": model, "usage": usage,
        "created_at": now.replace(tzinfo=None), "created_by": user.get("email", ""),
        "created_by_name": name,
    }
    col.update_one({"_id": key}, {"$set": {k: v for k, v in doc.items() if k != "_id"}}, upsert=True)
    return 200, _public(doc, False, now)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

def _user_required(f):
    """role_required("user") d'app.py, importe tardivement (app importe ce
    module avant d'avoir defini son decorateur)."""
    from functools import wraps

    @wraps(f)
    def enveloppe(*args, **kwargs):
        from app import role_required
        return role_required("user")(f)(*args, **kwargs)
    return enveloppe


@alert_ai_bp.route("/api/alerts/<alert_id>/explain", methods=["POST"])
@_user_required
def explain_route(alert_id):
    # Coupee par defaut : le bouton a ete retire des alertes (un appel au
    # modele par clic d'operateur). Un onglet reste sur l'ancien JS ne doit
    # plus pouvoir consommer. ALERT_AI_EXPLAIN=1 la reactive.
    if not EXPLAIN_ENABLED:
        return jsonify({"ok": False, "error": "desactive"}), 410
    from app import db, _get_user_alert_slugs
    payload = getattr(request, "user_payload", {}) or {}
    try:
        allowed = _get_user_alert_slugs(payload)
    except Exception:
        allowed = None
    refresh = str(request.args.get("refresh", "")).strip() in ("1", "true", "yes")
    try:
        status, body = explain_alert(db, alert_id, user=payload, refresh=refresh,
                                     allowed_slugs=allowed)
    except Exception:
        logger.exception("alert_ai explain %s", alert_id)
        return jsonify({"ok": False, "error": "erreur_interne"}), 500
    return jsonify(body), status
