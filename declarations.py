"""Constats terrain des tablettes Field en mode declarant (07/10/2026).

Une tablette dont le GROUPE de balises est coche "mode declarant"
(field._device_declarant) ne cree jamais de fiche : elle depose un CONSTAT
(degat, probleme, demande de reparation) avec photos, description, priorite
et position. Les constats vivent dans la collection `declarations`, se
suivent sur la page cockpit /declarations, et un operateur habilite (droit de
groupe `can_convert_declaration`, ou admin) les transforme en fiche
d'intervention avec l'assistant de creation habituel (pcorg.js, mode creation
seule) ou les classe sans suite.

Cycle : nouvelle -> en_suivi -> transformee (fiche liee) | classee.
Le declarant voit l'etat de ses constats sur sa tablette ; une fois la fiche
liee close, son constat apparait "Traite".

Envoi du constat (mail avec resume IA) : prepare dans
declarations_report.py, PAS encore branche (route d'envoi en 501).

Document `declarations` :
  _id (uuid), ref ("C-2026-0001"), event, year (int), created_at,
  device_id, device_name, group_id, group_label,
  text, priority (basse|normale|haute), gps {lat, lng} | None, carroye,
  photos [{photo, thumb, ts}], history [{ts, by, origin, kind, text, photos?}],
  status, status_at, status_by, fiche_id, converted_at, converted_by,
  classed_reason, report {state, ...}, client_token
"""
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request, render_template
from pymongo import ReturnDocument, DESCENDING

import field as F

logger = logging.getLogger(__name__)

declarations_bp = Blueprint("declarations", __name__)

COL = "declarations"
STATUSES = ("nouvelle", "en_suivi", "transformee", "classee")
PRIORITIES = {
    "basse": "Peut attendre",
    "normale": "A traiter rapidement",
    "haute": "Urgent, danger ou blocage",
}
# Priorite du constat -> urgence proposee a la transformation en fiche
PRIORITY_TO_URGENCY = {"basse": "IMP", "normale": "UR", "haute": "UA"}
TEXT_MAX = 2000
LIST_MAX = 500
FIELD_LIST_MAX = 100


def _now():
    return datetime.now(timezone.utc)


def _db():
    return F._get_mongo_db()


def _iso(v):
    return F._iso(v)


_indexes_done = False


def _ensure_indexes(db):
    global _indexes_done
    if _indexes_done:
        return
    try:
        db[COL].create_index([("created_at", DESCENDING)])
        db[COL].create_index([("device_id", 1), ("created_at", DESCENDING)])
        db[COL].create_index([("status", 1), ("created_at", DESCENDING)])
        db[COL].create_index("fiche_id", sparse=True)
        _indexes_done = True
    except Exception as e:  # pragma: no cover - droits / base indisponible
        logger.debug("declarations index : %s", e)


def _next_ref(db, year):
    """Numero lisible "C-2026-0042", sequence par annee civile."""
    doc = db["declarations_seq"].find_one_and_update(
        {"_id": str(year)}, {"$inc": {"seq": 1}},
        upsert=True, return_document=ReturnDocument.AFTER)
    return "C-%s-%04d" % (year, int(doc.get("seq") or 1))


def _clean_text(s, maxlen=TEXT_MAX):
    return (s or "").strip()[:maxlen]


def _float_or_none(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _gps(lat, lng):
    lat, lng = _float_or_none(lat), _float_or_none(lng)
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"lat": lat, "lng": lng}


def _entry(by, text, origin, kind="note", photos=None, ts=None):
    e = {"ts": ts or _now(), "by": by, "origin": origin, "kind": kind, "text": text or ""}
    if photos:
        e["photos"] = photos
    return e


def _save_photos(db, device, files):
    out = []
    sub_dir = F._device_photo_sub_dir(db, device, True)
    for pf in files:
        url, thumb = F._process_and_save_photo(pf, sub_dir)   # PhotoUploadError remonte
        out.append({"photo": url, "thumb": thumb, "ts": _now()})
    return out


