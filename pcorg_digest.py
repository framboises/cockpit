# pcorg_digest.py -- fiches de main courante structurees par le modele local (Alfred)
#
# Lance HORS du processus Cockpit (regle : aucune tache de fond dans Cockpit) :
# a la main ou par la tache planifiee de nuit (pcorg_digest.bat). Pour chaque
# fiche pcorg, le modele local d'Alfred (Ollama, Qwen3 30B-A3B) rend une
# version structuree : gravite 1-5 evaluee sur le deroule, type, victimes,
# moyens engages, issue, resume sans nom.
#
# Pourquoi : la main courante n'a aucune gravite exploitable (fiches SQL :
# niveau_urgence vide ou IMP, severity 0). Base des propositions de
# patrouilles (docs/claude/patrouilles.md, 6.0) et controlee par sondage sur
# /admin/gravite-fiches (gravite_fiches.py).
#
# Stockage : collection pcorg_digests, _id = id de la fiche. JAMAIS d'ecriture
# dans pcorg (reecrite par la synchro SQL). Une fiche controlee par un admin
# (review) n'est jamais reevaluee.
#
# Usage :
#   python pcorg_digest.py                      tout ce qui reste
#   python pcorg_digest.py --max-batches 1      un lot de 50 puis arret
#   python pcorg_digest.py --end-at 06:00       arret a 6 h
#   python pcorg_digest.py --only-gps           fiches avec position seulement
#   python pcorg_digest.py --poll               mode tache planifiee (chaque minute) :
#       1. une demande deposee depuis la page (lots de 50 / 100) -> traitee ;
#       2. sinon, si le traitement de nuit est active dans la page et qu'on
#          est dans la fenetre (--night 01:00-06:00) -> jusqu'a la fin de fenetre ;
#       3. sinon (ou traitement deja en cours) -> sortie immediate.
import argparse
import hashlib
import json
import logging
import os
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

PARIS = ZoneInfo("Europe/Paris")
COLL = "pcorg_digests"
LOCK_ID = "pcorg_digest_lock"           # cockpit_settings : etat du traitement en cours
REQUEST_ID = "pcorg_digest_request"     # cockpit_settings : lots demandes depuis la page
SETTINGS_ID = "pcorg_digest_settings"   # cockpit_settings : traitement automatique de nuit
BATCH_SIZES = (50, 100)
MAX_REQUEST_BATCHES = 50
LOCK_LEASE_S = 300                      # bail renouvele a chaque fiche
MAX_ATTEMPTS = 3                        # une fiche en echec est abandonnee apres 3 essais
BATCH_SIZE = 50
SOURCE_MAX_CHARS = 6000
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://srv-safe-docker.aco.local:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "alfred")
OLLAMA_TIMEOUT = int(os.getenv("PCORG_DIGEST_TIMEOUT", "180"))

# Changer le texte du prompt = incrementer PROMPT_VERSION : les fiches non
# controlees sont alors reevaluees.
PROMPT_VERSION = "v1"
PROMPT = """Tu analyses une fiche de main courante du PC Organisation du circuit des 24 Heures du Mans
(securite, secours, technique, flux, information) pendant un evenement.

Evalue la GRAVITE REELLE d'apres le deroule, pas d'apres le niveau d'urgence saisi (souvent absent ou faux) :
1 = information, logistique ou consigne, sans incident (ouverture, pose de materiel, appel informatif)
2 = gene ou anomalie sans risque pour les personnes (panne, congestion, objet trouve, demande de service)
3 = incident demandant une intervention, risque modere (malaise pris en charge sans gravite, conflit verbal,
    intrusion sans suite, chute d'arbre sans victime, personne egaree)
4 = atteinte aux personnes ou aux biens, ou risque serieux (bagarre, blesse, vol, degradation, conduite
    dangereuse, feu maitrise, menaces)
5 = danger vital ou crise (arret cardiaque, blesse grave, incendie important, mouvement de foule, evacuation)

Reponds UNIQUEMENT par un objet JSON :
{"gravite": 1 a 5, "justification": "pourquoi ce niveau, 20 mots maximum",
 "type": "nature de l'incident en quelques mots", "victimes": nombre (0 si aucune ou non precise),
 "moyens_engages": "...", "issue": "...",
 "resume": "deux phrases maximum, sans nom de personne, telephone ni plaque"}
N'invente rien : ce qui n'est pas ecrit vaut "non precise".

Fiche :
"""

log = logging.getLogger("pcorg_digest")


