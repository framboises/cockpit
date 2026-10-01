"""musee_api.py - Bloc Musee de l'accueil + configuration (onglet Musee de /live-controle).

  GET  /api/musee/state      (user + bloc widget-musee) visiteurs du jour, statut,
                             horaires du jour, courbe horaire, repartition par
                             checkpoint, comparaisons S-1 et N-1.
  GET  /api/musee/config     (admin) document de configuration + etat de la collecte.
  PUT  /api/musee/config     (admin, CSRF) validation stricte puis enregistrement.
  GET  /api/musee/structure  (admin) Areas / portes / checkpoints Handshake
                             (?refresh=1 : ignore le cache).
  POST /api/musee/test       (admin, CSRF) dry-run du collecteur pour les valeurs
                             du formulaire (lecture seule, rien n'est enregistre).

Ne lit que musee_*, cockpit_settings.musee et les archives
hsh_archive_structure_* (relations parent / enfant) ; la borne n'est
interrogee qu'en lecture (musee_borne.py, connexion propre). Independant du
live-controle : ni data_access, ni hsh_structure, ni live_controle_actif.
"""

import datetime as _dt
import logging
import threading
import time
from functools import wraps

from flask import Blueprint, jsonify, request

import musee as M

logger = logging.getLogger(__name__)

musee_bp = Blueprint("musee", __name__)

BLOCK_ID = "widget-musee"
STRUCTURE_TTL_S = 3600
ARCHIVE_PREFIX = "hsh_archive_structure_"
PASSAGES_JOURS = 60

_structure_cache = {"ts": 0.0, "data": None}
_structure_lock = threading.Lock()


def _db():
    from app import db
    return db


