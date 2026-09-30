"""
pcorg_assist.py - Aide a la saisie et rappel des precedents (main courante).

Deux fonctions, un seul blueprint (`pcorg_assist_bp`) :

F3 - Aide a la saisie d'une fiche (assistant de creation de pcorg.js)
    POST /api/pcorg/assist/suggest
    - suggestions de classement (categorie, sous-classification, urgence,
      zone citee) par Claude Haiku, JAMAIS appliquees automatiquement ;
    - doublons possibles : fiches ouvertes ou saisies dans les 2 dernieres
      heures sur le meme couple event/year, calcules SANS LLM (TF-IDF +
      Jaccard, bonus zone et GPS).

F4 - "Deja vu" : precedents d'une fiche (vue detail de pcorg.js)
    GET  /api/pcorg/assist/similar/<fiche_id>?scope=editions|all
    POST /api/pcorg/assist/similar/<fiche_id>/synthesis
    - recherche sans LLM dans les AUTRES editions du meme evenement (ou
      tous evenements), pre-filtree par Mongo ($text si un index texte
      existe, sinon meme categorie), classee en Python ;
    - synthese optionnelle par Claude, strictement fondee sur les
      precedents fournis, mise en cache 30 jours dans
      `pcorg_precedent_syntheses`.

Index : un index texte `pca_text` est cree a la volee sur `pcorg` si AUCUN
index texte n'existe (une collection n'en porte qu'un seul). S'il en existe
deja un, il est utilise tel quel. `PCA_CREATE_TEXT_INDEX=0` desactive la
creation (repli : meme categorie, plus recentes d'abord).

Les droits par groupe (categories autorisees) sont appliques cote serveur
via `app.get_user_allowed_categories`, importe tardivement (comme
pmv.py / scan_report.py) pour eviter l'import circulaire.
"""
import hashlib
import inspect
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

import pcorg_history as PH
import pcorg_summary

logger = logging.getLogger(__name__)

pcorg_assist_bp = Blueprint("pcorg_assist", __name__)

# ---------------------------------------------------------------------------
# Constantes metier (miroir du code existant, jamais inventees)
# ---------------------------------------------------------------------------

# app.ALL_PCO_CATEGORIES
ALL_PCO_CATEGORIES = [
    "PCO.Secours", "PCO.Securite", "PCO.Technique",
    "PCO.Flux", "PCO.Information", "PCO.MainCourante", "PCO.Fourriere",
]

# static/js/pcorg.js : CTX_DESCRIPTIONS (menu clic droit de la carte)
CATEGORY_DEFINITIONS = {
    "PCO.Secours": "Victime, malaise, blessure",
    "PCO.Securite": "Incident, intrusion, vol",
    "PCO.Technique": "Panne, infrastructure, materiel",
    "PCO.Flux": "Circulation, acces, jauge",
    "PCO.Fourriere": "Vehicule, stationnement",
    "PCO.Information": "Signalement, observation",
    "PCO.MainCourante": "Note, consigne, suivi",
}

# app.VALID_URGENCY_LEVELS / app.URGENCY_LABELS
VALID_URGENCY_LEVELS = ("EU", "UA", "UR", "IMP")
URGENCY_LABELS = {
    "SECOURS": {"EU": "Detresse vitale", "UA": "Urgence absolue",
                "UR": "Urgence relative", "IMP": "Implique medical"},
    "SECURITE": {"EU": "Danger immediat", "UA": "Incident grave",
                 "UR": "Incident en cours", "IMP": "Temoin / implique"},
    "MIXTE": {"EU": "Urgence extreme", "UA": "Urgence prioritaire",
              "UR": "Situation stable", "IMP": "Implique"},
}

SUGGEST_MODEL = os.getenv("PCA_SUGGEST_MODEL", "claude-haiku-4-5").strip()
SUGGEST_MAX_TOKENS = 300
SUGGEST_MIN_CHARS = 25
SUGGEST_MAX_CHARS = 2000
SUGGEST_CACHE_TTL_S = 15 * 60
SUGGEST_CACHE_MAX = 256
SUGGEST_MAX_PARALLEL = 4

DUPLICATE_WINDOW_H = 2
DUPLICATE_MAX_CANDIDATES = 500
DUPLICATE_TOP = 3
DUPLICATE_MIN_SCORE = 0.30

PRECEDENT_TOP = 5
PRECEDENT_MAX_CANDIDATES = 2000
PRECEDENT_MIN_TEXT = 0.12
# 600 tokens de reponse vises ; le budget est plus large parce que, sur les
# modeles a reflexion adaptative (defaut CLAUDE_MODEL), la reflexion consomme
# le meme max_tokens que la reponse. Effort "low" : resume factuel court.
SYNTHESIS_MAX_TOKENS = 1500
SYNTHESIS_EFFORT = "low"
SYNTHESIS_TTL_DAYS = 30
COL_SYNTHESES = "pcorg_precedent_syntheses"

TEXT_INDEX_NAME = "pca_text"
CREATE_TEXT_INDEX = os.getenv("PCA_CREATE_TEXT_INDEX", "1").strip() not in ("0", "false", "no")

