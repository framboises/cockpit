"""Page et API d'administration du module Formation (spec 9), reservees au role
admin ou a la page d'administration `formation` accordee a un groupe
(ADMIN_PAGE_REGISTRY d'app.py : /admin/formation et /api/formation/admin).

Onglets : Candidats (import, tableau, detail, amenagement, reinitialisation),
Campagne (gabarit, essai, envoi, etat), Resultats (statistiques, exports),
Reglages. Les candidats `fictif` sont exclus des statistiques et des exports.
Toute ecriture passe par le CSRF de Cockpit (en-tete X-CSRFToken).
"""
import csv
import io
import logging
import math
import re
import zipfile
from datetime import date, datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, Response, jsonify, render_template, request

from formation import store, tentative

formation_admin_bp = Blueprint("formation_admin", __name__)
logger = logging.getLogger(__name__)

_PARIS = ZoneInfo("Europe/Paris")
STATUTS = ("non_commence", "en_cours", "terminee", "interrompue")
PURGE_RAPPEL = date(2027, 1, 23)
EXPORT_COLONNES = (["forms_id", "nom", "prenom", "email", "statut", "debut", "duree_s"]
                   + store.EPREUVES + ["global", "amenagement", "reprises", "drapeaux"])
_ADRESSE = re.compile(r"[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+")