def _role_required(role):
    """role_required d'app.py, resolu a l'appel (cf. momentus_api.py)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _block_required(block_id):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import block_required
            return block_required(block_id)(f)(*args, **kwargs)
        return wrapper
    return deco


def _jsonable(v):
    if isinstance(v, _dt.datetime):
        return M.as_utc(v).isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


# ---------------------------------------------------------------------------
# Accueil
# ---------------------------------------------------------------------------

@musee_bp.route("/api/musee/state", methods=["GET"])
@_role_required("user")
@_block_required(BLOCK_ID)
def api_state():
    try:
        return jsonify(_jsonable(M.build_state(_db())))
    except Exception as e:
        logger.exception("musee state : %s", e)
        return jsonify({"ok": False, "error": "etat_indisponible"}), 500


# ---------------------------------------------------------------------------
# Structure Handshake
# ---------------------------------------------------------------------------

def _archives(db):
    """[(edition, docs)] des hsh_archive_structure_*, de la plus ancienne a la
    plus recente (date de derniere mise a jour)."""
    out = []
    proj = {"location_id": 1, "location_type": 1, "location_name": 1, "parent_area": 1,
            "parent_gate": 1, "enfants": 1, "evenement": 1, "derniere_maj": 1}
    for name in db.list_collection_names():
        if not name.startswith(ARCHIVE_PREFIX):
            continue
        docs = list(db[name].find({"location_type": {"$in": list(M.LOC_TYPES)}}, proj))
        if not docs:
            continue
        last = max((M.as_utc(d["derniere_maj"]) for d in docs if d.get("derniere_maj")), default=None)
        ed = next((d.get("evenement") for d in docs if d.get("evenement")), None)
        label = "%s %s" % (ed, name[-4:]) if ed and name[-4:].isdigit() else name[len(ARCHIVE_PREFIX):]
        out.append((last, name, label, docs))
    out.sort(key=lambda x: (x[0] is None, x[0] or 0))
    return [(label, docs) for _l, _n, label, docs in out], (out[-1][1] if out else None)


def _passages_relations(db):
    since = M.shift_date(M.now_utc().astimezone(M.TZ_PARIS).strftime("%Y-%m-%d"), days=-PASSAGES_JOURS)
    pipe = [
        {"$match": {"date": {"$gte": since}, "area_id": {"$exists": True}}},
        {"$group": {"_id": {"c": "$checkpoint_id", "g": "$gate_id", "a": "$area_id"},
                    "nom": {"$last": "$checkpoint_nom"}}},
    ]
    return [{"checkpoint_id": r["_id"].get("c"), "gate_id": r["_id"].get("g"),
             "area_id": r["_id"].get("a"), "checkpoint_nom": r.get("nom")}
            for r in db[M.COL_PASSAGES].aggregate(pipe)]


def _build_structure(db, avec_borne=True):
    inventaire, borne_err = None, None
    if avec_borne:
        try:
            import musee_borne as B
            inventaire = B.lire_inventaire()
        except Exception as exc:
            borne_err = str(exc)[:200]
            logger.warning("musee structure : borne injoignable (%s)", exc)
    archives, derniere = _archives(db)
    try:
        passages = _passages_relations(db)
    except Exception:
        passages = []
    data = M.build_structure(inventaire, archives, passages)
    data["source"] = "borne" if inventaire is not None else ("archive" if derniere else "aucune")
    data["source_detail"] = (
        "inventaire de la borne (%d compteurs)" % len(inventaire) if inventaire is not None
        else ("borne injoignable%s : derniere archive %s"
              % (" (" + borne_err + ")" if borne_err else "", derniere) if derniere
              else "ni borne ni archive"))
    data["editions"] = [ed for ed, _d in archives]
    data["genere_le"] = M.now_utc().isoformat()
    return data


def _structure(db, refresh=False):
    with _structure_lock:
        c = _structure_cache
        if not refresh and c["data"] is not None and time.time() - c["ts"] < STRUCTURE_TTL_S:
            return c["data"]
        data = _build_structure(db)
        c["ts"], c["data"] = time.time(), data
        return data


def _known(db, cfg):
    """Locations connues pour valider un enregistrement : structure en cache
    (sinon archives seules, sans attendre la borne) + config en place."""
    c = _structure_cache
    data = c["data"] if c["data"] is not None else _build_structure(db, avec_borne=False)
    known = dict(data.get("known") or {})
    if cfg:
        for loc in cfg["locations"]:
            known.setdefault(loc["id"], {"type": loc["type"], "nom": loc["nom"]})
        for cp in cfg["checkpoints"]:
            known.setdefault(cp["id"], {"type": "Checkpoint", "nom": cp["nom"]})
    return known


@musee_bp.route("/api/musee/structure", methods=["GET"])
@_role_required("admin")
def api_structure():
    try:
        db = _db()
        data = _structure(db, refresh=request.args.get("refresh") == "1")
        out = {k: v for k, v in data.items() if k != "known"}
        out["ok"] = True
        return jsonify(out)
    except Exception as e:
        logger.exception("musee structure : %s", e)
        return jsonify({"ok": False, "error": "structure_indisponible"}), 500


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _form_doc(doc):
    """Document stocke -> valeurs du formulaire (schema 2, tolerant)."""
    if not doc:
        return None
    d = M.migrate_legacy(doc)
    cfg = M.normalize_config(d)
    return {
        "enabled": cfg["enabled"],
        "horaires": cfg["horaires"],
        "area": ({"id": cfg["area_id"], "nom": cfg["area_nom"]} if cfg["area_id"] else None),
        "gates": cfg["gates"],
        "checkpoints": cfg["checkpoints"],
        "transactions": cfg["transactions"],
        "releve_perime_min": cfg["releve_perime_min"],
        "statuts_passage": cfg["statuts_passage"],
    }


@musee_bp.route("/api/musee/config", methods=["GET"])
@_role_required("admin")
def api_get_config():
    try:
        db = _db()
        doc = db["cockpit_settings"].find_one({"_id": M.SETTINGS_ID})
        cfg = M.normalize_config(doc)
        now_p = M.now_utc().astimezone(M.TZ_PARIS)
        today = now_p.strftime("%Y-%m-%d")
        return jsonify(_jsonable({
            "ok": True,
            "existe": doc is not None,
            "configure": bool(cfg and cfg["configure"]),
            "config": _form_doc(doc),
            "maj": (doc or {}).get("maj"),
            "aujourdhui": ({"date": today, "horaires": M.horaires_du_jour(cfg, today),
                            "statut": M.statut_ouverture(cfg, now_p)[1]}
                           if cfg and cfg["configure"] else None),
            "etat": M.etat_collecte(db),
            "defauts": {"releve_perime_min": M.DEFAUT_RELEVE_PERIME_MIN,
                        "statuts_passage": list(M.DEFAUT_STATUTS_PASSAGE)},
        }))
    except Exception as e:
        logger.exception("musee config : %s", e)
        return jsonify({"ok": False, "error": "config_indisponible"}), 500


@musee_bp.route("/api/musee/config", methods=["PUT"])
@_role_required("admin")
def api_put_config():
    db = _db()
    payload = request.get_json(silent=True)
    current = M.get_config(db)
    doc, errs = M.validate_config(payload, known=_known(db, current))
    if errs:
        return jsonify({"ok": False, "error": "config_invalide", "erreurs": errs}), 400
    user = getattr(request, "user_payload", {}) or {}
    doc["maj"] = {"ts": M.now_utc(), "par": user.get("email") or ""}
    db["cockpit_settings"].update_one(
        {"_id": M.SETTINGS_ID},
        {"$set": doc,
         "$unset": {k: "" for k in ("ouverture", "fermeture", "jours_fermes", "area_id", "locations")}},
        upsert=True)
    logger.info("musee config enregistree par %s : Area %s, %d checkpoint(s)",
                doc["maj"]["par"], (doc.get("area") or {}).get("id"), len(doc["checkpoints"]))
    return jsonify({"ok": True})


@musee_bp.route("/api/musee/test", methods=["POST"])
@_role_required("admin")
def api_test():
    db = _db()
    payload = request.get_json(silent=True)
    doc, errs = M.validate_config(payload, known=None)
    if errs:
        return jsonify({"ok": False, "error": "config_invalide", "erreurs": errs}), 400
    cfg = M.normalize_config(doc)
    try:
        import musee_borne as B
        return jsonify(_jsonable(B.tester(cfg, db)))
    except Exception as e:
        logger.exception("musee test : %s", e)
        return jsonify({"ok": False, "error": "test_impossible"}), 500