PARIS = PH.PARIS

# ---------------------------------------------------------------------------
# Acces application (import tardif : app.py importe ce module)
# ---------------------------------------------------------------------------


def _db():
    from app import db
    return db


def _resolve_role_required():
    from app import role_required
    return role_required


def _allowed_categories(payload):
    """Liste des categories autorisees, None = aucune restriction.

    En cas d'echec de la resolution : liste vide (on ferme, on n'ouvre pas).
    """
    try:
        from app import get_user_allowed_categories
        return get_user_allowed_categories(payload)
    except Exception as e:  # pragma: no cover - dependance app
        logger.warning("pcorg_assist : categories autorisees indisponibles (%s)", e)
        return []


def _role_required(role):
    """role_required d'app.py, resolu a l'appel (meme comportement que les
    autres routes /api/pcorg/*)."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            return _resolve_role_required()(role)(f)(*args, **kwargs)
        return wrapper
    return deco


def _err(code, status=400, **extra):
    body = {"ok": False, "error": code}
    body.update(extra)
    return jsonify(body), status


def _claude_call(system, user, max_tokens, model, db=None, effort=None):
    """pcorg_summary._claude_stream_request avec les seuls parametres qu'il
    accepte (db = controle de budget, effort = profondeur de reflexion) :
    sa signature evolue, un argument inconnu leverait un TypeError."""
    fn = pcorg_summary._claude_stream_request
    kwargs = {"model": model, "system_cache": False}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if db is not None and "db" in params:
        kwargs["db"] = db
    if effort and "effort" in params:
        kwargs["effort"] = effort
    return fn(system, user, max_tokens, **kwargs)


def _record_usage(db, feature, model, usage, meta=None):
    """pcorg_summary.record_ai_usage si present (ajoute en parallele), sinon rien."""
    fn = getattr(pcorg_summary, "record_ai_usage", None)
    if not callable(fn):
        return
    try:
        fn(db, feature, model, usage, meta=meta)
    except Exception as e:
        logger.warning("record_ai_usage(%s) : %s", feature, e)


# ---------------------------------------------------------------------------
# Normalisation et similarite (pur Python, sans LLM)
# ---------------------------------------------------------------------------

STOPWORDS_FR = frozenset("""
a ai au aux avec avait avoir c ca car ce cela celle celui ces cet cette chez
d dans de des donc dont du elle elles en encore est et etait ete etre eu eux
fait faire il ils j je l la le les leur leurs lui m ma mais me meme mes moi
mon n ne ni nos notre nous on ont ou par pas peu plus pour qu que quel qui
s sa sans se ses si sont son sous sur t ta te tes toi ton tous tout toute
toutes tres tu un une vers via vos votre vous y apres avant aussi ainsi
alors deja ici puis mr mme monsieur madame svp merci bien
""".split())

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold(s):
    """Minuscules, sans accents, ponctuation -> espace, espaces reduits."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(_NON_ALNUM.sub(" ", s).split())


def _stem(tok):
    if len(tok) > 4 and tok[-1] in "sx" and not tok.isdigit():
        return tok[:-1]
    return tok


