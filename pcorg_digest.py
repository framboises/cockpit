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

import pcorg_enrich as PE

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

# Changer le texte du prompt = incrementer PROMPT_VERSION : toutes les fiches
# sont alors retraitees (les controles des admins sont conserves, et la
# nouvelle version est mesuree sur eux).
# v2 (27/10/2026) : gravite recalee sur les 35 premiers controles (routine et
# preventif = 1, tout feu >= 3, gaz / explosion = 5, test = 1) ; ajout du lieu
# cite, des heures des etapes, de l'equipement, des services exterieurs, des
# causes, de l'issue codee et des donnees personnelles reperees.
# v3 (27/10/2026) : nature de la fiche (incident / routine / information : 52 %
# de la base est le PC Surete, surtout des rondes), blocs ronde, alarme,
# dysfonctionnement, jalon, secours, prise de service, et sous-classification
# choisie dans la liste officielle de la categorie (pcorg_config).
PROMPT_VERSION = "v3"
CAUSES = ("alcool", "meteo", "affluence", "travaux", "panne_materiel", "signaletique",
          "conduite_dangereuse", "malveillance", "accident", "sante", "stationnement", "autre")
ISSUES = ("resolu", "transmis", "sans_suite", "en_attente", "non_precise")
DONNEES_PERSO = ("nom", "telephone", "plaque", "age", "sante", "adresse")
NATURES = ("incident", "routine", "information")
ACTIVITES = ("ronde", "ouverture_fermeture", "prise_service", "prestation", "consigne", "controle_acces", "autre")
# Blocs facultatifs : champ -> {sous-champ: valeurs admises (None = texte libre / liste / heure)}
BLOCS = {
    "ronde": {"debut": "hh", "fin": "hh", "points": "list", "resultat": ("ras", "anomalie"), "anomalies": "list"},
    "alarme": {"action": ("mise_hors_service", "remise_en_service", "les_deux"), "batiment": "text",
               "prestataire": "text"},
    "dysfonctionnement": {"type": ("communication", "documentation", "materiel", "effectif", "procedure", "autre"),
                          "description": "text"},
    "jalon": {"type": ("ouverture_portes", "fermeture_portes", "parking_complet", "evacuation", "debut_course",
                       "fin_course", "feu_vert", "autre"), "heure": "hh", "libelle": "text"},
    "secours": {"nature": ("malaise", "traumatisme", "chute", "coup_de_chaleur", "plaie", "douleur",
                           "intoxication", "autre"),
                "sexe": ("homme", "femme", "non_precise"),
                "tranche_age": ("enfant", "18-30", "30-50", "50-70", "70+", "non_precise"),
                "prise_en_charge": ("cms", "evacuation", "laisse_sur_place", "refus", "non_precise")},
    "prise_service": {"type": ("prise", "fin", "prise_et_fin"), "poste": "text", "debut": "hh", "fin": "hh",
                      "materiel": "list"},
}
NUM_PREDICT = 1200          # le modele alfred est regle a 500 en sortie : trop court pour la v3
PROMPT = """Tu analyses une fiche de main courante du PC Organisation du circuit des 24 Heures du Mans
(securite, secours, technique, flux, fourriere, information) pendant un evenement.

1. GRAVITE REELLE d'apres le deroule, pas d'apres le niveau d'urgence saisi (presque toujours absent ou faux) :
1 = routine, information, logistique ou dispositif preventif, sans incident : ouverture, pose de materiel,
    renfort ou deploiement preventif, consigne, enlevement de fourriere ou stationnement genant traite sans
    incident, essai ou fiche de test (texte incoherent)
2 = gene ou anomalie sans risque pour les personnes : panne, congestion, objet trouve ou perdu, demande de
    service, personnel bloque ou en attente (portail encore ferme), materiel tombe ou deplace (barrieres),
    vol simple sans violence ni menace (velo, objet)
3 = incident demandant une intervention, risque modere : TOUT DEPART DE FEU meme vite eteint, malaise pris en
    charge sans gravite, chute sans blessure grave, conflit verbal, intrusion sans suite, chute d'arbre sans
    victime, personne ou enfant egare, engin ou materiel dangereux (grue deployee, charge instable)
4 = atteinte aux personnes ou aux biens, ou risque serieux : bagarre, blesse (saignement abondant, ambulance),
    vol avec violence ou menaces, degradation volontaire, conduite dangereuse, feu demandant des moyens importants
5 = danger vital ou crise : arret cardiaque, blesse grave, incendie non maitrise, FUITE DE GAZ ou RISQUE
    D'EXPLOSION, mouvement de foule, evacuation
Exemples corriges par le PC : « Enlevement fourriere Bd des Italiens, termine, paiement carte » = 1 ;
« Porte Nord, renfort filtrage, ouverture de couloirs supplementaires » = 1 ; « Personnel arrive en poste mais
portail encore ferme » = 2 ; « Vol de velo au paddock » = 2 ; « Barrieres tombees chemin aux Boeufs » = 2 ;
« Feu de poubelle eteint a l'extincteur par la patrouille » = 3 ; « Grue deployee sur un parking » = 3 ;
« Femme, saignement abondant, prise en charge CMS puis ambulance » = 4 ; « Fuite sur bouteille de gaz,
panache, non maitrisable » = 5.

2. FAITS (n'invente rien ; ce qui n'est pas ecrit vaut "non precise", null ou une liste vide) :
- lieu : le lieu cite, tel qu'ecrit (ex. "T34", "Porte Nord", "P4D", "giratoire Antares", "Welcome")
- etapes : heures HH:MM lues dans le deroule pour signalement, engagement des moyens, arrivee sur place, fin
- equipement : l'equipement ou materiel en cause (ex. "pompe a essence", "ecran geant", "barriere"), sinon "aucun"
- moyens_engages : liste des EQUIPES ou moyens internes cites (ex. "S3M", "PS Nord", "patrouille 4", "Tangos",
  "CMS") ; JAMAIS un nom de personne ni l'auteur d'une ligne du deroule
- services_exterieurs : liste des services exterieurs sollicites (ex. "SDIS", "SAMU", "gendarmerie", "police",
  "ENEDIS", "depanneuse"), sinon []
- causes : liste parmi alcool, meteo, affluence, travaux, panne_materiel, signaletique, conduite_dangereuse,
  malveillance, accident, sante, stationnement, autre (vide si rien n'est dit)
- issue_code : resolu, transmis, sans_suite, en_attente ou non_precise
- donnees_personnelles : types presents dans le texte parmi nom, telephone, plaque, age, sante, adresse

3. NATURE de la fiche :
- "incident" : evenement anormal qui demande une action (meme mineur)
- "routine" : activite prevue (ronde, ouverture ou fermeture, prise ou fin de service, prestation de
  nettoyage, consigne, controle d'acces). Une routine SANS anomalie a la gravite 1.
- "information" : message, point de situation, decision ou consigne du PC, sans action
activite : ronde, ouverture_fermeture, prise_service, prestation, consigne, controle_acces, autre, ou null

4. BLOCS : remplir SEULEMENT ceux qui concernent la fiche, sinon null.
- ronde : {"debut": "HH:MM", "fin": "HH:MM", "points": [batiments ou lieux controles, dans l'ordre],
  "resultat": "ras" ou "anomalie", "anomalies": [anomalies trouvees]}
- alarme (mise hors service / remise en service d'une alarme intrusion) : {"action": "mise_hors_service",
  "remise_en_service" ou "les_deux", "batiment": "...", "prestataire": "societe concernee ou non precise"}
- dysfonctionnement (rate d'organisation a retenir pour le retour d'experience) : {"type": "communication"
  (service injoignable, info non transmise), "documentation" (plan ou consigne faux), "materiel", "effectif",
  "procedure" ou "autre", "description": "une phrase, sans nom de personne"}
- jalon (etape d'exploitation de l'evenement) : {"type": "ouverture_portes", "fermeture_portes",
  "parking_complet", "evacuation", "debut_course", "fin_course", "feu_vert" ou "autre", "heure": "HH:MM",
  "libelle": "..."}
- secours (victime) : {"nature": "malaise", "traumatisme", "chute", "coup_de_chaleur", "plaie", "douleur",
  "intoxication" ou "autre", "sexe": "homme", "femme" ou "non_precise", "tranche_age": "enfant", "18-30",
  "30-50", "50-70", "70+" ou "non_precise", "prise_en_charge": "cms", "evacuation", "laisse_sur_place",
  "refus" ou "non_precise"}
- prise_service : {"type": "prise", "fin" ou "prise_et_fin", "poste": "poste ou porte, sans nom de personne",
  "debut": "HH:MM", "fin": "HH:MM", "materiel": [materiel remis ou rendu]}
- sous_classification : la valeur EXACTE de la liste fournie avec la fiche qui correspond le mieux, ou null

Reponds UNIQUEMENT par un objet JSON :
{"gravite": 1 a 5, "justification": "pourquoi ce niveau, 20 mots maximum",
 "type": "nature de l'incident en quelques mots", "victimes": nombre (0 si aucune ou non precise),
 "lieu": "...", "etapes": {"signalement": "HH:MM" ou null, "engagement": "HH:MM" ou null,
 "sur_place": "HH:MM" ou null, "fin": "HH:MM" ou null}, "equipement": "...",
 "moyens_engages": [...], "services_exterieurs": [...], "causes": [...],
 "issue": "...", "issue_code": "...", "donnees_personnelles": [...],
 "nature": "...", "activite": "..." ou null, "ronde": {...} ou null, "alarme": {...} ou null,
 "dysfonctionnement": {...} ou null, "jalon": {...} ou null, "secours": {...} ou null,
 "prise_service": {...} ou null, "sous_classification": "..." ou null,
 "resume": "deux phrases maximum, sans nom de personne, telephone ni plaque"}

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
# content_category sert au carroyage (pcorg_enrich) ; source_text n'en envoie
# JAMAIS l'appelant ni le telephone au modele.
SOURCE_PROJECTION = {"text": 1, "comment": 1, "category": 1, "niveau_urgence": 1,
                     "event": 1, "year": 1, "ts": 1, "date_local": 1, "time_local": 1,
                     "area": 1, "gps": 1, "sql_id": 1,
                     "content_category.carroye": 1, "content_category.classification": 1,
                     "content_category.motif_intervention": 1, "content_category.sous_classification": 1,
                     "content_category.service_contacte": 1, "content_category.relance": 1,
                     "content_category.intervenant1": 1, "content_category.intervenant2": 1,
                     "content_category.intervenant3": 1, "content_category.intervenant4": 1,
                     "content_category.intervenant5": 1}


def load_sous_classifications(db):
    """{categorie: [libelles officiels]} depuis pcorg_config (doublons d'accents retires)."""
    doc = db["pcorg_config"].find_one({"_id": "pcorg_lists"}, {"sous_classifications": 1}) or {}
    out = {}
    for cat, items in (doc.get("sous_classifications") or {}).items():
        seen, labels = set(), []
        for it in items or []:
            lab = str((it or {}).get("label") or "").strip()
            if lab and PE.norm(lab) not in seen:
                seen.add(PE.norm(lab))
                labels.append(lab)
        out[cat] = labels
    return out


def source_text(doc, sc_list=None):
    area = doc.get("area")
    area = (area.get("desc") or area.get("name")) if isinstance(area, dict) else area
    lines = [
        "Evenement : %s %s" % (doc.get("event") or "", doc.get("year") or ""),
        "Date : %s %s" % (doc.get("date_local") or "", doc.get("time_local") or ""),
        "Categorie : %s" % (doc.get("category") or "non precisee"),
        "Urgence saisie : %s" % (doc.get("niveau_urgence") or "aucune"),
    ]
    cc = doc.get("content_category") or {}
    for label, key in (("Classification", "classification"), ("Motif", "motif_intervention"),
                       ("Sous-classification saisie", "sous_classification")):
        if cc.get(key):
            lines.append("%s : %s" % (label, cc[key]))
    if area and str(area).strip() not in ("_MC PCS", "_MC PCO"):    # zones generiques : pas un lieu
        lines.append("Zone : %s" % area)
    lines.append("Titre : %s" % (doc.get("text") or "").strip())
    lines.append("Deroule :\n%s" % (doc.get("comment") or "").strip())
    body = "\n".join(lines)[:SOURCE_MAX_CHARS]
    if sc_list:
        body += "\n\nSous-classifications possibles pour cette categorie : %s" % " ; ".join(sc_list)
    else:
        body += "\n\nPas de liste de sous-classifications pour cette categorie : sous_classification = null"
    return body


def src_hash(doc):
    raw = "\x1f".join(str(doc.get(k) or "") for k in ("text", "comment", "category", "niveau_urgence"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Appel du modele et controle de la reponse
# ---------------------------------------------------------------------------
_TEXT_FIELDS = ("justification", "type", "lieu", "equipement", "issue", "resume")
_LIST_FIELDS = ("moyens_engages", "services_exterieurs")


def _clean_text(v, default="non precise", limit=600):
    s = re.sub(r"\s+", " ", str(v if v not in (None, "") else default)).strip()
    return s[:limit] or default


# Sigles d'equipes ou de services, jamais des noms de famille
_ACRONYMS = {"SDIS", "SAMU", "SMUR", "CMS", "PCO", "PCS", "S3M", "ENEDIS", "GRDF", "BPS", "SSIAP", "ACO",
             "CRS", "DDT", "DIR", "SNCF", "SETRAM", "TAXI", "VIP", "SECU", "TECH"}


def _looks_like_person(s):
    """« Prenom NOM », « NOM Prenom » (2 ou 3 mots), comptes « field:x » ou
    « a.dupont » : une personne, pas une equipe (« PS Nord », « SDIS Sarthe »
    et « Patrouille 4 » restent)."""
    s = s.strip()
    if s.lower().startswith("field:") or re.match(r"^[a-z]\.[a-z-]+", s):
        return True
    words = s.split()
    if len(words) not in (2, 3):
        return False
    caps = [w for w in words if re.fullmatch(r"[A-Z][A-Z'-]{2,}", w)]
    title = [w for w in words if re.fullmatch(r"[A-Z][a-zà-ÿ'-]+", w)]
    if not caps or not title or len(caps) + len(title) != len(words):
        return False
    return not any(w in _ACRONYMS for w in caps)


def _clean_list(v, allowed=None, limit=12, drop_persons=False):
    if isinstance(v, str):
        v = [x for x in re.split(r"[,;/]", v) if x.strip()]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        s = re.sub(r"\s+", " ", str(x or "")).strip()
        if not s or s.lower() in ("non precise", "aucun", "aucune", "none", "null"):
            continue
        if drop_persons and _looks_like_person(s):
            continue
        if allowed is not None:
            s = s.lower().replace(" ", "_")
            if s not in allowed:
                continue
        if s not in out:
            out.append(s[:80])
    return out[:limit]


def _clean_hhmm(v):
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:hH]\s*(\d{2})\s*", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return "%02d:%s" % (int(m.group(1)), m.group(2))


def _clean_bloc(v, spec):
    """Bloc facultatif (ronde, alarme...) -> dict controle, ou None s'il est vide."""
    if not isinstance(v, dict):
        return None
    out, useful = {}, False
    for k, kind in spec.items():
        x = v.get(k)
        if kind == "hh":
            out[k] = _clean_hhmm(x)
        elif kind == "list":
            out[k] = _clean_list(x, drop_persons=True)
        elif kind == "text":
            out[k] = _clean_text(x, limit=300)
            if out[k] == "non precise":
                del out[k]
                continue
        else:                                     # valeurs admises
            s = str(x or "").strip().lower().replace(" ", "_")
            out[k] = s if s in kind else None
        if out[k] not in (None, [], "non_precise"):
            useful = True
    return out if useful else None


def parse_reply(raw, sc_allowed=None):
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
        out[k] = _clean_text(data.get(k))
    for k in _LIST_FIELDS:
        out[k] = _clean_list(data.get(k), drop_persons=True)
    out["causes"] = _clean_list(data.get("causes"), CAUSES)
    out["donnees_personnelles"] = _clean_list(data.get("donnees_personnelles"), DONNEES_PERSO)
    code = str(data.get("issue_code") or "").strip().lower().replace(" ", "_")
    out["issue_code"] = code if code in ISSUES else "non_precise"
    et = data.get("etapes") if isinstance(data.get("etapes"), dict) else {}
    out["etapes"] = {k: _clean_hhmm(et.get(k)) for k in ("signalement", "engagement", "sur_place", "fin")}
    nat = str(data.get("nature") or "").strip().lower()
    out["nature"] = nat if nat in NATURES else None
    act = str(data.get("activite") or "").strip().lower().replace(" ", "_")
    out["activite"] = act if act in ACTIVITES else None
    for bloc, spec in BLOCS.items():
        out[bloc] = _clean_bloc(data.get(bloc), spec)
    # Sous-classification : seulement une valeur EXACTE de la liste officielle (accents pres)
    sc = str(data.get("sous_classification") or "").strip()
    match = None
    if sc and sc_allowed:
        match = next((x for x in sc_allowed if PE.norm(x) == PE.norm(sc)), None)
    out["sous_classification_proposee"] = match
    return out


def analyze(doc, session=None, sc_lists=None):
    """(resultat, meta) pour une fiche. Leve en cas d'echec reseau / reponse.
    num_ctx n'est PAS envoye : le changer forcerait Ollama a recharger le
    modele partage avec le chat Alfred (charge en 16 384)."""
    s = session or requests
    sc_list = (sc_lists or {}).get(doc.get("category"))
    t0 = time.time()
    r = s.post(OLLAMA_URL + "/api/generate", timeout=OLLAMA_TIMEOUT, json={
        "model": OLLAMA_MODEL, "prompt": PROMPT + source_text(doc, sc_list), "stream": False,
        "format": "json", "options": {"temperature": 0, "num_predict": NUM_PREDICT}, "keep_alive": "30m"})
    r.raise_for_status()
    body = r.json()
    if body.get("done_reason") == "length":
        raise ValueError("reponse coupee (num_predict atteint)")
    res = parse_reply(body.get("response") or "", sc_list)
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
    """Fiche a (re)traiter : jamais structuree, texte modifie, ou consignes plus
    recentes. Une fiche controlee est retraitee seulement pour une nouvelle
    version des consignes (nouveaux champs) : son controle est conserve et sert
    a mesurer la nouvelle version ; jamais pour un simple renvoi d'echec."""
    if digest is None:
        return True
    if digest.get("review"):
        return digest.get("prompt_version") != PROMPT_VERSION
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
    gaz = PE.Gazetteer(db)
    taxo = PE.Taxonomy(db)
    sc_lists = load_sous_classifications(db)
    touched = set()
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
                res, meta = analyze(doc, sess, sc_lists)
                res.update(PE.enrich(gaz, doc, res, taxo))
                db[COLL].update_one({"_id": doc["_id"]}, {
                    "$set": dict(base, **res, meta=meta, error=None, attempts=0)}, upsert=True)
                touched.add((doc.get("event"), doc.get("year")))
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
        dups = alarms = 0
        for ev, yr in touched:          # par evenement touche (code pur, rapide)
            dups += PE.link_duplicates(db, COLL, ev, yr)
            alarms += PE.flag_unrestored_alarms(db, COLL, ev, yr)
            PE.resolve_declared_duplicates(db, COLL, ev, yr)
        if touched:
            log.info("Doublons probables : %d ; alarmes sans remise en service : %d", dups, alarms)
    finally:
        release_lock(db, {"done": done, "errors": errors, "stop_reason": reason})
    dur = (_now() - started).total_seconds()
    log.info("Fin %s : %d structurees, %d echecs, %.0f s (%s)", run_id, done, errors, dur, reason)
    return {"run_id": run_id, "done": done, "errors": errors, "reason": reason, "seconds": dur}


def enrich_only(db):
    """Recalcule les champs « code pur » (position, zone, delais, completude,
    doublons) de toutes les fiches deja structurees, sans le modele."""
    gaz = PE.Gazetteer(db)
    taxo = PE.Taxonomy(db)
    n, touched = 0, set()
    for d in db[COLL].find({"error": None, "gravite": {"$ne": None}}):
        fiche = db["pcorg"].find_one({"_id": d["_id"]}, SOURCE_PROJECTION)
        if not fiche:
            continue
        db[COLL].update_one({"_id": d["_id"]}, {"$set": PE.enrich(gaz, fiche, d, taxo)})
        touched.add((d.get("event"), d.get("year")))
        n += 1
    dups = sum(PE.link_duplicates(db, COLL, ev, yr) for ev, yr in touched)
    for ev, yr in touched:
        PE.flag_unrestored_alarms(db, COLL, ev, yr)
        PE.resolve_declared_duplicates(db, COLL, ev, yr)
    log.info("Enrichissement seul : %d fiches, %d doublons probables", n, dups)
    return {"enriched": n, "duplicates": dups}


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
    p.add_argument("--enrich-only", action="store_true",
                   help="recalcule position / zone / delais / doublons sans le modele")
    a = p.parse_args(argv)
    if a.enrich_only:
        _setup_logging()
        enrich_only(_db())
        return 0
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