def _photo_files():
    files = [pf for pf in request.files.getlist("photos") if pf and pf.filename]
    if not files:
        one = request.files.get("photo")
        if one and one.filename:
            files = [one]
    return files


def _fiches_by_id(db, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {f["_id"]: f for f in db["pcorg"].find(
        {"_id": {"$in": ids}},
        {"status_code": 1, "close_ts": 1, "category": 1, "niveau_urgence": 1,
         "content_category.patrouille": 1, "text": 1, "event": 1, "year": 1})}


def _fiche_summary(f):
    if not f:
        return None
    return {
        "id": f["_id"],
        "category": f.get("category"),
        "niveau_urgence": f.get("niveau_urgence"),
        "closed": f.get("status_code") == 10,
        "close_ts": _iso(f.get("close_ts")),
        "unit": ((f.get("content_category") or {}).get("patrouille") or None),
        "event": f.get("event"),
        "year": f.get("year"),
    }


def _field_state(d, fiche):
    """Etat montre au declarant sur sa tablette."""
    st = d.get("status")
    if st == "transformee":
        if fiche and fiche.get("status_code") == 10:
            return "traitee", "Traitee"
        return "prise_en_charge", "Prise en charge"
    if st == "classee":
        return "classee", "Classee"
    if st == "en_suivi":
        return "vue", "Vue par le PC"
    return "envoyee", "Envoyee"


def _pub(d, fiche=None, full=False):
    gps = d.get("gps") or {}
    out = {
        "id": d["_id"],
        "ref": d.get("ref"),
        "event": d.get("event"),
        "year": d.get("year"),
        "created_at": _iso(d.get("created_at")),
        "device_name": d.get("device_name"),
        "group_label": d.get("group_label"),
        "text": d.get("text") or "",
        "priority": d.get("priority") or "normale",
        "priority_label": PRIORITIES.get(d.get("priority") or "normale"),
        "lat": gps.get("lat"),
        "lng": gps.get("lng"),
        "carroye": d.get("carroye") or "",
        "photos": [{"photo": p.get("photo"), "thumb": p.get("thumb")} for p in (d.get("photos") or [])],
        "status": d.get("status") or "nouvelle",
        "status_at": _iso(d.get("status_at")),
        "status_by": d.get("status_by"),
        "fiche_id": d.get("fiche_id"),
        "fiche": _fiche_summary(fiche),
        "classed_reason": d.get("classed_reason"),
        "history_count": len(d.get("history") or []),
        # Discussion PC <-> declarant : messages non lus de chaque cote
        "field_unread": int(d.get("field_unread") or 0),
        "cockpit_unread": int(d.get("cockpit_unread") or 0),
        "last_message_at": _iso(d.get("last_message_at")),
        "report_state": (d.get("report") or {}).get("state") or "none",
    }
    st_key, st_label = _field_state(d, fiche)
    out["field_state"] = st_key
    out["field_state_label"] = st_label
    if full:
        out["history"] = [{
            "ts": _iso(h.get("ts")), "by": h.get("by"), "origin": h.get("origin"),
            "kind": h.get("kind"), "text": h.get("text") or "",
            "photos": [{"photo": p.get("photo"), "thumb": p.get("thumb")} for p in (h.get("photos") or [])],
        } for h in (d.get("history") or [])]
    return out


# ---------------------------------------------------------------------------
# Tablette (mode declarant)
# ---------------------------------------------------------------------------

def _require_declarant(db, device):
    if not F._device_declarant(db, device):
        return jsonify({"ok": False, "error": "not_declarant"}), 403
    return None


def _form_or_json():
    if request.content_type and "multipart" in request.content_type:
        return request.form, _photo_files()
    return (request.get_json(silent=True) or {}), []


@declarations_bp.route("/field/declarations", methods=["POST"])
@F.field_token_required
def field_declaration_create():
    """Depot d'un constat : texte obligatoire, photos (0-5), priorite,
    position. Idempotent par `client_token` (file hors ligne)."""
    device = request.device
    db = _db()
    _ensure_indexes(db)
    err = _require_declarant(db, device)
    if err:
        return err
    data, files = _form_or_json()
    text = _clean_text(data.get("text"))
    if not text:
        return jsonify({"ok": False, "error": "empty_text"}), 400
    priority = (data.get("priority") or "normale").strip()
    if priority not in PRIORITIES:
        return jsonify({"ok": False, "error": "invalid_priority"}), 400
    if len(files) > F.FIELD_PHOTO_MAX_PER_BATCH:
        return jsonify({"ok": False, "error": "too_many_photos"}), 400

    token = (data.get("client_token") or "").strip()
    is_new, prev = F._claim_client_request(db, device["_id"], "decl", token)
    if not is_new:
        if prev and prev.get("id"):
            return jsonify({"ok": True, "id": prev["id"], "ref": prev.get("ref"), "duplicate": True})
        return jsonify({"ok": False, "error": "in_progress"}), 409

    try:
        photos = _save_photos(db, device, files)
    except F.PhotoUploadError as e:
        return jsonify({"ok": False, "error": e.code}), e.status

    # Le constat appartient a l'evenement de la tablette (son appairage ;
    # SAISON d'une annee passee -> SAISON courant), a defaut l'evenement courant.
    event, year = F.device_home_pair(device) or F.fiche_target_event(db, device)
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        year_int = _now().year
    groups = F._beacon_groups(db)
    grp = groups.get(device.get("beacon_group_id")) or {}
    now = _now()
    name = device.get("name") or "?"
    doc = {
        "_id": str(uuid.uuid4()),
        "ref": _next_ref(db, year_int),
        "event": event,
        "year": year_int,
        "created_at": now,
        "device_id": str(device["_id"]),
        "device_name": name,
        "group_id": device.get("beacon_group_id"),
        "group_label": grp.get("label") or device.get("beacon_group_id"),
        "text": text,
        "priority": priority,
        "gps": _gps(data.get("lat"), data.get("lng")),
        "carroye": _clean_text(data.get("carroye"), 20),
        "photos": photos,
        "history": [_entry("field:" + name, text, "field", kind="declaration", photos=photos, ts=now)],
        "status": "nouvelle",
        "status_at": now,
        "status_by": None,
        "fiche_id": None,
        "report": {"state": "none"},
        "client_token": token or None,
    }
    db[COL].insert_one(doc)
    F._finish_client_request(db, device["_id"], "decl", token, {"id": doc["_id"], "ref": doc["ref"]})
    return jsonify({"ok": True, "id": doc["_id"], "ref": doc["ref"]})


@declarations_bp.route("/field/declarations", methods=["GET"])
@F.field_token_required
def field_declarations_mine():
    device = request.device
    db = _db()
    err = _require_declarant(db, device)
    if err:
        return err
    docs = list(db[COL].find({"device_id": str(device["_id"])})
                .sort("created_at", DESCENDING).limit(FIELD_LIST_MAX))
    fiches = _fiches_by_id(db, [d.get("fiche_id") for d in docs])
    return jsonify({"ok": True, "declarations": [_pub(d, fiches.get(d.get("fiche_id"))) for d in docs]})


def _own_declaration(db, device, decl_id):
    d = db[COL].find_one({"_id": decl_id})
    if not d or d.get("device_id") != str(device["_id"]):
        return None
    return d


@declarations_bp.route("/field/declarations/<decl_id>", methods=["GET"])
@F.field_token_required
def field_declaration_detail(decl_id):
    device = request.device
    db = _db()
    err = _require_declarant(db, device)
    if err:
        return err
    d = _own_declaration(db, device, decl_id)
    if not d:
        return jsonify({"ok": False, "error": "not_found"}), 404
    fiche = _fiches_by_id(db, [d.get("fiche_id")]).get(d.get("fiche_id"))
    out = _pub(d, fiche, full=True)
    # Le declarant ne lit pas les notes internes du PC (messages et statuts oui)
    out["history"] = [h for h in out["history"]
                      if h["origin"] == "field" or h["kind"] in ("status", "message")]
    # Ouvrir le constat = lire les messages du PC
    if d.get("field_unread"):
        db[COL].update_one({"_id": decl_id}, {"$set": {"field_unread": 0}})
    return jsonify(dict(out, ok=True))


@declarations_bp.route("/field/declarations/<decl_id>/comment", methods=["POST"])
@F.field_token_required
def field_declaration_comment(decl_id):
    """Complement du declarant (precision, photo "apres")."""
    device = request.device
    db = _db()
    err = _require_declarant(db, device)
    if err:
        return err
    d = _own_declaration(db, device, decl_id)
    if not d:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if d.get("status") == "classee":
        return jsonify({"ok": False, "error": "classee"}), 409
    data, files = _form_or_json()
    text = _clean_text(data.get("comment") or data.get("text"))
    if not text and not files:
        return jsonify({"ok": False, "error": "empty_comment"}), 400
    if len(files) > F.FIELD_PHOTO_MAX_PER_BATCH:
        return jsonify({"ok": False, "error": "too_many_photos"}), 400
    try:
        photos = _save_photos(db, device, files)
    except F.PhotoUploadError as e:
        return jsonify({"ok": False, "error": e.code}), e.status
    now = _now()
    upd = {"$push": {"history": _entry("field:" + (device.get("name") or "?"), text, "field",
                                       kind="complement", photos=photos, ts=now)},
           # Signale la reponse sur la page Constats terrain
           "$inc": {"cockpit_unread": 1},
           "$set": {"last_message_at": now, "field_unread": 0}}
    if photos:
        upd["$push"]["photos"] = {"$each": photos}
    db[COL].update_one({"_id": decl_id}, upd)
    return jsonify({"ok": True, "photos": [{"photo": p["photo"], "thumb": p["thumb"]} for p in photos]})


# ---------------------------------------------------------------------------
# Cockpit : page /declarations
# ---------------------------------------------------------------------------

def _role_required(role):
    def deco(f):
        from functools import wraps

        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _user():
    return getattr(request, "user_payload", None) or {}


def _user_name(u):
    name = ("%s %s" % (u.get("firstname") or "", u.get("lastname") or "")).strip()
    return name or u.get("email") or "?"


def _can_treat(u):
    """Suivre, classer, annoter, transformer en fiche : droit de groupe
    `can_convert_declaration` (ou admin). Voir la page = Pages accessibles."""
    try:
        from app import _user_can_treat_declaration
        return bool(_user_can_treat_declaration(u))
    except Exception:
        return bool(u.get("is_super_admin") or u.get("app_role") == "admin")


def _err(code, status=400):
    return jsonify({"ok": False, "error": code}), status


@declarations_bp.route("/declarations")
@_role_required("user")
def declarations_page():
    u = _user()
    try:
        from app import _user_can_create_fiche
        can_create = bool(_user_can_create_fiche(u))
    except Exception:
        can_create = False
    return render_template("declarations.html", user=u, can_treat=_can_treat(u), can_create=can_create,
                           user_is_admin=bool(u.get("is_super_admin") or u.get("app_role") == "admin"))


@declarations_bp.route("/api/declarations")
@_role_required("user")
def declarations_list():
    db = _db()
    _ensure_indexes(db)
    q = {}
    st = request.args.get("status") or ""
    if st in STATUSES:
        q["status"] = st
    elif st == "a_traiter":
        q["status"] = {"$in": ["nouvelle", "en_suivi"]}
    try:
        days = int(request.args.get("days") or 0)
    except ValueError:
        days = 0
    if days > 0:
        q["created_at"] = {"$gte": _now() - timedelta(days=days)}
    ev = (request.args.get("event") or "").strip()
    if ev:
        q["event"] = ev
        try:
            q["year"] = int(request.args.get("year"))
        except (TypeError, ValueError):
            pass
    search = (request.args.get("q") or "").strip()
    if search:
        rx = {"$regex": re.escape(search[:80]), "$options": "i"}
        q["$or"] = [{"text": rx}, {"ref": rx}, {"device_name": rx}, {"carroye": rx},
                    {"group_label": rx}, {"history.text": rx}]
    docs = list(db[COL].find(q, {"history": 0}).sort("created_at", DESCENDING).limit(LIST_MAX))
    fiches = _fiches_by_id(db, [d.get("fiche_id") for d in docs])
    # Compteurs des onglets : sur l'evenement filtre (tous sinon)
    evq = {k: q[k] for k in ("event", "year") if k in q}
    counts = {s: 0 for s in STATUSES}
    for row in db[COL].aggregate([{"$match": evq}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        if row["_id"] in counts:
            counts[row["_id"]] = row["n"]
    counts["reponses"] = db[COL].count_documents(dict(evq, cockpit_unread={"$gt": 0}))
    # Evenements presents (filtre de la page), les plus recents d'abord
    events = [{"event": r["_id"]["event"], "year": r["_id"]["year"], "count": r["n"]}
              for r in db[COL].aggregate([
                  {"$group": {"_id": {"event": "$event", "year": "$year"}, "n": {"$sum": 1},
                              "last": {"$max": "$created_at"}}},
                  {"$sort": {"last": -1}}]) if r["_id"].get("event")]
    return jsonify({
        "ok": True,
        "now": _iso(_now()),
        "can_treat": _can_treat(_user()),
        "counts": counts,
        "events": events,
        "declarations": [_pub(d, fiches.get(d.get("fiche_id"))) for d in docs],
    })


@declarations_bp.route("/api/declarations/<decl_id>")
@_role_required("user")
def declarations_detail(decl_id):
    db = _db()
    d = db[COL].find_one({"_id": decl_id})
    if not d:
        return _err("not_found", 404)
    fiche = _fiches_by_id(db, [d.get("fiche_id")]).get(d.get("fiche_id"))
    out = _pub(d, fiche, full=True)
    # Un operateur habilite qui ouvre le constat lit les reponses du declarant
    if d.get("cockpit_unread") and _can_treat(_user()):
        db[COL].update_one({"_id": decl_id}, {"$set": {"cockpit_unread": 0}})
    out["prefill"] = {
        "lat": out["lat"], "lon": out["lng"],
        "urgency": PRIORITY_TO_URGENCY.get(out["priority"]),
        "text": "%s - %s" % (out["ref"] or "Constat", out["text"]),
        # Source de la fiche : externe, l'appelant = le groupe declarant,
        # canal "application" (constat depose depuis la tablette)
        "source": "externe",
        "appelant": d.get("group_label") or d.get("device_name") or "Constat terrain",
        "canal": "application",
    }
    # Evenement de la fiche, choisi par l'operateur au moment de transformer :
    # l'epreuve active (si le probleme est traite pendant l'epreuve) ou SAISON
    # en cours (traite apres). Proposition = l'evenement du constat.
    import event_courant as EC
    choices = []
    try:
        for a in EC.active_events(_db()):
            choices.append({"event": a["event"], "year": a["year"], "kind": a.get("kind"),
                            "phase": a.get("phase")})
    except Exception as e:  # pragma: no cover - parametrages illisibles
        logger.warning("declarations: evenements actifs : %s", e)
    yr = _now().astimezone(timezone.utc).year
    if not any(EC.is_saison(c["event"]) for c in choices):
        choices.append({"event": EC.SAISON, "year": yr, "kind": "saison", "phase": None})
    out["event_choices"] = choices
    out["event_default"] = {"event": d.get("event"), "year": d.get("year")}
    return jsonify(dict(out, ok=True, can_treat=_can_treat(_user())))


def _set_status(db, decl_id, status, u, text, expect=None, extra=None):
    now = _now()
    flt = {"_id": decl_id}
    if expect:
        flt["status"] = {"$in": list(expect)}
    upd = {"$set": dict({"status": status, "status_at": now, "status_by": _user_name(u)}, **(extra or {})),
           "$push": {"history": _entry(_user_name(u), text, "cockpit", kind="status", ts=now)}}
    return db[COL].find_one_and_update(flt, upd, return_document=ReturnDocument.AFTER)


STATUS_TEXT = {
    # "Accuser reception" : le declarant voit "Vu par le PC" sur sa tablette
    "en_suivi": "Reception du constat accusee par le PC",
    "nouvelle": "Constat remis en attente",
}


@declarations_bp.route("/api/declarations/<decl_id>/status", methods=["POST"])
@_role_required("user")
def declarations_status(decl_id):
    """en_suivi / nouvelle, ou classee (motif obligatoire)."""
    u = _user()
    if not _can_treat(u):
        return _err("forbidden", 403)
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    db = _db()
    if status == "classee":
        reason = _clean_text(data.get("reason"), 500)
        if len(reason) < 3:
            return _err("motif_requis")
        d = _set_status(db, decl_id, "classee", u, "Classe sans suite : " + reason,
                        expect=("nouvelle", "en_suivi"), extra={"classed_reason": reason})
    elif status in STATUS_TEXT:
        cur = db[COL].find_one({"_id": decl_id}, {"status": 1}) or {}
        text = "Constat rouvert" if cur.get("status") == "classee" else STATUS_TEXT[status]
        d = _set_status(db, decl_id, status, u, text,
                        expect=("nouvelle", "en_suivi", "classee"), extra={"classed_reason": None})
    else:
        return _err("invalid_status")
    if not d:
        return _err("etat_incompatible", 409)
    return jsonify({"ok": True, "status": d["status"]})


@declarations_bp.route("/api/declarations/<decl_id>/note", methods=["POST"])
@_role_required("user")
def declarations_note(decl_id):
    """Note interne du PC (non montree au declarant)."""
    u = _user()
    if not _can_treat(u):
        return _err("forbidden", 403)
    text = _clean_text((request.get_json(silent=True) or {}).get("text"))
    if not text:
        return _err("empty_text")
    res = _db()[COL].update_one({"_id": decl_id}, {"$push": {"history": _entry(_user_name(u), text, "cockpit")}})
    if not res.matched_count:
        return _err("not_found", 404)
    return jsonify({"ok": True})


MESSAGE_MAX = 1000


def _device_oid(device_id):
    from bson import ObjectId
    try:
        return ObjectId(str(device_id))
    except Exception:
        return device_id


@declarations_bp.route("/api/declarations/<decl_id>/message", methods=["POST"])
@_role_required("user")
def declarations_message(decl_id):
    """Message du PC au declarant (demande de precision...) : visible dans le
    constat sur la tablette, notification push, reponse par le complement
    du constat. Possible tant que le constat n'est pas classe."""
    u = _user()
    if not _can_treat(u):
        return _err("forbidden", 403)
    text = _clean_text((request.get_json(silent=True) or {}).get("text"), MESSAGE_MAX)
    if not text:
        return _err("empty_text")
    db = _db()
    now = _now()
    d = db[COL].find_one_and_update(
        {"_id": decl_id, "status": {"$ne": "classee"}},
        {"$push": {"history": _entry(_user_name(u), text, "cockpit", kind="message", ts=now)},
         "$inc": {"field_unread": 1},
         "$set": {"last_message_at": now, "cockpit_unread": 0}},
        return_document=ReturnDocument.AFTER)
    if not d:
        if db[COL].find_one({"_id": decl_id}, {"_id": 1}):
            return _err("classee", 409)
        return _err("not_found", 404)
    # Ecrire au declarant vaut accuse de reception d'un constat nouveau
    if d.get("status") == "nouvelle":
        _set_status(db, decl_id, "en_suivi", u, STATUS_TEXT["en_suivi"], expect=("nouvelle",))
    pushed = 0
    try:
        pushed = F.send_push_to_device(
            db, _device_oid(d.get("device_id")),
            title="PC Organisation - constat " + (d.get("ref") or ""),
            body=text[:140], url="/field?constat=" + decl_id, tag="constat-" + decl_id)
    except Exception as e:  # push indisponible : le message reste visible a l'ouverture
        logger.warning("declarations: push %s : %s", decl_id, e)
    return jsonify({"ok": True, "pushed": pushed})


@declarations_bp.route("/api/declarations/<decl_id>/link", methods=["POST"])
@_role_required("user")
def declarations_link(decl_id):
    """Rattache la fiche que l'operateur vient de creer avec l'assistant
    (pre-rempli depuis le constat) : constat "transformee", et les photos et
    la reference du constat sont posees dans la chronologie de la fiche."""
    import pcorg_history as PH
    u = _user()
    if not _can_treat(u):
        return _err("forbidden", 403)
    fiche_id = str((request.get_json(silent=True) or {}).get("fiche_id") or "").strip()
    db = _db()
    fiche = db["pcorg"].find_one({"_id": fiche_id}, {"_id": 1, "operator_id_create": 1, "declaration_id": 1})
    if not fiche:
        return _err("fiche_introuvable", 404)
    # Seule une fiche creee par cet operateur, pas deja rattachee ailleurs
    if str(fiche.get("operator_id_create") or "").lower() != str(u.get("email") or "").lower():
        return _err("fiche_non_creee_par_vous", 403)
    if fiche.get("declaration_id") and fiche["declaration_id"] != decl_id:
        return _err("fiche_deja_rattachee", 409)
    now = _now()
    d = db[COL].find_one_and_update(
        {"_id": decl_id, "status": {"$in": ["nouvelle", "en_suivi"]}},
        {"$set": {"status": "transformee", "status_at": now, "status_by": _user_name(u),
                  "fiche_id": fiche_id, "converted_at": now, "converted_by": _user_name(u)},
         "$push": {"history": _entry(_user_name(u), "Transforme en fiche d'intervention", "cockpit",
                                     kind="status", ts=now)}},
        return_document=ReturnDocument.AFTER)
    if not d:
        return _err("etat_incompatible", 409)
    photos = [{"photo": p.get("photo"), "thumb": p.get("thumb")} for p in (d.get("photos") or [])]
    txt = "Constat terrain %s (%s, %s) : %s" % (
        d.get("ref"), d.get("device_name"), d.get("group_label") or "declarant", d.get("text") or "")
    entry = PH.make_entry(_user_name(u), txt, origin="cockpit", ts=now)
    if photos:
        entry["photos"] = photos
        entry["photo"] = photos[0]["photo"]
        entry["thumb"] = photos[0]["thumb"]
    PH.append_entry(db["pcorg"], fiche_id, entry,
                    set_fields={"declaration_id": decl_id, "declaration_ref": d.get("ref")},
                    inc_bounce=True)
    return jsonify({"ok": True, "fiche_id": fiche_id})


# ---------------------------------------------------------------------------
# Constat a envoyer (mail + resume IA) : PREPARE, envoi a finaliser
# ---------------------------------------------------------------------------

@declarations_bp.route("/api/declarations/<decl_id>/report/preview")
@_role_required("user")
def declarations_report_preview(decl_id):
    """Apercu HTML du constat tel qu'il partira par mail (sans resume IA
    tant que la generation n'est pas branchee)."""
    import declarations_report as DR
    db = _db()
    d = db[COL].find_one({"_id": decl_id})
    if not d:
        return _err("not_found", 404)
    fiche = db["pcorg"].find_one({"_id": d.get("fiche_id")}) if d.get("fiche_id") else None
    constat = DR.build_constat(d, fiche)
    return DR.render_email_html(constat, summary=None), 200, {"Content-Type": "text/html; charset=utf-8"}


@declarations_bp.route("/api/declarations/<decl_id>/report/send", methods=["POST"])
@_role_required("user")
def declarations_report_send(decl_id):
    """A finaliser : resume IA + envoi SMTP (declarations_report.send_constat)."""
    if not _can_treat(_user()):
        return _err("forbidden", 403)
    return _err("a_finaliser", 501)
