"""Controle de documents depuis les tablettes Field (08/11/2026).

La tablette lit le document sur place (bande MRZ, texte, QR / code-barres) ou
l'agent saisit nom / prenom / entreprise / SIRET. Cockpit ne consulte AUCUN
fichier lui-meme : il verifie le droit de la tablette, normalise la demande,
la transmet signee au serveur de consultation (CONTROLE_API_URL), assainit la
reponse et trace chaque consultation (collection `controle_consultations`).

Aucune image ne quitte la tablette : seuls les champs lus ou saisis partent.
Contrat complet de l'API : docs/controle-documents.md.

Sans serveur configure : 503 `consultation_non_configuree`, sauf tablette en
mode test (resultats fictifs, pour travailler l'ecran sans vraie base).
"""
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

COLLECTION = "controle_consultations"

TYPES = ("personne", "entreprise", "code")
SOURCES = ("manuel", "mrz", "ocr", "code")
MOTIFS = ("acces", "identite", "incident", "autre")
STATUTS = ("aucun", "correspondance", "attention", "alerte")
NIVEAUX = ("info", "ok", "attention", "alerte")
ETATS_SOURCE = ("ok", "indisponible", "non_consulte")

_NOM_RE = re.compile(r"^[A-Za-zÀ-ɏ' .-]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOC_RE = re.compile(r"^[A-Z0-9<]{4,20}$")
_PAYS_RE = re.compile(r"^[A-Z<]{3}$")
_TTL_READY = False


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _api_url():
    return (os.environ.get("CONTROLE_API_URL") or "").strip()


def _api_secret():
    return os.environ.get("CONTROLE_API_SECRET") or ""


def api_configured():
    return bool(_api_url() and len(_api_secret()) >= 32)


def _now():
    return datetime.now(timezone.utc)


def _txt(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:limit]


def luhn_ok(digits):
    """Cle de Luhn (SIREN, SIRET). Les etablissements de La Poste (SIREN
    356000000) suivent une autre regle : somme des chiffres multiple de 5."""
    if not digits.isdigit():
        return False
    total = 0
    for i, c in enumerate(reversed(digits)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    if total % 10 == 0:
        return True
    return len(digits) == 14 and digits.startswith("356000000") and sum(int(c) for c in digits) % 5 == 0


def normalize_query(data):
    """Corps de la tablette -> (demande normalisee, None) ou (None, code d'erreur)."""
    if not isinstance(data, dict):
        return None, "requete_invalide"
    q = data.get("query")
    if not isinstance(q, dict):
        return None, "requete_invalide"
    kind = q.get("type")
    if kind not in TYPES:
        return None, "type_invalide"
    source = data.get("source") if data.get("source") in SOURCES else "manuel"
    motif = data.get("motif") if data.get("motif") in MOTIFS else "acces"
    out = {"type": kind}

    if kind == "personne":
        nom = _txt(q.get("nom"), 80)
        prenom = _txt(q.get("prenom"), 80)
        for v in (nom, prenom):
            if v and not _NOM_RE.match(v):
                return None, "nom_invalide"
        date = _txt(q.get("date_naissance"), 10)
        if date:
            if not _DATE_RE.match(date):
                return None, "date_invalide"
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return None, "date_invalide"
        doc = _txt(q.get("numero_document"), 20).upper().replace(" ", "")
        if doc and not _DOC_RE.match(doc):
            return None, "document_invalide"
        if len(nom) < 2 and not doc:
            return None, "nom_requis"
        out.update(nom=nom, prenom=prenom, date_naissance=date, numero_document=doc,
                   type_document=_txt(q.get("type_document"), 30),
                   nationalite=_txt(q.get("nationalite"), 3).upper())
        if out["nationalite"] and not _PAYS_RE.match(out["nationalite"]):
            out["nationalite"] = ""
        if out["type_document"] and not re.match(r"^[a-z_]+$", out["type_document"]):
            out["type_document"] = ""
    elif kind == "entreprise":
        raison = _txt(q.get("raison_sociale"), 120)
        siret = re.sub(r"\s", "", _txt(q.get("siret"), 20))
        if siret:
            if len(siret) not in (9, 14) or not luhn_ok(siret):
                return None, "siret_invalide"
        if len(raison) < 2 and not siret:
            return None, "entreprise_requise"
        out.update(raison_sociale=raison, siret=siret)
    else:
        valeur = _txt(q.get("valeur"), 512)
        if not valeur:
            return None, "code_requis"
        out.update(valeur=valeur, format=_txt(q.get("format"), 20), type_code=_txt(q.get("type_code"), 20))
        # Titulaire lu sur le badge (facultatif) : le serveur verifie qu'il correspond
        for k in ("nom", "prenom"):
            v = _txt(q.get(k), 80)
            if v and not _NOM_RE.match(v):
                return None, "nom_invalide"
            if v:
                out[k] = v
    return {"query": out, "source": source, "motif": motif}, None


# ---------------------------------------------------------------------------
# Reponse du serveur de consultation : jamais affichee telle quelle
# ---------------------------------------------------------------------------

def sanitize_response(raw):
    if not isinstance(raw, dict):
        raw = {}
    results = []
    for r in (raw.get("resultats") or [])[:20]:
        if not isinstance(r, dict):
            continue
        champs = []
        for ch in (r.get("champs") or [])[:20]:
            if isinstance(ch, dict) and ch.get("label"):
                champs.append({"label": _txt(ch.get("label"), 40), "valeur": _txt(ch.get("valeur"), 200)})
        results.append({
            "niveau": r.get("niveau") if r.get("niveau") in NIVEAUX else "info",
            "titre": _txt(r.get("titre"), 120) or "Resultat",
            "sous_titre": _txt(r.get("sous_titre"), 160),
            "source": _txt(r.get("source"), 60),
            "message": _txt(r.get("message"), 500),
            "consignes": _txt(r.get("consignes"), 500),
            "champs": champs,
        })
    sources = []
    for s in (raw.get("sources") or [])[:10]:
        if isinstance(s, dict) and s.get("nom"):
            sources.append({"nom": _txt(s.get("nom"), 60),
                            "etat": s.get("etat") if s.get("etat") in ETATS_SOURCE else "ok"})
    messages = [_txt(m, 300) for m in (raw.get("messages") or [])[:5] if isinstance(m, str) and m.strip()]
    statut = raw.get("statut")
    if statut not in STATUTS:
        niveaux = {r["niveau"] for r in results}
        statut = ("alerte" if "alerte" in niveaux else "attention" if "attention" in niveaux
                  else "correspondance" if results else "aucun")
    return {"statut": statut, "resultats": results, "sources": sources, "messages": messages}


def signature(secret, timestamp, body):
    mac = hmac.new(secret.encode("utf-8"), (str(timestamp) + ".").encode("utf-8") + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _call_api(payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time()))
    resp = requests.post(_api_url(), data=body, timeout=_env_int("CONTROLE_API_TIMEOUT", 8), headers={
        "Content-Type": "application/json",
        "X-Cockpit-Timestamp": ts,
        "X-Cockpit-Signature": signature(_api_secret(), ts, body),
        "X-Cockpit-Consultation": payload["consultation_id"],
    })
    if resp.status_code != 200:
        raise RuntimeError("HTTP %s" % resp.status_code)
    return resp.json()


# ---------------------------------------------------------------------------
# Mode test : resultats fictifs, jamais une vraie personne
# ---------------------------------------------------------------------------

def demo_response(q):
    demo = {"source": "Demonstration"}
    if q["type"] == "personne":
        nom = q.get("nom", "").upper()
        ident = " ".join(x for x in (q.get("nom"), q.get("prenom")) if x) or q.get("numero_document")
        champs = [{"label": "Nom", "valeur": q.get("nom") or "-"}, {"label": "Prenom", "valeur": q.get("prenom") or "-"}]
        if q.get("date_naissance"):
            champs.append({"label": "Naissance", "valeur": q["date_naissance"]})
        if "ALERTE" in nom:
            res = dict(demo, niveau="alerte", titre=ident, sous_titre="Interdiction d'acces (fictif)",
                       message="Personne interdite d'acces au site jusqu'au 31/12/2026.",
                       consignes="Ne pas intervenir seul. Prevenir le PC Org.", champs=champs)
        elif "VERIF" in nom:
            res = dict(demo, niveau="attention", titre=ident, sous_titre="Accreditation expiree (fictif)",
                       message="Accreditation valable jusqu'au 20/04/2025.", champs=champs)
        elif "TEST" in nom:
            res = dict(demo, niveau="ok", titre=ident, sous_titre="Accreditation valide (fictif)",
                       message="Zones : RC, MC, BLC.", champs=champs)
        else:
            return {"statut": "aucun", "resultats": [], "messages": ["Resultats fictifs (mode test)."],
                    "sources": [{"nom": "Demonstration", "etat": "ok"}]}
    elif q["type"] == "entreprise":
        res = dict(demo, niveau="ok", titre=q.get("raison_sociale") or "Entreprise " + q.get("siret", ""),
                   sous_titre="Etablissement actif (fictif)",
                   champs=[{"label": "SIRET", "valeur": q.get("siret") or "-"}])
    else:
        champs = [{"label": "Format", "valeur": q.get("format") or "-"}]
        if q.get("nom"):
            champs.insert(0, {"label": "Titulaire", "valeur": " ".join(x for x in (q["nom"], q.get("prenom")) if x)})
        res = dict(demo, niveau="ok", titre="Titre " + q["valeur"][:40], sous_titre="Titre valide (fictif)", champs=champs)
    return {"resultats": [res], "messages": ["Resultats fictifs (mode test)."],
            "sources": [{"nom": "Demonstration", "etat": "ok"}]}


# ---------------------------------------------------------------------------
# Point d'entree de la route /field/controle/search
# ---------------------------------------------------------------------------

def _ensure_ttl(db):
    global _TTL_READY
    if _TTL_READY:
        return
    try:
        db[COLLECTION].create_index("expire_at", expireAfterSeconds=0)
        db[COLLECTION].create_index([("device_id", 1), ("at", -1)])
        _TTL_READY = True
    except Exception:
        pass


def search(db, device, data, group_id=None, position=None):
    """Retourne (corps JSON, code HTTP). Toute tentative valide est tracee."""
    norm, err = normalize_query(data)
    if err:
        return {"ok": False, "error": err}, 400
    _ensure_ttl(db)
    now = _now()
    device_id = str(device.get("_id"))
    consultation_id = uuid.uuid4().hex
    test_mode = bool(device.get("test_mode"))
    log = {
        "_id": consultation_id, "at": now, "device_id": device_id,
        "device_name": device.get("name"), "group_id": group_id,
        "event": device.get("event"), "year": device.get("year"),
        "motif": norm["motif"], "source": norm["source"], "query": norm["query"],
        "position": position,
        "expire_at": now + timedelta(days=_env_int("CONTROLE_RETENTION_DAYS", 365)),
    }
    # Trace AVANT toute consultation, puis comptage qui l'inclut : deux appels
    # simultanes ne peuvent pas passer tous les deux sous la limite. Sans
    # journal, pas de consultation.
    try:
        db[COLLECTION].insert_one(dict(log, statut="en_cours"))
    except Exception:
        return {"ok": False, "error": "journal_indisponible"}, 503
    limit = _env_int("CONTROLE_RATE_10MIN", 40)
    recent = db[COLLECTION].count_documents({"device_id": device_id, "at": {"$gte": now - timedelta(minutes=10)}})
    if recent > limit:
        db[COLLECTION].update_one({"_id": consultation_id}, {"$set": {"statut": "refuse_limite", "provider": None}})
        return {"ok": False, "error": "trop_de_consultations"}, 429
    started = time.monotonic()
    status_code = 200
    if api_configured():
        log["provider"] = "api"
        try:
            body = sanitize_response(_call_api({
                "consultation_id": consultation_id,
                "query": norm["query"], "motif": norm["motif"], "source": norm["source"],
                "origine": {"application": "cockpit-field", "tablette_id": device_id,
                            "tablette": device.get("name"), "groupe": group_id,
                            "evenement": device.get("event"), "annee": device.get("year")},
                "ts": now.isoformat(),
            }))
        except Exception as e:
            log["error"] = _txt(str(e), 200)
            body, status_code = None, 502
    elif test_mode:
        log["provider"] = "demo"
        body = sanitize_response(demo_response(norm["query"]))
    else:
        log["provider"] = None
        body, status_code = None, 503
    done = {"provider": log.get("provider"), "duration_ms": int((time.monotonic() - started) * 1000),
            "statut": body["statut"] if body else ("erreur" if status_code == 502 else "non_configure")}
    if body:
        done["nb_resultats"] = len(body["resultats"])
    if log.get("error"):
        done["error"] = log["error"]
    try:
        db[COLLECTION].update_one({"_id": consultation_id}, {"$set": done})
    except Exception:
        pass
    if status_code == 503:
        return {"ok": False, "error": "consultation_non_configuree", "consultation_id": consultation_id}, 503
    if status_code == 502:
        return {"ok": False, "error": "consultation_indisponible", "consultation_id": consultation_id}, 502
    body.update(ok=True, consultation_id=consultation_id, demo=log["provider"] == "demo")
    return body, 200