def _role_required(role):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from app import role_required
            return role_required(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _maintenant():
    return datetime.now(timezone.utc)


def _auteur():
    u = getattr(request, "user_payload", None) or {}
    return u.get("email") or "?"


def _json():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _utc(d):
    if isinstance(d, datetime) and d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def _serial(v):
    """Document MongoDB -> JSON (ObjectId en texte, dates ISO UTC)."""
    if isinstance(v, dict):
        return {str(k): _serial(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_serial(x) for x in v]
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return _utc(v).isoformat()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _paris_local(d):
    """'YYYY-MM-DDTHH:MM' en heure de Paris (champ datetime-local)."""
    return _utc(d).astimezone(_PARIS).strftime("%Y-%m-%dT%H:%M") if isinstance(d, datetime) else None


def _purge():
    return {"date": "2027-01-30", "rappel": _maintenant().astimezone(_PARIS).date() >= PURGE_RAPPEL}


def _oid(candidat_id):
    try:
        return ObjectId(candidat_id)
    except (InvalidId, TypeError):
        return None


# --- Page ---------------------------------------------------------------------

@formation_admin_bp.get("/admin/formation")
@_role_required("admin")
def page_admin():
    payload = getattr(request, "user_payload", None) or {}
    return render_template("formation/admin.html",
                           user_roles=payload.get("roles", []),
                           user_firstname=payload.get("firstname", ""),
                           user_lastname=payload.get("lastname", ""),
                           user_email=payload.get("email", ""),
                           app_role=payload.get("app_role", "user"))


@formation_admin_bp.get("/api/formation/admin/etat")
@_role_required("admin")
def api_etat():
    return jsonify({"ok": True, "campagne": store.CAMPAGNE, "purge": _purge()})


# --- Candidats ------------------------------------------------------------------

def _scores(t):
    out = {e: None for e in store.EPREUVES}
    for e in store.EPREUVES:
        ep = (t.get("epreuves") or {}).get(e) or {}
        if ep.get("etat") in ("terminee", "abandonnee") and isinstance(ep.get("score"), (int, float)):
            out[e] = int(ep["score"])
    return out


def _drapeaux(t):
    """['epreuve:drapeau', ...] dans l'ordre des epreuves."""
    out = []
    for e in store.EPREUVES:
        for d in ((t.get("epreuves") or {}).get(e) or {}).get("drapeaux") or []:
            x = "%s:%s" % (e, d)
            if x not in out:
                out.append(x)
    return out


def _arrondi(g):
    return None if g is None else round(g, 1)


def _lignes(db, fictifs=False):
    """Une ligne par candidat (tentative active seulement ; reinitialisees au detail)."""
    reg = store.reglages(db)
    filtre = {"campagne": store.CAMPAGNE}
    if not fictifs:
        filtre["fictif"] = {"$ne": True}
    cands = list(db["candidats"].find(filtre).sort([("nom", 1), ("prenom", 1)]))
    tents = {t["candidat_id"]: t for t in db["tentatives"].find(
        {"campagne": store.CAMPAGNE, "statut": {"$in": store.STATUTS_TENTATIVE_ACTIFS}})}
    envois = {}
    for e in db["envois"].find({"campagne": store.CAMPAGNE, "type": {"$in": ["invitation", "relance"]}}):
        d = _utc(e.get("envoye_le") or e.get("cree_le"))
        cur = envois.get(e["candidat_id"])
        if cur is None or (d is not None and (cur["date"] is None or d > cur["date"])):
            envois[e["candidat_id"]] = {"type": e["type"], "statut": e.get("statut"), "date": d}
    out = []
    for c in cands:
        cid = str(c["_id"])
        t = tents.get(cid)
        ligne = {"id": cid, "forms_id": c.get("forms_id"), "nom": c.get("nom") or "",
                 "prenom": c.get("prenom") or "", "email": c.get("email") or "",
                 "amenagement": c.get("amenagement") is True, "fictif": c.get("fictif") is True,
                 "statut": "non_commence", "debut": None, "duree_s": None, "coef": None,
                 "scores": {e: None for e in store.EPREUVES}, "global": None, "reprises": 0,
                 "drapeaux": [], "dernier_envoi": envois.get(cid)}
        if t is not None:
            debut, fin = _utc(t.get("debut")), _utc(t.get("fin"))
            ligne.update({
                "statut": t["statut"], "debut": debut, "coef": t.get("coef"),
                "duree_s": int((fin - debut).total_seconds()) if debut and fin else None,
                "scores": _scores(t), "reprises": int(t.get("reprises") or 0),
                "global": _arrondi(tentative.score_global(t, reg["ponderations"])),
                "drapeaux": _drapeaux(t)})
        out.append(ligne)
    return out


@formation_admin_bp.get("/api/formation/admin/candidats")
@_role_required("admin")
def api_candidats():
    statut = (request.args.get("statut") or "").strip()
    if statut and statut not in STATUTS:
        return jsonify({"ok": False, "error": "statut_inconnu"}), 400
    q = (request.args.get("q") or "").strip().lower()
    lignes = _lignes(store.get_db(), fictifs=request.args.get("fictifs") == "1")
    if statut:
        lignes = [l for l in lignes if l["statut"] == statut]
    if q:
        lignes = [l for l in lignes if q in " ".join(
            [l["nom"], l["prenom"], l["email"], str(l["forms_id"] or "")]).lower()]
    return jsonify({"ok": True, "candidats": _serial(lignes)})


def _corrige():
    """Reponses attendues de la banque, par epreuve : {id: {points|ok|ans|a}}.
    Reserve a l'administration (jamais envoye au candidat)."""
    from formation import banque
    return {e: banque.secrets_par_id(e) for e in store.EPREUVES}


def _tentative_admin(t, ponderations):
    d = {k: v for k, v in t.items() if k not in ("sid", "seed")}
    d["global"] = _arrondi(tentative.score_global(t, ponderations))
    return _serial(d)


@formation_admin_bp.get("/api/formation/admin/tentative/<candidat_id>")
@_role_required("admin")
def api_tentative(candidat_id):
    db = store.get_db()
    oid = _oid(candidat_id)
    c = db["candidats"].find_one({"_id": oid, "campagne": store.CAMPAGNE}) if oid else None
    if c is None:
        return jsonify({"ok": False, "error": "candidat_introuvable"}), 404
    reg = store.reglages(db)
    t = tentative.active(db, candidat_id)
    hist = db["tentatives"].find({"campagne": store.CAMPAGNE, "candidat_id": candidat_id,
                                  "statut": "reinitialisee"}).sort("debut", 1)
    journal = db["journal_admin"].find({"campagne": store.CAMPAGNE,
                                        "details.candidat_id": candidat_id}).sort("t", 1)
    envois = db["envois"].find({"campagne": store.CAMPAGNE, "candidat_id": candidat_id},
                               {"type": 1, "statut": 1, "cree_le": 1, "envoye_le": 1, "erreur": 1})
    cand = {k: c.get(k) for k in ("forms_id", "nom", "prenom", "email", "amenagement", "fictif",
                                  "forms_repondu_le", "importe_le")}
    cand["id"] = candidat_id
    return jsonify({
        "ok": True, "candidat": _serial(cand),
        "tentative": _tentative_admin(t, reg["ponderations"]) if t else None,
        "global": _arrondi(tentative.score_global(t, reg["ponderations"])) if t else None,
        "historique": [_tentative_admin(h, reg["ponderations"]) for h in hist],
        "journal": _serial(list(journal)), "envois": _serial(list(envois)),
        "corrige": _corrige(), "epreuves": store.EPREUVES})


@formation_admin_bp.post("/api/formation/admin/candidat/<candidat_id>/amenagement")
@_role_required("admin")
def api_amenagement(candidat_id):
    actif = _json().get("actif")
    if not isinstance(actif, bool):
        return jsonify({"ok": False, "error": "actif_invalide"}), 400
    db = store.get_db()
    oid = _oid(candidat_id)
    c = db["candidats"].find_one({"_id": oid, "campagne": store.CAMPAGNE}) if oid else None
    if c is None:
        return jsonify({"ok": False, "error": "candidat_introuvable"}), 404
    avant = c.get("amenagement") is True
    db["candidats"].update_one({"_id": oid}, {"$set": {"amenagement": actif}})
    store.journal(db, "amenagement", _auteur(), candidat_id=candidat_id, avant=avant, apres=actif)
    # Coefficient fige au clic Commencer : sans effet sur une tentative commencee
    return jsonify({"ok": True, "amenagement": actif,
                    "tentative_commencee": tentative.active(db, candidat_id) is not None})


@formation_admin_bp.post("/api/formation/admin/candidat/<candidat_id>/reinitialiser")
@_role_required("admin")
def api_reinitialiser(candidat_id):
    motif = _json().get("motif")
    if not isinstance(motif, str) or not motif.strip():
        return jsonify({"ok": False, "error": "motif_requis"}), 400
    db = store.get_db()
    oid = _oid(candidat_id)
    if oid is None or db["candidats"].find_one({"_id": oid, "campagne": store.CAMPAGNE}) is None:
        return jsonify({"ok": False, "error": "candidat_introuvable"}), 404
    try:
        tentative.reinitialiser(db, candidat_id, motif.strip()[:500], _auteur(), _maintenant())
    except ValueError:
        return jsonify({"ok": False, "error": "aucune_tentative"}), 404
    return jsonify({"ok": True})


# --- Resultats --------------------------------------------------------------------

@formation_admin_bp.get("/api/formation/admin/stats")
@_role_required("admin")
def api_stats():
    db = store.get_db()
    ids = {str(c["_id"]) for c in db["candidats"].find(
        {"campagne": store.CAMPAGNE, "fictif": {"$ne": True}}, {"_id": 1})}
    invites = {e["candidat_id"] for e in db["envois"].find(
        {"campagne": store.CAMPAGNE, "type": "invitation", "statut": "envoye"}, {"candidat_id": 1})}
    tents = [t for t in db["tentatives"].find(
        {"campagne": store.CAMPAGNE, "statut": {"$in": store.STATUTS_TENTATIVE_ACTIFS}})
        if t["candidat_id"] in ids]
    n = {s: sum(1 for t in tents if t["statut"] == s) for s in store.STATUTS_TENTATIVE_ACTIFS}
    durees = [(_utc(t["fin"]) - _utc(t["debut"])).total_seconds() for t in tents
              if t["statut"] == "terminee" and t.get("fin") and t.get("debut")]
    distribution = {e: [0] * 10 for e in store.EPREUVES}
    notes = {e: [] for e in store.EPREUVES}
    for t in tents:
        for e, s in _scores(t).items():
            if s is not None:
                distribution[e][min(9, max(0, s // 10))] += 1
                notes[e].append(s)
    pond = store.reglages(db)["ponderations"]
    globaux = [g for g in (tentative.score_global(t, pond) for t in tents if t["statut"] == "terminee")
               if g is not None]
    # Reperes de la fiche individuelle (vrais candidats seulement, fictifs exclus)
    moyennes = {e: _arrondi(sum(v) / len(v)) if v else None for e, v in notes.items()}
    moyennes["global"] = _arrondi(sum(globaux) / len(globaux)) if globaux else None
    return jsonify({"ok": True, "inscrits": len(ids), "invites": len(ids & invites),
                    "moyennes": moyennes,
                    "commences": len(tents), "termines": n["terminee"],
                    "interrompus": n["interrompue"], "en_cours": n["en_cours"],
                    "temps_moyen_s": int(round(sum(durees) / len(durees))) if durees else None,
                    "distribution": distribution})


def _ligne_export(l):
    debut = l["debut"].astimezone(_PARIS).strftime("%Y-%m-%d %H:%M") if l["debut"] else None
    return ([l["forms_id"], l["nom"], l["prenom"], l["email"], l["statut"], debut, l["duree_s"]]
            + [l["scores"][e] for e in store.EPREUVES]
            + [l["global"], "oui" if l["amenagement"] else "non", l["reprises"],
               ", ".join(l["drapeaux"])])


def _sans_formule(v):
    """Texte commencant par = + - @ : prefixe d'une apostrophe (pas de formule dans Excel)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _cellule_csv(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(v).replace(".", ",")
    return _sans_formule(v)


def _nom_export(ext):
    return "formation-%s-%s.%s" % (store.CAMPAGNE, _maintenant().astimezone(_PARIS).strftime("%Y%m%d-%H%M"), ext)


@formation_admin_bp.get("/api/formation/admin/export.csv")
@_role_required("admin")
def api_export_csv():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_COLONNES)
    for l in _lignes(store.get_db()):
        w.writerow([_cellule_csv(v) for v in _ligne_export(l)])
    corps = "\ufeff" + buf.getvalue()
    return Response(corps.encode("utf-8"), mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="%s"' % _nom_export("csv"),
        "Cache-Control": "no-store"})


@formation_admin_bp.get("/api/formation/admin/export.xlsx")
@_role_required("admin")
def api_export_xlsx():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Candidats"
    ws.append(EXPORT_COLONNES)
    for l in _lignes(store.get_db()):
        ws.append(_ligne_export(l))
        for cell in ws[ws.max_row]:
            if cell.data_type == "f":  # texte commencant par = : jamais une formule
                cell.data_type = "s"
    ws.freeze_panes = "B2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": 'attachment; filename="%s"' % _nom_export("xlsx"),
        "Cache-Control": "no-store"})


# --- Reglages ---------------------------------------------------------------------

_CHAMPS_REGLAGES = ("ouverture", "fermeture", "coef_amenagement", "ponderations", "reply_to",
                    "contact_rgpd", "expediteur_nom", "debit_envoi_s")


def _date_reglage(v):
    """Texte ISO -> datetime aware UTC ; sans fuseau : heure de Paris."""
    if not isinstance(v, str) or not v.strip():
        raise ValueError
    d = datetime.fromisoformat(v.strip())
    if d.tzinfo is None:
        d = d.replace(tzinfo=_PARIS)
    return d.astimezone(timezone.utc)


def _nombre(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _valider_reglages(data, actuels):
    """Champs valides a ecrire, ou (None, code d'erreur)."""
    inconnus = [k for k in data if k not in _CHAMPS_REGLAGES]
    if inconnus:
        return None, "champ_inconnu"
    out = {}
    for k in ("ouverture", "fermeture"):
        if k in data:
            try:
                out[k] = _date_reglage(data[k])
            except (ValueError, TypeError):
                return None, k + "_invalide"
    ouv = out.get("ouverture", actuels["ouverture"])
    fer = out.get("fermeture", actuels["fermeture"])
    if ("ouverture" in out or "fermeture" in out) and not fer > ouv:
        return None, "fermeture_avant_ouverture"
    if "coef_amenagement" in data:
        v = data["coef_amenagement"]
        if not _nombre(v) or not 1 <= v <= 3:
            return None, "coef_invalide"
        out["coef_amenagement"] = float(v)
    if "ponderations" in data:
        p = data["ponderations"]
        if (not isinstance(p, dict) or set(p) != set(store.EPREUVES)
                or not all(_nombre(x) and x >= 0 for x in p.values()) or not sum(p.values()) > 0):
            return None, "ponderations_invalides"
        out["ponderations"] = {e: p[e] for e in store.EPREUVES}
    for k in ("reply_to", "contact_rgpd"):
        if k in data:
            v = data[k]
            if not isinstance(v, str) or (v.strip() and not _ADRESSE.fullmatch(v.strip())):
                return None, k + "_invalide"
            out[k] = v.strip()
    if "expediteur_nom" in data:
        v = data["expediteur_nom"]
        if not isinstance(v, str) or len(v) > 100 or re.search(r"[\r\n]", v):
            return None, "expediteur_invalide"
        out["expediteur_nom"] = v.strip()
    if "debit_envoi_s" in data:
        v = data["debit_envoi_s"]
        if not _nombre(v) or not 0.5 <= v <= 60:
            return None, "debit_invalide"
        out["debit_envoi_s"] = float(v)
    return out, None


def _reglages_json(reg):
    d = {k: reg.get(k) for k in _CHAMPS_REGLAGES + ("conservation_jusqu_au", "campagne_arretee")}
    d["ouverture_paris"] = _paris_local(reg["ouverture"])
    d["fermeture_paris"] = _paris_local(reg["fermeture"])
    return _serial(d)


@formation_admin_bp.get("/api/formation/admin/reglages")
@_role_required("admin")
def api_reglages():
    reg = store.reglages(store.get_db())
    return jsonify({"ok": True, "reglages": _reglages_json(reg), "gabarit": reg["gabarit"],
                    "purge": _purge()})


@formation_admin_bp.put("/api/formation/admin/reglages")
@_role_required("admin")
def api_reglages_put():
    db = store.get_db()
    champs, err = _valider_reglages(_json(), store.reglages(db))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    reg = store.maj_reglages(db, champs, _auteur())
    return jsonify({"ok": True, "reglages": _reglages_json(reg)})


# --- Import -------------------------------------------------------------------------

@formation_admin_bp.post("/api/formation/admin/import")
@_role_required("admin")
def api_import():
    from openpyxl.utils.exceptions import InvalidFileException
    from formation import importer
    f = request.files.get("fichier")
    if f is None:
        return jsonify({"ok": False, "error": "fichier_manquant"}), 400
    try:
        rapport = importer.importer(store.get_db(), f.read(), _auteur())
    except (InvalidFileException, zipfile.BadZipFile, ValueError) as exc:
        return jsonify({"ok": False, "error": "fichier_illisible", "detail": str(exc)[:200]}), 400
    except Exception:
        logger.exception("formation import : erreur inattendue")
        return jsonify({"ok": False, "error": "erreur_interne"}), 500
    return jsonify({"ok": True, "rapport": _serial(rapport)})


# --- Campagne d'e-mails -----------------------------------------------------


@formation_admin_bp.post("/api/formation/admin/campagne/essai")
@_role_required("admin")
def api_campagne_essai():
    from formation import mailer, store
    from pcorg_summary_mail import SmtpError
    try:
        mailer.envoyer_essai(store.get_db(), _json().get("adresse"), _auteur())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except SmtpError as exc:
        return jsonify({"ok": False, "error": "smtp", "detail": str(exc)[:200]}), 502
    return jsonify({"ok": True})


@formation_admin_bp.post("/api/formation/admin/campagne/compter")
@_role_required("admin")
def api_campagne_compter():
    from formation import mailer, store
    try:
        n = mailer.compter(store.get_db(), _json().get("cible"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "n": n})


@formation_admin_bp.post("/api/formation/admin/campagne/lancer")
@_role_required("admin")
def api_campagne_lancer():
    from datetime import datetime, timezone
    from formation import mailer, store
    data = _json()
    db = store.get_db()
    cn = data.get("confirmation_n")
    if not isinstance(cn, int) or isinstance(cn, bool):
        return jsonify({"ok": False, "error": "confirmation_requise"}), 400
    try:
        res = mailer.lancer(db, data.get("cible"), data.get("confirmation_n"), _auteur(),
                            datetime.now(timezone.utc))
    except ValueError as exc:
        if exc.args and exc.args[0] == "confirmation_differente":
            return jsonify({"ok": False, "error": "confirmation_differente", "n": exc.args[1]}), 409
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "planifies": res["planifies"], "invalides": res["invalides"],
                    "actif": mailer.fil.etat()["actif"], "etat": mailer.fil.etat()})


@formation_admin_bp.post("/api/formation/admin/campagne/arreter")
@_role_required("admin")
def api_campagne_arreter():
    from formation import mailer, store
    mailer.arreter(store.get_db(), _auteur())
    return jsonify({"ok": True, "etat": mailer.fil.etat()})


@formation_admin_bp.post("/api/formation/admin/campagne/reprendre")
@_role_required("admin")
def api_campagne_reprendre():
    from formation import mailer, store
    mailer.reprendre(store.get_db(), _auteur())
    etat = mailer.fil.etat()
    return jsonify({"ok": True, "actif": etat["actif"], "etat": etat})


@formation_admin_bp.post("/api/formation/admin/campagne/renvoyer-echecs")
@_role_required("admin")
def api_campagne_renvoyer_echecs():
    from formation import mailer, store
    db = store.get_db()
    n = mailer.renvoyer_echecs(db, _auteur())
    if n:
        mailer.fil.demarrer(db)
    return jsonify({"ok": True, "n": n, "etat": mailer.fil.etat()})


@formation_admin_bp.get("/api/formation/admin/campagne/etat")
@_role_required("admin")
def api_campagne_etat():
    from formation import mailer
    return jsonify({"ok": True, "etat": mailer.fil.etat()})


@formation_admin_bp.put("/api/formation/admin/gabarit")
@_role_required("admin")
def api_gabarit():
    from formation import store
    data = _json()
    objet, corps = data.get("objet"), data.get("corps")
    if not isinstance(objet, str) or not isinstance(corps, str) or not objet.strip() or not corps.strip():
        return jsonify({"ok": False, "error": "gabarit_invalide"}), 400
    reg = store.maj_reglages(store.get_db(), {"gabarit": {"objet": objet, "corps": corps}}, _auteur())
    return jsonify({"ok": True, "gabarit": reg["gabarit"]})


@formation_admin_bp.post("/api/formation/admin/gabarit/apercu")
@_role_required("admin")
def api_gabarit_apercu():
    """Rendu du gabarit saisi (non enregistre) pour un candidat d'exemple."""
    from formation import mailer
    data = _json()
    objet, corps = data.get("objet"), data.get("corps")
    if not isinstance(objet, str) or not isinstance(corps, str):
        return jsonify({"ok": False, "error": "gabarit_invalide"}), 400
    o, texte, html = mailer.rendre({"objet": objet, "corps": corps}, {"prenom": "Camille"},
                                   store.reglages(store.get_db()))
    return jsonify({"ok": True, "objet": o, "texte": texte, "html": html})