def tokenize(text):
    """Jetons significatifs : sans accents, sans mots vides, pluriel simple
    retire. Les nombres courts sont gardes ("porte 5")."""
    out = []
    for tok in fold(text).split():
        if tok in STOPWORDS_FR:
            continue
        if len(tok) < 2 and not tok.isdigit():
            continue
        out.append(_stem(tok))
    return out


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def tfidf_vectors(token_lists):
    """Vecteurs TF-IDF normalises (dict jeton -> poids), IDF lisse sur le lot."""
    n = len(token_lists)
    df = Counter()
    for toks in token_lists:
        df.update(set(toks))
    idf = {t: math.log((n + 1.0) / (c + 1.0)) + 1.0 for t, c in df.items()}
    vecs = []
    for toks in token_lists:
        tf = Counter(toks)
        v = {t: (1.0 + math.log(c)) * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
        vecs.append({t: w / norm for t, w in v.items()})
    return vecs


def cosine(u, v):
    if len(u) > len(v):
        u, v = v, u
    return sum(w * v.get(t, 0.0) for t, w in u.items())


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _doc_latlon(doc):
    gps = (doc or {}).get("gps")
    if isinstance(gps, dict):
        c = gps.get("coordinates")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            try:
                return float(c[1]), float(c[0])
            except (TypeError, ValueError):
                return None
    return None


def _zone_key(desc):
    """Zone comparable : prefixe technique Prysm retire, repliee."""
    d = re.sub(r"_MC PCO/?", "", str(desc or ""))
    return fold(d)


def _doc_zone(doc):
    return _zone_key(((doc or {}).get("area") or {}).get("desc"))


def _doc_text(doc):
    return (doc or {}).get("text") or (doc or {}).get("text_full") or ""


def _doc_sous(doc):
    return (((doc or {}).get("content_category") or {}).get("sous_classification") or "")


def _excerpt(text, n=180):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "..."


def _gps_bonus(q_latlon, doc, near_m, far_m, near_bonus, far_bonus):
    d_ll = _doc_latlon(doc)
    if not q_latlon or not d_ll:
        return 0.0, None
    dist = haversine_m(q_latlon[0], q_latlon[1], d_ll[0], d_ll[1])
    if dist <= near_m:
        return near_bonus, dist
    if dist <= far_m:
        return far_bonus, dist
    return 0.0, dist


def _text_scores(query_text, docs):
    """(cosinus TF-IDF, Jaccard) de la requete contre chaque document."""
    q_toks = tokenize(query_text)
    d_toks = [tokenize(_doc_text(d)) for d in docs]
    vecs = tfidf_vectors([q_toks] + d_toks)
    qv = vecs[0]
    return [(cosine(qv, vecs[i + 1]), jaccard(q_toks, d_toks[i])) for i in range(len(docs))]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _valid_close_ts(doc):
    ct = doc.get("close_ts")
    if doc.get("status_code") != 10 or not isinstance(ct, datetime) or ct.year >= 9000:
        return None
    return ct


def _duration_min(doc):
    ct = _valid_close_ts(doc)
    ts = PH.to_aware(doc.get("ts"))
    if ct is None or ts is None:
        return None
    mins = (PH.to_aware(ct) - ts).total_seconds() / 60.0
    return int(round(mins)) if mins >= 0 else None


def _short_item(doc, score, extra=None):
    area = (doc.get("area") or {})
    out = {
        "id": str(doc.get("_id")),
        "event": doc.get("event"),
        "year": doc.get("year"),
        "ts": PH.iso_paris(doc.get("ts")),
        "category": doc.get("category"),
        "sous_classification": _doc_sous(doc),
        "niveau_urgence": doc.get("niveau_urgence"),
        "area_desc": re.sub(r"_MC PCO/?", "", str(area.get("desc") or "")).lstrip("/"),
        "status_closed": doc.get("status_code") == 10,
        "excerpt": _excerpt(_doc_text(doc)),
        "score": round(float(score), 3),
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# F3 - doublons possibles
# ---------------------------------------------------------------------------

def rank_duplicates(text, docs, lat=None, lng=None, area_desc=None,
                    top=DUPLICATE_TOP, min_score=DUPLICATE_MIN_SCORE):
    """Classe les fiches candidates par proximite avec le texte saisi.

    score = 0.7 * cosinus TF-IDF + 0.3 * Jaccard
            + 0.15 meme zone + 0.20 (< 100 m) / 0.10 (< 300 m)
    Un rapprochement purement geographique ne suffit pas : il faut un
    minimum de texte commun (0.12) pour qu'une fiche soit retenue.
    """
    docs = [d for d in docs if _doc_text(d).strip()]
    if not docs or len(tokenize(text)) == 0:
        return []
    q_ll = None
    try:
        if lat is not None and lng is not None and lat != "" and lng != "":
            q_ll = (float(lat), float(lng))
    except (TypeError, ValueError):
        q_ll = None
    q_zone = _zone_key(area_desc) if area_desc else ""
    ranked = []
    for doc, (cos, jac) in zip(docs, _text_scores(text, docs)):
        text_score = 0.7 * cos + 0.3 * jac
        if text_score < 0.12:
            continue
        score = text_score
        same_zone = bool(q_zone) and _doc_zone(doc) == q_zone
        if same_zone:
            score += 0.15
        bonus, dist = _gps_bonus(q_ll, doc, 100, 300, 0.20, 0.10)
        score += bonus
        if score < min_score:
            continue
        ranked.append((score, doc, same_zone, dist))
    # Tri sur le score brut : le plafond a 1 n'est qu'un affichage
    ranked.sort(key=lambda r: r[0], reverse=True)
    out = []
    for score, doc, same_zone, dist in ranked[:top]:
        out.append(_short_item(doc, min(score, 1.0), {
            "same_zone": same_zone,
            "distance_m": int(round(dist)) if dist is not None else None,
        }))
    return out


_DUP_PROJECTION = {
    "_id": 1, "event": 1, "year": 1, "ts": 1, "category": 1, "text": 1,
    "text_full": 1, "area": 1, "gps": 1, "status_code": 1, "niveau_urgence": 1,
    "content_category.sous_classification": 1,
}


def _cat_filter(allowed):
    if allowed is None:
        return {"$regex": "^PCO"}
    return {"$in": [c for c in allowed if str(c).startswith("PCO.")]}


def query_duplicate_candidates(col, event, year, allowed, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    since = now_utc - timedelta(hours=DUPLICATE_WINDOW_H)
    q = {
        "event": event, "year": year, "category": _cat_filter(allowed),
        "$or": [{"status_code": {"$ne": 10}}, {"ts": {"$gte": since}}],
    }
    return list(col.find(q, _DUP_PROJECTION).sort("ts", -1).limit(DUPLICATE_MAX_CANDIDATES))


# ---------------------------------------------------------------------------
# F3 - suggestions de classement (Claude Haiku)
# ---------------------------------------------------------------------------

def _labels(items):
    out = []
    for it in items or []:
        lbl = it.get("label") if isinstance(it, dict) else it
        if lbl and str(lbl).strip():
            out.append(str(lbl).strip())
    return out


def load_pcorg_lists(db):
    """(sous-classifications par categorie, categories avec urgence)."""
    doc = db["pcorg_config"].find_one({"_id": "pcorg_lists"}) or {}
    sous = {cat: _labels(v) for cat, v in (doc.get("sous_classifications") or {}).items()}
    urg = {cat for cat, on in (doc.get("urgence_categories") or {}).items() if on}
    return sous, urg


def _urgency_type(cat):
    if cat == "PCO.Secours":
        return "SECOURS"
    if cat == "PCO.Securite":
        return "SECURITE"
    return "MIXTE"


def build_suggest_prompts(text, categories, sous_by_cat, urgency_cats,
                          area_desc=None, current_category=None):
    """(system, user) pour la suggestion de classement."""
    lines = [
        "Tu assistes un operateur du PC Organisation (circuit du Mans, evenements "
        "sportifs et festivals) qui saisit une fiche de main courante.",
        "Propose un classement a partir du texte de la fiche. Tu ne dois RIEN inventer : "
        "si le texte ne permet pas de conclure, mets null et une confiance basse.",
        "",
        "Categories autorisees (code : definition) :",
    ]
    for cat in categories:
        lines.append("- %s : %s" % (cat, CATEGORY_DEFINITIONS.get(cat, "")))
    lines.append("")
    lines.append("Sous-classifications (recopier EXACTEMENT un libelle de la liste de la "
                 "categorie choisie, sinon null) :")
    for cat in categories:
        subs = sous_by_cat.get(cat) or []
        lines.append("- %s : %s" % (cat, " | ".join(subs) if subs else "(aucune)"))
    lines.append("")
    urg = [c for c in categories if c in urgency_cats]
    if urg:
        lines.append("Niveau d'urgence : uniquement pour les categories %s, sinon null." % ", ".join(urg))
        for lvl in VALID_URGENCY_LEVELS:
            lines.append("- %s : %s (Secours) / %s (Securite) / %s (autres)" % (
                lvl, URGENCY_LABELS["SECOURS"][lvl], URGENCY_LABELS["SECURITE"][lvl],
                URGENCY_LABELS["MIXTE"][lvl]))
        lines.append("Ne propose EU ou UA que si le texte decrit explicitement une gravite "
                     "(detresse vitale, danger immediat, blessure grave...).")
    else:
        lines.append("Niveau d'urgence : toujours null.")
    lines += [
        "",
        "zone : le lieu explicitement cite dans le texte (porte, parking, tribune...), "
        "recopie tel quel, sinon null.",
        "",
        "Reponds UNIQUEMENT par un objet JSON, sans texte autour :",
        '{"category": "PCO.xxx" | null, "sous_classification": "..." | null, '
        '"niveau_urgence": "EU" | "UA" | "UR" | "IMP" | null, "zone": "..." | null, '
        '"confidence": 0.0 a 1.0, "reason": "une phrase courte en francais"}',
    ]
    system = "\n".join(lines)
    user = ["Texte de la fiche :", '"""', str(text)[:SUGGEST_MAX_CHARS], '"""']
    if area_desc:
        user.append("Zone de la position posee sur la carte : %s" % str(area_desc)[:120])
    if current_category:
        user.append("Categorie deja choisie par l'operateur : %s" % current_category)
    return system, "\n".join(user)


def extract_json_object(text):
    if not text:
        return None
    s = str(text).strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_suggestion(raw, allowed_categories, sous_by_cat, urgency_cats,
                        current_category=None):
    """Nettoie la reponse du modele. Tout champ hors referentiel est ecarte :
    une categorie inventee ou non autorisee n'est jamais proposee."""
    if not isinstance(raw, dict):
        return None
    allowed = list(allowed_categories or [])
    cat = raw.get("category")
    cat = cat if isinstance(cat, str) and cat in allowed else None

    effective = cat or (current_category if current_category in allowed else None)

    sous = None
    raw_sous = raw.get("sous_classification")
    if effective and isinstance(raw_sous, str) and raw_sous.strip():
        wanted = fold(raw_sous)
        for lbl in sous_by_cat.get(effective) or []:
            if fold(lbl) == wanted:
                sous = lbl
                break

    urg = raw.get("niveau_urgence")
    urg = urg.strip().upper() if isinstance(urg, str) else None
    if urg not in VALID_URGENCY_LEVELS or not effective or effective not in urgency_cats:
        urg = None

    zone = raw.get("zone")
    zone = " ".join(zone.split())[:80] if isinstance(zone, str) and zone.strip() else None

    try:
        conf = float(raw.get("confidence"))
        if conf != conf:
            raise ValueError
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reason = raw.get("reason")
    reason = " ".join(reason.split())[:200] if isinstance(reason, str) else ""

    if not (cat or sous or urg or zone):
        return None
    return {
        "category": cat,
        "sous_classification": sous,
        "sous_classification_category": effective if sous else None,
        "niveau_urgence": urg,
        "niveau_urgence_label": URGENCY_LABELS[_urgency_type(effective)][urg] if urg else None,
        "zone": zone,
        "confidence": round(conf, 2),
        "reason": reason,
    }


_suggest_cache = OrderedDict()
_suggest_lock = threading.Lock()
_suggest_sem = threading.BoundedSemaphore(SUGGEST_MAX_PARALLEL)


def _cache_get(key):
    with _suggest_lock:
        hit = _suggest_cache.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > SUGGEST_CACHE_TTL_S:
            _suggest_cache.pop(key, None)
            return None
        _suggest_cache.move_to_end(key)
        return hit[1]


def _cache_put(key, value):
    with _suggest_lock:
        _suggest_cache[key] = (time.time(), value)
        _suggest_cache.move_to_end(key)
        while len(_suggest_cache) > SUGGEST_CACHE_MAX:
            _suggest_cache.popitem(last=False)


def reset_cache():
    with _suggest_lock:
        _suggest_cache.clear()


def suggest_classification(db, text, allowed, area_desc=None, current_category=None,
                           meta=None):
    """Rend (suggestion|None, statut) ; statut : ok, cached, unavailable,
    busy, error, empty."""
    categories = [c for c in ALL_PCO_CATEGORIES if allowed is None or c in allowed]
    if not categories:
        return None, "empty"
    sous_by_cat, urgency_cats = load_pcorg_lists(db)
    key = hashlib.sha256(json.dumps([
        fold(text), current_category or "", categories, area_desc or "",
        sorted(urgency_cats), {c: sous_by_cat.get(c) for c in categories},
    ], sort_keys=True).encode("utf-8")).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("s"), "cached"
    if not pcorg_summary.ANTHROPIC_API_KEY:
        return None, "unavailable"
    if not _suggest_sem.acquire(blocking=False):
        return None, "busy"
    try:
        system, user = build_suggest_prompts(text, categories, sous_by_cat, urgency_cats,
                                             area_desc=area_desc,
                                             current_category=current_category)
        model = pcorg_summary._validate_model(SUGGEST_MODEL) or SUGGEST_MODEL
        raw_text, usage, _stop = _claude_call(system, user, SUGGEST_MAX_TOKENS, model, db=db)
        _record_usage(db, "pcorg_assist_suggest", model, usage, meta)
    except pcorg_summary.ClaudeError as e:
        logger.info("pcorg_assist suggest : %s", e)
        return None, "budget" if str(e) == "budget_exceeded" else "error"
    except Exception as e:
        logger.warning("pcorg_assist suggest : %s", e)
        return None, "error"
    finally:
        _suggest_sem.release()
    sug = validate_suggestion(extract_json_object(raw_text), categories, sous_by_cat,
                              urgency_cats, current_category=current_category)
    _cache_put(key, {"s": sug})
    return sug, "ok"


# ---------------------------------------------------------------------------
# F4 - precedents
# ---------------------------------------------------------------------------

_TEXT_INDEX_STATE = {}
_TEXT_INDEX_LOCK = threading.Lock()


def ensure_text_index(col):
    """True si la collection porte un index texte (cree `pca_text` si aucun)."""
    key = getattr(col, "full_name", None) or id(col)
    if key in _TEXT_INDEX_STATE:
        return _TEXT_INDEX_STATE[key]
    with _TEXT_INDEX_LOCK:
        if key in _TEXT_INDEX_STATE:
            return _TEXT_INDEX_STATE[key]
        ok = False
        try:
            for ix in col.list_indexes():
                if "_fts" in dict(ix.get("key") or {}):
                    ok = True
                    break
            if not ok and CREATE_TEXT_INDEX:
                t0 = time.time()
                col.create_index(
                    [("text", "text"), ("content_category.sous_classification", "text"),
                     ("area.desc", "text")],
                    name=TEXT_INDEX_NAME, default_language="french",
                    language_override="pca_language",
                    weights={"text": 5, "content_category.sous_classification": 2,
                             "area.desc": 1},
                )
                logger.info("pcorg_assist : index texte %s cree en %.1f s",
                            TEXT_INDEX_NAME, time.time() - t0)
                ok = True
        except Exception as e:
            logger.warning("pcorg_assist : index texte indisponible (%s)", e)
            ok = False
        _TEXT_INDEX_STATE[key] = ok
        return ok


_PREC_PROJECTION = {
    "_id": 1, "event": 1, "year": 1, "ts": 1, "close_ts": 1, "category": 1,
    "text": 1, "text_full": 1, "area": 1, "gps": 1, "status_code": 1,
    "niveau_urgence": 1, "content_category.sous_classification": 1,
}


def search_terms(text, max_terms=24):
    """Mots pour $text : replies, sans mots vides, les plus longs d'abord."""
    seen = []
    for tok in fold(text).split():
        if tok in STOPWORDS_FR or (len(tok) < 3 and not tok.isdigit()) or tok in seen:
            continue
        seen.append(tok)
    seen.sort(key=len, reverse=True)
    return seen[:max_terms]


def scope_filter(doc, scope, allowed):
    """Filtre Mongo des candidats : l'edition de la fiche est toujours exclue."""
    event, year = doc.get("event"), doc.get("year")
    q = {"category": _cat_filter(allowed), "_id": {"$ne": doc.get("_id")}}
    if scope == "all":
        q["$nor"] = [{"event": event, "year": year}]
    else:
        q["event"] = event
        q["year"] = {"$ne": year}
    return q


def fetch_precedent_candidates(col, doc, scope, allowed, limit=PRECEDENT_MAX_CANDIDATES):
    """(candidats, methode). Pre-filtre Mongo, jamais un balayage complet."""
    base = scope_filter(doc, scope, allowed)
    terms = search_terms(_doc_text(doc) + " " + _doc_sous(doc))
    if terms and ensure_text_index(col):
        q = dict(base)
        q["$text"] = {"$search": " ".join(terms)}
        proj = dict(_PREC_PROJECTION)
        proj["score"] = {"$meta": "textScore"}
        cur = col.find(q, proj).sort([("score", {"$meta": "textScore"})]).limit(limit)
        return list(cur), "text_index"
    q = dict(base)
    if doc.get("category") and (allowed is None or doc.get("category") in allowed):
        q["category"] = doc.get("category")
    return list(col.find(q, _PREC_PROJECTION).sort("ts", -1).limit(limit)), "category_recent"


def rank_precedents(doc, candidates, top=PRECEDENT_TOP, min_text=PRECEDENT_MIN_TEXT):
    """score = 0.75 * cosinus TF-IDF + 0.25 * Jaccard
               + 0.08 meme categorie + 0.12 meme sous-classification
               + 0.08 meme zone + 0.07 (< 200 m)
    Le texte doit porter au moins `min_text` : les bonus seuls ne font pas
    un precedent."""
    candidates = [c for c in candidates if _doc_text(c).strip()]
    if not candidates or not tokenize(_doc_text(doc)):
        return []
    cat = doc.get("category")
    sous = fold(_doc_sous(doc))
    zone = _doc_zone(doc)
    q_ll = _doc_latlon(doc)
    ranked = []
    for cand, (cos, jac) in zip(candidates, _text_scores(_doc_text(doc), candidates)):
        text_score = 0.75 * cos + 0.25 * jac
        if text_score < min_text:
            continue
        score = text_score
        reasons = []
        if cat and cand.get("category") == cat:
            score += 0.08
            reasons.append("categorie")
        if sous and fold(_doc_sous(cand)) == sous:
            score += 0.12
            reasons.append("sous_classification")
        if zone and _doc_zone(cand) == zone:
            score += 0.08
            reasons.append("zone")
        bonus, _dist = _gps_bonus(q_ll, cand, 200, 200, 0.07, 0.0)
        if bonus:
            score += bonus
            reasons.append("position")
        ranked.append((score, cand, reasons))
    ranked.sort(key=lambda r: (r[0], PH.to_aware(r[1].get("ts")) or datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True)
    return ranked[:top]


def closing_info(doc):
    """Comment la fiche a ete close : commentaire de cloture et dernieres
    entrees de chronologie (texte libre seulement)."""
    hist = doc.get("comment_history")
    if hist is None:
        hist = PH.parse_comment(doc.get("comment"), origin="sql")
    try:
        items = PH.decorate_history(hist, doc)
    except Exception:
        items = []
    closing = ""
    for e in items:
        if e.get("kind") == "status" and PH._is_closed_label(e.get("status_to")) and e.get("body"):
            closing = e["body"]
    comments = [e for e in items if e.get("kind") in ("comment", "status") and (e.get("body") or "").strip()]
    last = [{
        "ts": e.get("ts"),
        "operator": re.sub(r"\s*\[.*?\]\s*$", "", str(e.get("operator") or "")).strip(),
        "text": _excerpt(e.get("body"), 300),
    } for e in comments[-3:]]
    return {"closing_comment": _excerpt(closing, 400), "last_entries": last}


def build_precedents(col, doc, scope, allowed):
    """Liste des precedents serialises + meta (candidats, methode)."""
    t0 = time.time()
    cands, method = fetch_precedent_candidates(col, doc, scope, allowed)
    t_fetch = time.time()
    ranked = rank_precedents(doc, cands)
    ids = [r[1]["_id"] for r in ranked]
    full = {}
    if ids:
        for d in col.find({"_id": {"$in": ids}},
                          {"comment_history": 1, "comment": 1, "status_code": 1,
                           "close_ts": 1, "operator_close": 1}):
            full[d["_id"]] = d
    items = []
    for score, cand, reasons in ranked:
        extra_doc = dict(cand)
        extra_doc.update(full.get(cand["_id"], {}))
        info = closing_info(extra_doc)
        items.append(_short_item(cand, min(score, 1.0), {
            "edition": "%s %s" % (cand.get("event") or "", cand.get("year") or ""),
            "duration_min": _duration_min(extra_doc),
            "match": reasons,
            "closing_comment": info["closing_comment"],
            "last_entries": info["last_entries"],
        }))
    meta = {
        "candidates": len(cands), "method": method,
        "fetch_ms": int((t_fetch - t0) * 1000),
        "rank_ms": int((time.time() - t_fetch) * 1000),
    }
    return items, meta


def build_synthesis_prompts(doc, precedents):
    system = "\n".join([
        "Tu es l'assistant du PC Organisation (circuit du Mans). Un operateur traite "
        "une fiche de main courante et consulte des fiches similaires d'editions "
        "precedentes.",
        "Redige 3 a 5 puces en francais : ce qui a ete fait dans ces precedents et ce "
        "qui a fonctionne (delais, moyens, services contactes, issue).",
        "Regles strictes :",
        "- Appuie-toi UNIQUEMENT sur les precedents fournis. N'invente aucun fait, "
        "aucun moyen, aucun delai.",
        "- Cite l'edition entre parentheses a la fin de chaque puce, par exemple (24H AUTOS 2025).",
        "- Si les precedents sont peu comparables ou peu renseignes, dis-le clairement "
        "dans une puce et mets fiabilite a \"faible\".",
        "- Pas de recommandation generique sans appui dans les precedents.",
        "Reponds UNIQUEMENT par un objet JSON : "
        '{"points": ["...", "..."], "fiabilite": "faible" | "moyenne" | "bonne"}',
    ])
    lines = [
        "FICHE EN COURS",
        "Categorie : %s%s" % (doc.get("category") or "?",
                               (" / " + _doc_sous(doc)) if _doc_sous(doc) else ""),
        "Zone : %s" % (((doc.get("area") or {}).get("desc")) or "non renseignee"),
        "Texte : %s" % _excerpt(_doc_text(doc), 600),
        "",
        "PRECEDENTS",
    ]
    for i, p in enumerate(precedents, 1):
        lines.append("#%d - %s - %s" % (i, p.get("edition") or "", p.get("ts") or ""))
        lines.append("  Categorie : %s%s ; urgence : %s ; zone : %s" % (
            p.get("category") or "?",
            (" / " + p["sous_classification"]) if p.get("sous_classification") else "",
            p.get("niveau_urgence") or "non renseignee",
            p.get("area_desc") or "non renseignee"))
        lines.append("  Texte : %s" % (p.get("excerpt") or ""))
        dur = p.get("duration_min")
        lines.append("  Duree d'ouverture : %s" % ("%d min" % dur if dur is not None
                                                   else ("non close" if not p.get("status_closed") else "inconnue")))
        if p.get("closing_comment"):
            lines.append("  Commentaire de cloture : %s" % p["closing_comment"])
        for e in p.get("last_entries") or []:
            lines.append("  Chronologie (%s) : %s" % (e.get("ts") or "", e.get("text") or ""))
        lines.append("")
    return system, "\n".join(lines)


def parse_synthesis(text):
    obj = extract_json_object(text)
    points = []
    fiab = None
    if obj:
        raw = obj.get("points")
        if isinstance(raw, list):
            points = [" ".join(str(p).split()) for p in raw if str(p).strip()]
        f = obj.get("fiabilite")
        if isinstance(f, str) and f.strip().lower() in ("faible", "moyenne", "bonne"):
            fiab = f.strip().lower()
    if not points and text:
        for ln in str(text).splitlines():
            m = re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s+(.*\S)\s*$", ln)
            if m:
                points.append(m.group(1))
    points = [p[:500] for p in points[:6]]
    return {"points": points, "fiabilite": fiab or "moyenne"}


_synth_index_ok = False


def _synth_col(db):
    global _synth_index_ok
    col = db[COL_SYNTHESES]
    if not _synth_index_ok:
        try:
            col.create_index("created_at", expireAfterSeconds=SYNTHESIS_TTL_DAYS * 86400)
            col.create_index("fiche_id")
            _synth_index_ok = True
        except Exception as e:
            logger.warning("pcorg_assist : index %s (%s)", COL_SYNTHESES, e)
    return col


def synthesis_key(fiche_id, precedent_ids):
    raw = str(fiche_id) + "|" + ",".join(sorted(str(i) for i in precedent_ids))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _payload():
    return getattr(request, "user_payload", {}) or {}


def _parse_year(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@pcorg_assist_bp.route("/api/pcorg/assist/suggest", methods=["POST"])
@_role_required("user")
def assist_suggest():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()[:SUGGEST_MAX_CHARS]
    event = str(data.get("event") or "").strip()
    year = _parse_year(data.get("year"))
    if not event or year is None:
        return _err("event_year_requis")
    allowed = _allowed_categories(_payload())
    current = data.get("category") if isinstance(data.get("category"), str) else None
    area_desc = str(data.get("area_desc") or "").strip()[:200] or None
    lat, lng = data.get("lat"), data.get("lng", data.get("lon"))
    t0 = time.time()
    db = _db()

    duplicates = []
    if len(text) >= 10:
        try:
            cands = query_duplicate_candidates(db["pcorg"], event, year, allowed)
            duplicates = rank_duplicates(text, cands, lat=lat, lng=lng, area_desc=area_desc)
        except Exception as e:
            logger.warning("pcorg_assist doublons : %s", e)
    t_dup = time.time()

    suggestions, ai = None, "skipped"
    if len(text) >= SUGGEST_MIN_CHARS:
        u = _payload()
        suggestions, ai = suggest_classification(
            db, text, allowed, area_desc=area_desc, current_category=current,
            meta={"event": event, "year": year, "user": u.get("email", "")})

    return jsonify({
        "ok": True,
        "suggestions": suggestions,
        "ai": ai,
        "possible_duplicates": duplicates,
        "duplicates_window_h": DUPLICATE_WINDOW_H,
        "timing_ms": {"duplicates": int((t_dup - t0) * 1000),
                      "total": int((time.time() - t0) * 1000)},
    })


def _load_fiche_for_user(db, fiche_id):
    doc = db["pcorg"].find_one({"_id": fiche_id})
    if not doc:
        return None, _err("introuvable", 404)
    allowed = _allowed_categories(_payload())
    if allowed is not None and doc.get("category") not in allowed:
        return None, _err("categorie_non_autorisee", 403)
    return (doc, allowed), None


@pcorg_assist_bp.route("/api/pcorg/assist/similar/<fiche_id>", methods=["GET"])
@_role_required("user")
def assist_similar(fiche_id):
    scope = request.args.get("scope", "editions")
    if scope not in ("editions", "all"):
        return _err("scope_invalide")
    db = _db()
    res, err = _load_fiche_for_user(db, fiche_id)
    if err:
        return err
    doc, allowed = res
    t0 = time.time()
    try:
        items, meta = build_precedents(db["pcorg"], doc, scope, allowed)
    except Exception as e:
        logger.warning("pcorg_assist precedents : %s", e)
        return _err("recherche_impossible", 500)
    meta["elapsed_ms"] = int((time.time() - t0) * 1000)
    return jsonify({"ok": True, "fiche_id": str(doc["_id"]), "scope": scope,
                    "precedents": items, "meta": meta})


@pcorg_assist_bp.route("/api/pcorg/assist/similar/<fiche_id>/synthesis", methods=["POST"])
@_role_required("user")
def assist_synthesis(fiche_id):
    data = request.get_json(silent=True) or {}
    scope = data.get("scope") if data.get("scope") in ("editions", "all") else "editions"
    db = _db()
    res, err = _load_fiche_for_user(db, fiche_id)
    if err:
        return err
    doc, allowed = res

    items, _meta = build_precedents(db["pcorg"], doc, scope, allowed)
    wanted = data.get("precedent_ids")
    if isinstance(wanted, list) and wanted:
        wanted = {str(i) for i in wanted[:PRECEDENT_TOP]}
        items = [p for p in items if p["id"] in wanted]
    if not items:
        return _err("aucun_precedent", 404)

    ids = [p["id"] for p in items]
    key = synthesis_key(doc["_id"], ids)
    col = _synth_col(db)
    hit = col.find_one({"_id": key})
    if hit and not data.get("force"):
        return jsonify({"ok": True, "cached": True, "points": hit.get("points") or [],
                        "fiabilite": hit.get("fiabilite"), "model": hit.get("model"),
                        "precedent_ids": ids})

    if not pcorg_summary.ANTHROPIC_API_KEY:
        return _err("cle_api_absente", 503)
    system, user = build_synthesis_prompts(doc, items)
    model = pcorg_summary.CLAUDE_MODEL
    try:
        text, usage, stop = _claude_call(system, user, SYNTHESIS_MAX_TOKENS, model,
                                         db=db, effort=SYNTHESIS_EFFORT)
    except pcorg_summary.ClaudeError as e:
        if str(e) == "budget_exceeded":
            return _err("budget_ia_depasse", 429)
        return _err(str(e).split(":")[0] or "claude_error", 502)
    u = _payload()
    _record_usage(db, "pcorg_precedents", model, usage,
                  {"fiche_id": str(doc["_id"]), "event": doc.get("event"),
                   "year": doc.get("year"), "user": u.get("email", "")})
    parsed = parse_synthesis(text)
    if not parsed["points"]:
        return _err("synthese_illisible", 502)
    rec = {
        "_id": key, "fiche_id": str(doc["_id"]), "precedent_ids": ids,
        "points": parsed["points"], "fiabilite": parsed["fiabilite"],
        "model": model, "usage": usage, "stop_reason": stop,
        "created_at": datetime.now(timezone.utc),
        "created_by": u.get("email", ""),
    }
    try:
        col.replace_one({"_id": key}, rec, upsert=True)
    except Exception as e:
        logger.warning("pcorg_assist : cache synthese (%s)", e)
    return jsonify({"ok": True, "cached": False, "points": parsed["points"],
                    "fiabilite": parsed["fiabilite"], "model": model,
                    "precedent_ids": ids})