def _db():
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    name = "titan" if env in {"prod", "production"} else "titan_dev"
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=8000)
    return client[name]


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Source envoyee au modele (jamais l'appelant ni son telephone)
# ---------------------------------------------------------------------------
SOURCE_PROJECTION = {"text": 1, "comment": 1, "category": 1, "niveau_urgence": 1,
                     "event": 1, "year": 1, "ts": 1, "date_local": 1, "time_local": 1,
                     "area": 1, "gps": 1}


def source_text(doc):
    area = doc.get("area")
    area = area.get("name") if isinstance(area, dict) else area
    lines = [
        "Evenement : %s %s" % (doc.get("event") or "", doc.get("year") or ""),
        "Date : %s %s" % (doc.get("date_local") or "", doc.get("time_local") or ""),
        "Categorie : %s" % (doc.get("category") or "non precisee"),
        "Urgence saisie : %s" % (doc.get("niveau_urgence") or "aucune"),
    ]
    if area:
        lines.append("Zone : %s" % area)
    lines.append("Titre : %s" % (doc.get("text") or "").strip())
    lines.append("Deroule :\n%s" % (doc.get("comment") or "").strip())
    return "\n".join(lines)[:SOURCE_MAX_CHARS]


def src_hash(doc):
    raw = "\x1f".join(str(doc.get(k) or "") for k in ("text", "comment", "category", "niveau_urgence"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Appel du modele et controle de la reponse
# ---------------------------------------------------------------------------
_TEXT_FIELDS = ("justification", "type", "moyens_engages", "issue", "resume")


def parse_reply(raw):
    """Reponse JSON du modele -> dict controle. ValueError si inexploitable."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("reponse non objet")
    try:
        grav = int(data.get("gravite"))
    except (TypeError, ValueError):
        raise ValueError("gravite absente")
    if not 1 <= grav <= 5:
        raise ValueError("gravite hors 1-5 : %s" % grav)
    try:
        victimes = max(0, int(data.get("victimes") or 0))
    except (TypeError, ValueError):
        victimes = 0
    out = {"gravite": grav, "victimes": victimes}
    for k in _TEXT_FIELDS:
        out[k] = re.sub(r"\s+", " ", str(data.get(k) or "non precise")).strip()[:600]
    return out


def analyze(doc, session=None):
    """(resultat, meta) pour une fiche. Leve en cas d'echec reseau / reponse."""
    s = session or requests
    t0 = time.time()
    r = s.post(OLLAMA_URL + "/api/generate", timeout=OLLAMA_TIMEOUT, json={
        "model": OLLAMA_MODEL, "prompt": PROMPT + source_text(doc), "stream": False,
        "format": "json", "options": {"temperature": 0}, "keep_alive": "30m"})
    r.raise_for_status()
    body = r.json()
    res = parse_reply(body.get("response") or "")
    return res, {"ms": int((time.time() - t0) * 1000), "tokens_out": body.get("eval_count"),
                 "tokens_in": body.get("prompt_eval_count")}


# ---------------------------------------------------------------------------
# Selection des fiches a traiter
# ---------------------------------------------------------------------------
def _done_map(db):
    """{fiche_id: etat du digest} pour decider qui est a (re)traiter."""
    out = {}
    for d in db[COLL].find({}, {"src_hash": 1, "prompt_version": 1, "error": 1,
                                "attempts": 1, "review": 1}):
        out[d["_id"]] = d
    return out


def needs_work(doc, digest):
    if digest is None:
        return True
    if digest.get("review"):
        return False                       # controlee par un admin : figee
    same = digest.get("src_hash") == src_hash(doc) and digest.get("prompt_version") == PROMPT_VERSION
    if not same:
        return True
    if digest.get("error"):
        return (digest.get("attempts") or 0) < MAX_ATTEMPTS
    return False


def todo_ids(db, only_gps=False, event=None):
    """Identifiants des fiches a traiter : avec position d'abord, puis les
    autres ; recentes d'abord. Liste etablie au depart (un curseur garde
    ouvert des heures, a 2-4 s par fiche, expirerait cote serveur)."""
    done = _done_map(db)
    base = {"event": event} if event else {}
    passes = [dict(base, gps={"$ne": None})]
    if not only_gps:
        passes.append(dict(base, gps=None))
    out = []
    for q in passes:
        for doc in db["pcorg"].find(q, SOURCE_PROJECTION).sort("ts", -1):
            if needs_work(doc, done.get(doc["_id"])):
                out.append(doc["_id"])
    return out


def iter_todo(db, only_gps=False, event=None):
    """Fiches a traiter, relues une a une au moment de leur traitement."""
    for fid in todo_ids(db, only_gps, event):
        doc = db["pcorg"].find_one({"_id": fid}, SOURCE_PROJECTION)
        if doc is not None and needs_work(doc, db[COLL].find_one({"_id": fid})):
            yield doc


def count_todo(db, only_gps=False, event=None):
    return len(todo_ids(db, only_gps, event))


# ---------------------------------------------------------------------------
# Verrou / etat du traitement (lu par la page de suivi)
# ---------------------------------------------------------------------------
def acquire_lock(db, info):
    now = _now()
    col = db["cockpit_settings"]
    try:
        doc = col.find_one_and_update(
            {"_id": LOCK_ID, "$or": [{"until": {"$lt": now}}, {"until": None}]},
            {"$set": dict(info, until=now + timedelta(seconds=LOCK_LEASE_S), running=True,
                          stop_requested=False, started_at=now, last_at=now)},
            upsert=True, return_document=True)
        return doc is not None
    except DuplicateKeyError:
        return False


def heartbeat(db, fields):
    """Renouvelle le bail et publie l'avancement ; rend True si un arret est demande."""
    now = _now()
    doc = db["cockpit_settings"].find_one_and_update(
        {"_id": LOCK_ID},
        {"$set": dict(fields, until=now + timedelta(seconds=LOCK_LEASE_S), last_at=now)},
        return_document=True)
    return bool(doc and doc.get("stop_requested"))


def release_lock(db, fields):
    db["cockpit_settings"].update_one(
        {"_id": LOCK_ID},
        {"$set": dict(fields, running=False, until=_now(), ended_at=_now())})


# ---------------------------------------------------------------------------
# Traitement
# ---------------------------------------------------------------------------
def _end_deadline(end_at, now_local):
    """Echeance locale pour --end-at HH:MM (le lendemain si deja passee)."""
    h, m = (int(x) for x in end_at.split(":"))
    dl = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if dl <= now_local:
        dl += timedelta(days=1)
    return dl


def run(db, batch_size=BATCH_SIZE, max_batches=None, limit=None, end_at=None,
        only_gps=False, event=None, session=None, requested_by=None):
    started = _now()
    run_id = started.astimezone(PARIS).strftime("%Y%m%d-%H%M%S")
    deadline = _end_deadline(end_at, started.astimezone(PARIS)) if end_at else None
    todo_total = count_todo(db, only_gps, event)
    # Objectif de CE traitement (barre de progression de la page)
    target = todo_total
    if max_batches is not None:
        target = min(target, max_batches * batch_size)
    if limit is not None:
        target = min(target, limit)
    info = {"run_id": run_id, "host": socket.gethostname(), "pid": os.getpid(),
            "model": OLLAMA_MODEL, "prompt_version": PROMPT_VERSION, "batch_size": batch_size,
            "todo_at_start": todo_total, "target": target, "max_batches": max_batches,
            "end_at": end_at, "requested_by": requested_by,
            "done": 0, "errors": 0, "batch": None, "stop_reason": None}
    if not acquire_lock(db, info):
        log.warning("Un traitement est deja en cours (verrou %s) : arret.", LOCK_ID)
        return {"locked": True}
    log.info("Debut %s : %d fiches a traiter, lots de %d, modele %s, prompt %s",
             run_id, todo_total, batch_size, OLLAMA_MODEL, PROMPT_VERSION)
    done = errors = 0
    batch_no, in_batch = 1, 0
    reason = "termine"
    sess = session or requests.Session()
    try:
        for doc in iter_todo(db, only_gps, event):
            if limit is not None and done + errors >= limit:
                reason = "limite atteinte"
                break
            if deadline and datetime.now(PARIS) >= deadline:
                reason = "heure de fin atteinte"
                break
            if in_batch >= batch_size:
                log.info("Lot %s-%03d termine", run_id, batch_no)
                if max_batches is not None and batch_no >= max_batches:
                    reason = "nombre de lots atteint"
                    break
                batch_no, in_batch = batch_no + 1, 0
            batch = "%s-%03d" % (run_id, batch_no)
            base = {"event": doc.get("event"), "year": doc.get("year"), "ts": doc.get("ts"),
                    "category": doc.get("category"), "has_gps": doc.get("gps") is not None,
                    "src_hash": src_hash(doc), "model": OLLAMA_MODEL,
                    "prompt_version": PROMPT_VERSION, "batch": batch, "at": _now()}
            try:
                res, meta = analyze(doc, sess)
                db[COLL].update_one({"_id": doc["_id"]}, {
                    "$set": dict(base, **res, meta=meta, error=None, attempts=0)}, upsert=True)
                done += 1
            except requests.ConnectionError as e:
                reason = "Ollama injoignable"
                log.error("Ollama injoignable (%s) : arret propre.", e)
                break
            except Exception as e:  # reponse invalide, delai, HTTP
                errors += 1
                db[COLL].update_one({"_id": doc["_id"]}, {
                    "$set": dict(base, error=str(e)[:300]), "$inc": {"attempts": 1}}, upsert=True)
                log.warning("Fiche %s en echec : %s", doc["_id"], e)
            in_batch += 1
            if heartbeat(db, {"done": done, "errors": errors, "batch": batch}):
                reason = "arret demande depuis la page"
                break
        else:
            if in_batch:
                log.info("Lot %s-%03d termine", run_id, batch_no)
    finally:
        release_lock(db, {"done": done, "errors": errors, "stop_reason": reason})
    dur = (_now() - started).total_seconds()
    log.info("Fin %s : %d structurees, %d echecs, %.0f s (%s)", run_id, done, errors, dur, reason)
    return {"run_id": run_id, "done": done, "errors": errors, "reason": reason, "seconds": dur}


# ---------------------------------------------------------------------------
# Mode tache planifiee : demandes de la page et fenetre de nuit
# ---------------------------------------------------------------------------
def lock_alive(db):
    lock = db["cockpit_settings"].find_one({"_id": LOCK_ID}) or {}
    until = lock.get("until")
    if isinstance(until, datetime) and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return bool(lock.get("running")) and isinstance(until, datetime) and until > _now()


def claim_request(db):
    """Passe la demande en attente a 'running' (une seule tache la prend)."""
    return db["cockpit_settings"].find_one_and_update(
        {"_id": REQUEST_ID, "state": "pending"},
        {"$set": {"state": "running", "started_at": _now()}}, return_document=True)


def in_window(window, now_local):
    """'01:00-06:00' -> True si l'heure locale est dans la fenetre (peut passer minuit)."""
    a, b = window.split("-")
    ha, ma = (int(x) for x in a.split(":"))
    hb, mb = (int(x) for x in b.split(":"))
    t, ta, tb = now_local.hour * 60 + now_local.minute, ha * 60 + ma, hb * 60 + mb
    return ta <= t < tb if ta < tb else (t >= ta or t < tb)


def poll(db, night=None, session=None):
    """Une passe de la tache planifiee. Rend ce qui a ete fait (pour le journal)."""
    if lock_alive(db):
        return {"skipped": "traitement deja en cours"}
    req = claim_request(db)
    if req:
        size = req.get("batch_size") if req.get("batch_size") in BATCH_SIZES else BATCH_SIZE
        batches = max(1, min(MAX_REQUEST_BATCHES, int(req.get("batches") or 1)))
        out = run(db, batch_size=size, max_batches=batches, session=session,
                  requested_by=req.get("requested_by"))
        if out.get("locked"):   # course perdue contre un autre lancement : on rend la demande
            db["cockpit_settings"].update_one({"_id": REQUEST_ID}, {"$set": {"state": "pending"}})
            return {"skipped": "verrou pris"}
        db["cockpit_settings"].update_one({"_id": REQUEST_ID}, {"$set": {
            "state": "done", "ended_at": _now(), "run_id": out.get("run_id"),
            "result": {k: out.get(k) for k in ("done", "errors", "reason", "seconds")}}})
        return {"request": out}
    settings = db["cockpit_settings"].find_one({"_id": SETTINGS_ID}) or {}
    if night and settings.get("night_enabled") and in_window(night, datetime.now(PARIS)):
        return {"night": run(db, batch_size=BATCH_SIZE, end_at=night.split("-")[1], session=session,
                             requested_by="nuit")}
    return {"skipped": "rien a faire"}


def _setup_logging():
    here = os.path.dirname(os.path.abspath(__file__))
    logdir = os.path.join(here, "logs")
    os.makedirs(logdir, exist_ok=True)
    path = os.path.join(logdir, "pcorg_digest-%s.log" % datetime.now(PARIS).strftime("%Y%m%d"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])


def main(argv=None):
    p = argparse.ArgumentParser(description="Fiches pcorg structurees par le modele local")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--max-batches", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--end-at", help="HH:MM, heure locale d'arret (tache de nuit)")
    p.add_argument("--only-gps", action="store_true")
    p.add_argument("--event")
    p.add_argument("--poll", action="store_true", help="mode tache planifiee (demandes de la page, nuit)")
    p.add_argument("--night", help="fenetre de nuit HH:MM-HH:MM (avec --poll)")
    a = p.parse_args(argv)
    if a.poll:
        db = _db()
        # Passe a vide (cas courant, chaque minute) : pas de journal ouvert pour rien
        if lock_alive(db):
            return 0
        _setup_logging()
        out = poll(db, night=a.night)
        if "skipped" not in out:
            log.info("Passe de la tache : %s", out)
        return 0
    _setup_logging()
    out = run(_db(), batch_size=a.batch_size, max_batches=a.max_batches, limit=a.limit,
              end_at=a.end_at, only_gps=a.only_gps, event=a.event)
    return 1 if out.get("locked") else 0


if __name__ == "__main__":
    sys.exit(main())
