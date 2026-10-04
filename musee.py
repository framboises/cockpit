"""musee.py - Frequentation du Musee des 24 Heures du Mans (helpers purs).

Bloc AUTONOME : ni live-controle (data_access ___GLOBAL___, hsh_structure,
hsh_transactions_agg), ni Waze. Alimente par scripts/musee_collect.py (tache
planifiee toutes les 5 min) qui interroge la borne Handshake via musee_borne.py
(fonctions reseau de live_controle.py), sur sa PROPRE connexion TCP.

AUCUNE valeur operationnelle n'est codee ici : ni identifiants Handshake, ni
noms de lieux, ni horaires. Tout vient de cockpit_settings {_id: "musee"},
edite dans la page /live-controle, onglet Musee (musee_api.py). Sans document
(ou sans Area choisie), le musee est "a configurer" : le collecteur sort
proprement et le bloc de l'accueil l'affiche.

Schema du document (schema = 2) :
  enabled            bool   collecte + bloc
  horaires           {ouverture, fermeture,             horaires par defaut
                      semaine: {lun..dim: {ferme: true} | {ouverture, fermeture}},
                      exceptions: [{date, ferme | ouverture+fermeture, libelle}]}
  area               {id, nom}      Area Handshake = perimetre des visiteurs
  gates              [{id, nom}]    portes de l'Area (compteurs releves)
  checkpoints        [{id, nom, libelle, inclus, mobile}]
  transactions       bool   collecte des passages (heure exacte)
  releve_perime_min  int    au-dela : "releve perime", jamais 0
  statuts_passage    [str]  statuts de transaction comptes comme un passage

Les compteurs sont ENTREE SEULE (exits toujours 0, current = cumul jamais remis
a zero) : le chiffre utile est le nombre de VISITEURS DU JOUR depuis
l'ouverture, jamais un "presents". Un checkpoint "mobile" peut servir ailleurs
(ses compteurs ne sont pas que du musee) : les passages sont filtres sur
l'AREA, jamais sur le checkpoint.

Collections (toutes prefixees musee_, rien d'autre n'est ecrit) :
  musee_releves  {ts (UTC), date "YYYY-MM-DD" Paris, heure "HH:MM" Paris,
                  compteurs: {loc_id: {type, nom, entries, exits, current}},
                  erreurs: {loc_id: msg}}                        TTL 400 j
  musee_passages {_id: transaction_id, ts (UTC), date, heure "HH:MM:SS",
                  checkpoint_id, checkpoint_nom, gate_id, area_id, status,
                  releases}                                       TTL 400 j
  musee_jours    {_id: date, visiteurs, source, par_heure{}, par_checkpoint{},
                  pic_heure, ... + etat de collecte tx_depuis / tx_jusqu_a}

Second perimetre "Site - visites libres" (02/10/2026) : un billet musee donne
acces libre au site ; les visiteurs entrent ET sortent par des checkpoints
choisis (n'importe quelle Area, ex. la porte Nord bis de l'enceinte). Ces
checkpoints servent de delestage pendant les epreuves : on ne compte QUE les
jours publics du parametrage SAISON (event_courant.saison_public_days, saisis
dans GroundMaster), dans leurs horaires +/- `site.marge_min`, et JAMAIS
pendant une epreuve active. Document : cle `site`
  site  {enabled, marge_min, checkpoints: [{id, nom, libelle, inclus, sens}]}
        sens = mixte | entree | sortie (indication : sert si la transaction
        ne porte pas de sens)
Collections : musee_site_releves (compteurs entries ET exits, TTL 400 j),
musee_site_passages (transactions des checkpoints, sens E/S, TTL 400 j),
musee_site_jours (entrees, sorties, presents_now, presents_max, par_heure).
"""

from __future__ import annotations

import datetime as _dt
import re
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
UTC = _dt.timezone.utc

SETTINGS_ID = "musee"
SCHEMA = 2
COL_RELEVES = "musee_releves"
COL_PASSAGES = "musee_passages"
COL_JOURS = "musee_jours"
COL_SITE_RELEVES = "musee_site_releves"
COL_SITE_PASSAGES = "musee_site_passages"
COL_SITE_JOURS = "musee_site_jours"

RETENTION_JOURS = 400          # N-1 a la meme date reste comparable
TX_RECOUVREMENT_MIN = 10       # chevauchement des fenetres transactions

# Valeurs par defaut des reglages TECHNIQUES (pas des donnees du musee) quand
# le document ne les porte pas : modifiables dans l'onglet Musee.
# Statuts : ceux que les compteurs Area Skidata comptent comme un passage
# (verifie par reconciliation dans scan_import_hsh.py) : OK, hors ligne,
# entree sans sortie.
DEFAUT_STATUTS_PASSAGE = ("0", "107", "133")
DEFAUT_RELEVE_PERIME_MIN = 15
DEFAUT_TRANSACTIONS = True
DEFAUT_SITE_MARGE_MIN = 30         # sorties tardives / arrivees en avance
SITE_MARGE_MAX = 240
SITE_SENS = ("mixte", "entree", "sortie")
SITE_LIBELLE = "Site - visites libres"

JOURS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")   # date.weekday()
JOURS_LIBELLES = {"lun": "lundi", "mar": "mardi", "mer": "mercredi", "jeu": "jeudi",
                  "ven": "vendredi", "sam": "samedi", "dim": "dimanche"}
LOC_TYPES = ("Area", "Gate", "Checkpoint")

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^\d{1,9}$")
_STATUT_RE = re.compile(r"^\d{1,4}$")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def hhmm_ok(v):
    return isinstance(v, str) and bool(_HHMM_RE.match(v))


def date_ok(v):
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return False
    try:
        _dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def migrate_legacy(doc):
    """Document schema 1 (ouverture/fermeture/jours_fermes/area_id/locations a
    plat) -> schema 2. Pur et idempotent : un document deja au schema 2 est
    rendu tel quel. Ne perd aucune valeur (les champs v1 sont recopies sous
    migration.v1 par l'appelant qui ecrit)."""
    if not isinstance(doc, dict):
        return doc
    if doc.get("schema") == SCHEMA:
        return dict(doc)
    out = {k: v for k, v in doc.items()
           if k not in ("ouverture", "fermeture", "jours_fermes", "area_id", "locations")}
    out["schema"] = SCHEMA
    out["enabled"] = bool(doc.get("enabled", True))
    out["horaires"] = {
        "ouverture": doc.get("ouverture"),
        "fermeture": doc.get("fermeture"),
        "semaine": {},
        "exceptions": [{"date": d, "ferme": True, "libelle": ""}
                       for d in (doc.get("jours_fermes") or []) if date_ok(d)],
    }
    locs = [x for x in (doc.get("locations") or []) if isinstance(x, dict) and x.get("id")]
    area_id = str(doc.get("area_id") or "")
    area_nom = ""
    for x in locs:
        if str(x["id"]) == area_id or x.get("role") == "area":
            area_id = area_id or str(x["id"])
            area_nom = x.get("nom") or ""
            break
    out["area"] = {"id": area_id, "nom": area_nom} if area_id else None
    out["gates"] = [{"id": str(x["id"]), "nom": x.get("nom") or ""}
                    for x in locs if x.get("role") == "gate" or x.get("type") == "Gate"]
    out["checkpoints"] = [{"id": str(x["id"]), "nom": x.get("nom") or "", "libelle": "",
                           "inclus": True, "mobile": bool(x.get("mobile"))}
                          for x in locs if x.get("role") == "checkpoint" or x.get("type") == "Checkpoint"]
    out.setdefault("transactions", doc.get("transactions", DEFAUT_TRANSACTIONS))
    out.setdefault("releve_perime_min", DEFAUT_RELEVE_PERIME_MIN)
    out.setdefault("statuts_passage", list(DEFAUT_STATUTS_PASSAGE))
    return out


def normalize_config(doc):
    """Document (schema 1 ou 2) -> config de travail, ou None sans document.

    Tolerant (lecture) : un champ invalide est ignore, jamais d'exception.
    `configure` est faux tant qu'il manque l'Area ou des horaires par defaut.
    `locations` (Area + portes + checkpoints inclus, avec role) est ce que
    lisent le collecteur et compute_day."""
    if not isinstance(doc, dict):
        return None
    d = migrate_legacy(doc)
    hor = d.get("horaires") if isinstance(d.get("horaires"), dict) else {}
    semaine = {}
    for j, v in (hor.get("semaine") or {}).items():
        if j not in JOURS or not isinstance(v, dict):
            continue
        if v.get("ferme"):
            semaine[j] = {"ferme": True}
        elif hhmm_ok(v.get("ouverture")) and hhmm_ok(v.get("fermeture")) \
                and v["ouverture"] < v["fermeture"]:
            semaine[j] = {"ouverture": v["ouverture"], "fermeture": v["fermeture"]}
    exceptions = []
    for e in hor.get("exceptions") or []:
        if not isinstance(e, dict) or not date_ok(e.get("date")):
            continue
        lib = str(e.get("libelle") or "")[:80]
        if e.get("ferme"):
            exceptions.append({"date": e["date"], "ferme": True, "libelle": lib})
        elif hhmm_ok(e.get("ouverture")) and hhmm_ok(e.get("fermeture")) \
                and e["ouverture"] < e["fermeture"]:
            exceptions.append({"date": e["date"], "ferme": False, "ouverture": e["ouverture"],
                               "fermeture": e["fermeture"], "libelle": lib})
    exceptions.sort(key=lambda e: e["date"])
    ouv, ferm = hor.get("ouverture"), hor.get("fermeture")
    horaires_ok = hhmm_ok(ouv) and hhmm_ok(ferm) and ouv < ferm
    horaires = {"ouverture": ouv if horaires_ok else None,
                "fermeture": ferm if horaires_ok else None,
                "semaine": semaine, "exceptions": exceptions}

    area = d.get("area") if isinstance(d.get("area"), dict) else {}
    area_id = str(area.get("id") or "")
    area_nom = str(area.get("nom") or area_id)
    gates = [{"id": str(g["id"]), "nom": str(g.get("nom") or g["id"])}
             for g in (d.get("gates") or []) if isinstance(g, dict) and g.get("id")]
    cps = []
    for c in d.get("checkpoints") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        nom = str(c.get("nom") or c["id"])
        cps.append({"id": str(c["id"]), "nom": nom, "libelle": str(c.get("libelle") or ""),
                    "inclus": c.get("inclus", True) is not False, "mobile": bool(c.get("mobile"))})

    locations = []
    if area_id:
        locations.append({"id": area_id, "type": "Area", "nom": area_nom, "role": "area",
                          "mobile": False})
    for g in gates:
        locations.append({"id": g["id"], "type": "Gate", "nom": g["nom"], "role": "gate",
                          "mobile": False})
    for c in cps:
        if c["inclus"]:
            locations.append({"id": c["id"], "type": "Checkpoint",
                              "nom": c["libelle"] or c["nom"], "role": "checkpoint",
                              "mobile": c["mobile"]})

    statuts = [str(s) for s in (d.get("statuts_passage") or []) if _STATUT_RE.match(str(s))]
    try:
        perime = int(d.get("releve_perime_min") or DEFAUT_RELEVE_PERIME_MIN)
    except (TypeError, ValueError):
        perime = DEFAUT_RELEVE_PERIME_MIN
    perime = min(max(perime, 1), 1440)
    tx = d.get("transactions")
    return {
        "site": normalize_site(d.get("site")),
        "configure": bool(area_id) and horaires_ok,
        "enabled": d.get("enabled", True) is not False,
        "horaires": horaires,
        "area_id": area_id,
        "area_nom": area_nom,
        "gates": gates,
        "checkpoints": cps,
        "locations": locations,
        "transactions": DEFAUT_TRANSACTIONS if tx is None else bool(tx),
        "releve_perime_min": perime,
        "releve_perime_s": perime * 60,
        "statuts_passage": statuts or list(DEFAUT_STATUTS_PASSAGE),
    }


def normalize_site(raw):
    """Cle `site` du document -> config de travail du perimetre visites libres
    (tolerant). `configure` : active et au moins un checkpoint inclus.
    `locations` : checkpoints inclus (lus par le collecteur)."""
    s = raw if isinstance(raw, dict) else {}
    try:
        marge = int(s.get("marge_min", DEFAUT_SITE_MARGE_MIN))
    except (TypeError, ValueError):
        marge = DEFAUT_SITE_MARGE_MIN
    marge = min(max(marge, 0), SITE_MARGE_MAX)
    cps = []
    vus = set()
    for c in s.get("checkpoints") or []:
        if not isinstance(c, dict) or not c.get("id") or str(c["id"]) in vus:
            continue
        vus.add(str(c["id"]))
        sens = c.get("sens") if c.get("sens") in SITE_SENS else "mixte"
        cps.append({"id": str(c["id"]), "nom": str(c.get("nom") or c["id"]),
                    "libelle": str(c.get("libelle") or ""),
                    "inclus": c.get("inclus", True) is not False, "sens": sens})
    locations = [{"id": c["id"], "type": "Checkpoint", "nom": c["libelle"] or c["nom"],
                  "role": "site", "mobile": False, "sens": c["sens"]}
                 for c in cps if c["inclus"]]
    enabled = s.get("enabled") is True
    return {"enabled": enabled, "configure": enabled and bool(locations),
            "marge_min": marge, "checkpoints": cps, "locations": locations,
            "libelle": SITE_LIBELLE}


def get_config(db):
    """Config de travail (lecture seule), None si le document n'existe pas."""
    try:
        doc = db["cockpit_settings"].find_one({"_id": SETTINGS_ID})
    except Exception:
        doc = None
    return normalize_config(doc)


def ensure_config(db):
    """Compatibilite : lecture seule (plus aucune creation avec des valeurs
    codees en dur)."""
    return get_config(db)


def validate_config(payload, known=None):
    """Valide strictement le formulaire de l'onglet Musee.

    `known` : {id: {"type", "nom"}} des locations connues (structure borne ou
    archive) ; None = pas de controle d'existence. Retourne (doc, erreurs) ;
    doc est le document schema 2 a enregistrer (sans _id) quand erreurs est
    vide."""
    errs = []
    if not isinstance(payload, dict):
        return None, ["corps JSON attendu"]

    def _bool(name, default):
        v = payload.get(name, default)
        if not isinstance(v, bool):
            errs.append("%s : booleen attendu" % name)
            return default
        return v

    enabled = _bool("enabled", True)
    transactions = _bool("transactions", DEFAUT_TRANSACTIONS)

    # --- Horaires ---------------------------------------------------------
    hor = payload.get("horaires")
    if not isinstance(hor, dict):
        errs.append("horaires manquants")
        hor = {}

    def _plage(v, ctx):
        o, f = v.get("ouverture"), v.get("fermeture")
        if not hhmm_ok(o):
            errs.append("%s : ouverture invalide (HH:MM)" % ctx)
            return None
        if not hhmm_ok(f):
            errs.append("%s : fermeture invalide (HH:MM)" % ctx)
            return None
        if f <= o:
            errs.append("%s : la fermeture doit suivre l'ouverture" % ctx)
            return None
        return {"ouverture": o, "fermeture": f}

    defaut = _plage(hor, "horaires par defaut") or {"ouverture": None, "fermeture": None}
    semaine = {}
    raw_sem = hor.get("semaine") or {}
    if not isinstance(raw_sem, dict):
        errs.append("semaine : objet attendu")
        raw_sem = {}
    for j, v in raw_sem.items():
        if j not in JOURS:
            errs.append("semaine : jour inconnu %r" % j)
            continue
        if v is None:
            continue                                  # = horaires par defaut
        if not isinstance(v, dict):
            errs.append("%s : objet attendu" % JOURS_LIBELLES[j])
            continue
        if v.get("ferme") is True:
            semaine[j] = {"ferme": True}
        else:
            p = _plage(v, JOURS_LIBELLES[j])
            if p:
                semaine[j] = p
    exceptions, vues = [], set()
    raw_exc = hor.get("exceptions") or []
    if not isinstance(raw_exc, list):
        errs.append("exceptions : liste attendue")
        raw_exc = []
    if len(raw_exc) > 400:
        errs.append("exceptions : 400 dates au plus")
        raw_exc = raw_exc[:400]
    for e in raw_exc:
        if not isinstance(e, dict) or not date_ok(e.get("date")):
            errs.append("exception : date invalide (AAAA-MM-JJ) %r"
                        % (e.get("date") if isinstance(e, dict) else e))
            continue
        if e["date"] in vues:
            errs.append("exception %s : date en double" % e["date"])
            continue
        vues.add(e["date"])
        lib = e.get("libelle") or ""
        if not isinstance(lib, str) or len(lib) > 80:
            errs.append("exception %s : libelle de 80 caracteres au plus" % e["date"])
            lib = ""
        if e.get("ferme") is True:
            exceptions.append({"date": e["date"], "ferme": True, "libelle": lib.strip()})
        else:
            p = _plage(e, "exception " + e["date"])
            if p:
                exceptions.append({"date": e["date"], "ferme": False, "libelle": lib.strip(), **p})
    exceptions.sort(key=lambda x: x["date"])

    # --- Perimetre Handshake ----------------------------------------------
    def _loc_ok(lid, typ, ctx):
        if not isinstance(lid, str) or not _ID_RE.match(lid):
            errs.append("%s : identifiant invalide %r" % (ctx, lid))
            return False
        if known is not None:
            k = known.get(lid)
            if not k:
                errs.append("%s %s : absent de la structure Handshake" % (ctx, lid))
                return False
            if k.get("type") != typ:
                errs.append("%s %s : est un(e) %s, pas un(e) %s" % (ctx, lid, k.get("type"), typ))
                return False
        return True

    area = payload.get("area")
    area_doc = None
    if not isinstance(area, dict) or not area.get("id"):
        errs.append("Area du musee a choisir")
    else:
        aid = str(area["id"])
        if _loc_ok(aid, "Area", "Area"):
            nom = (known or {}).get(aid, {}).get("nom") or str(area.get("nom") or aid)
            area_doc = {"id": aid, "nom": nom[:80]}

    gates, seen = [], set()
    for g in payload.get("gates") or []:
        gid = str((g or {}).get("id") or "") if isinstance(g, dict) else ""
        if gid in seen:
            continue
        if _loc_ok(gid, "Gate", "Porte"):
            seen.add(gid)
            nom = (known or {}).get(gid, {}).get("nom") or str(g.get("nom") or gid)
            gates.append({"id": gid, "nom": nom[:80]})

    cps = []
    raw_cps = payload.get("checkpoints") or []
    if not isinstance(raw_cps, list):
        errs.append("checkpoints : liste attendue")
        raw_cps = []
    for c in raw_cps:
        cid = str((c or {}).get("id") or "") if isinstance(c, dict) else ""
        if cid in seen:
            continue
        if not _loc_ok(cid, "Checkpoint", "Checkpoint"):
            continue
        seen.add(cid)
        lib = c.get("libelle") or ""
        if not isinstance(lib, str) or len(lib) > 60:
            errs.append("Checkpoint %s : nom affiche de 60 caracteres au plus" % cid)
            lib = ""
        for b in ("inclus", "mobile"):
            if b in c and not isinstance(c[b], bool):
                errs.append("Checkpoint %s : %s booleen attendu" % (cid, b))
        nom = (known or {}).get(cid, {}).get("nom") or str(c.get("nom") or cid)
        cps.append({"id": cid, "nom": nom[:80], "libelle": lib.strip(),
                    "inclus": c.get("inclus", True) is not False,
                    "mobile": c.get("mobile") is True})
    if area_doc and not any(c["inclus"] for c in cps):
        errs.append("au moins un checkpoint inclus")

    # --- Reglages avances -------------------------------------------------
    perime = payload.get("releve_perime_min", DEFAUT_RELEVE_PERIME_MIN)
    if isinstance(perime, bool) or not isinstance(perime, int) or not 1 <= perime <= 1440:
        errs.append("seuil de releve perime : entier de 1 a 1440 minutes")
        perime = DEFAUT_RELEVE_PERIME_MIN
    statuts = payload.get("statuts_passage", list(DEFAUT_STATUTS_PASSAGE))
    if not isinstance(statuts, list) or not statuts or \
            not all(isinstance(s, str) and _STATUT_RE.match(s) for s in statuts):
        errs.append("statuts comptes : liste de codes numeriques non vide")
        statuts = list(DEFAUT_STATUTS_PASSAGE)
    statuts = sorted(set(statuts), key=lambda s: int(s))

    # --- Site - visites libres (optionnel : absent = inchange) -------------
    site_doc = None
    if "site" in payload:
        site = payload.get("site")
        if not isinstance(site, dict):
            errs.append("site : objet attendu")
        else:
            s_enabled = site.get("enabled", False)
            if not isinstance(s_enabled, bool):
                errs.append("site : enabled booleen attendu")
                s_enabled = False
            marge = site.get("marge_min", DEFAUT_SITE_MARGE_MIN)
            if isinstance(marge, bool) or not isinstance(marge, int) \
                    or not 0 <= marge <= SITE_MARGE_MAX:
                errs.append("site : marge entier de 0 a %d minutes" % SITE_MARGE_MAX)
                marge = DEFAUT_SITE_MARGE_MIN
            s_cps, s_seen = [], set()
            raw = site.get("checkpoints") or []
            if not isinstance(raw, list):
                errs.append("site : checkpoints liste attendue")
                raw = []
            for c in raw:
                cid = str((c or {}).get("id") or "") if isinstance(c, dict) else ""
                if cid in s_seen:
                    continue
                if not _loc_ok(cid, "Checkpoint", "Site, checkpoint"):
                    continue
                s_seen.add(cid)
                lib = c.get("libelle") or ""
                if not isinstance(lib, str) or len(lib) > 60:
                    errs.append("Site, checkpoint %s : nom affiche de 60 caracteres au plus" % cid)
                    lib = ""
                if "inclus" in c and not isinstance(c["inclus"], bool):
                    errs.append("Site, checkpoint %s : inclus booleen attendu" % cid)
                sens = c.get("sens", "mixte")
                if sens not in SITE_SENS:
                    errs.append("Site, checkpoint %s : sens %r inconnu (mixte, entree, sortie)"
                                % (cid, sens))
                    sens = "mixte"
                nom = (known or {}).get(cid, {}).get("nom") or str(c.get("nom") or cid)
                s_cps.append({"id": cid, "nom": nom[:80], "libelle": lib.strip(),
                              "inclus": c.get("inclus", True) is not False, "sens": sens})
            if s_enabled and not any(c["inclus"] for c in s_cps):
                errs.append("site : au moins un checkpoint inclus pour activer les visites libres")
            site_doc = {"enabled": s_enabled, "marge_min": marge, "checkpoints": s_cps}

    doc = {
        "schema": SCHEMA,
        "enabled": enabled,
        "horaires": {"ouverture": defaut["ouverture"], "fermeture": defaut["fermeture"],
                     "semaine": semaine, "exceptions": exceptions},
        "area": area_doc,
        "gates": gates,
        "checkpoints": cps,
        "transactions": transactions,
        "releve_perime_min": perime,
        "statuts_passage": statuts,
    }
    if site_doc is not None:
        doc["site"] = site_doc
    return doc, errs


def ensure_indexes(db):
    ttl = RETENTION_JOURS * 24 * 3600
    db[COL_RELEVES].create_index([("date", 1), ("ts", 1)])
    db[COL_RELEVES].create_index("ts", expireAfterSeconds=ttl)
    db[COL_PASSAGES].create_index([("date", 1), ("heure", 1)])
    db[COL_PASSAGES].create_index("ts", expireAfterSeconds=ttl)
    db[COL_SITE_RELEVES].create_index([("date", 1), ("ts", 1)])
    db[COL_SITE_RELEVES].create_index("ts", expireAfterSeconds=ttl)
    db[COL_SITE_PASSAGES].create_index([("date", 1), ("heure", 1)])
    db[COL_SITE_PASSAGES].create_index("ts", expireAfterSeconds=ttl)


# ---------------------------------------------------------------------------
# Temps et horaires
# ---------------------------------------------------------------------------

def now_utc():
    return _dt.datetime.now(UTC)


def as_utc(ts):
    """Les datetimes relus de Mongo sont naifs (UTC)."""
    if ts is None:
        return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def paris(ts):
    return as_utc(ts).astimezone(TZ_PARIS)


def date_key(ts):
    return paris(ts).strftime("%Y-%m-%d")


def paris_at(date_str, hhmm):
    """Datetime aware Paris pour une date "YYYY-MM-DD" et une heure "HH:MM"."""
    d = _dt.date.fromisoformat(date_str)
    h, m = (int(x) for x in hhmm.split(":"))
    return _dt.datetime(d.year, d.month, d.day, h, m, tzinfo=TZ_PARIS)


def shift_date(date_str, days=0, years=0):
    d = _dt.date.fromisoformat(date_str)
    if years:
        try:
            d = d.replace(year=d.year + years)
        except ValueError:          # 29 fevrier
            d = d.replace(year=d.year + years, day=28)
    return (d + _dt.timedelta(days=days)).isoformat()


def horaires_du_jour(cfg, date_str):
    """Horaires effectifs d'une date : exception > jour de semaine > defaut.

    {ferme, ouverture, fermeture, source: exception|semaine|defaut, libelle}.
    ouverture/fermeture valent None un jour ferme."""
    hor = cfg.get("horaires") or {}
    for e in hor.get("exceptions") or []:
        if e.get("date") == date_str:
            if e.get("ferme"):
                return {"ferme": True, "ouverture": None, "fermeture": None,
                        "source": "exception", "libelle": e.get("libelle") or ""}
            return {"ferme": False, "ouverture": e["ouverture"], "fermeture": e["fermeture"],
                    "source": "exception", "libelle": e.get("libelle") or ""}
    j = JOURS[_dt.date.fromisoformat(date_str).weekday()]
    s = (hor.get("semaine") or {}).get(j)
    if s:
        if s.get("ferme"):
            return {"ferme": True, "ouverture": None, "fermeture": None,
                    "source": "semaine", "libelle": "ferme le " + JOURS_LIBELLES[j]}
        return {"ferme": False, "ouverture": s["ouverture"], "fermeture": s["fermeture"],
                "source": "semaine", "libelle": ""}
    return {"ferme": False, "ouverture": hor.get("ouverture"), "fermeture": hor.get("fermeture"),
            "source": "defaut", "libelle": ""}


def heure_base(cfg, date_str):
    """Heure avant laquelle un releve sert de base du jour : l'ouverture du
    jour, ou celle par defaut un jour ferme (des passages restent possibles)."""
    h = horaires_du_jour(cfg, date_str)
    return h["ouverture"] or (cfg.get("horaires") or {}).get("ouverture") or "00:00"


def statut_ouverture(cfg, now_p):
    """('ouvert'|'avant_ouverture'|'ferme'|'ferme_jour', libelle)."""
    h = horaires_du_jour(cfg, now_p.strftime("%Y-%m-%d"))
    if h["ferme"]:
        return "ferme_jour", "Ferme aujourd'hui" + (" (" + h["libelle"] + ")" if h["libelle"] else "")
    hm = now_p.strftime("%H:%M")
    if hm < h["ouverture"]:
        return "avant_ouverture", "Ouvre a " + h["ouverture"].replace(":", "h")
    if hm < h["fermeture"]:
        return "ouvert", "Ouvert jusqu'a " + h["fermeture"].replace(":", "h")
    return "ferme", "Ferme depuis " + h["fermeture"].replace(":", "h")


# ---------------------------------------------------------------------------
# Lecture des compteurs (collecteur)
# ---------------------------------------------------------------------------

def to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def build_releve(ts, compteurs, erreurs=None):
    p = paris(ts)
    return {"ts": as_utc(ts), "date": p.strftime("%Y-%m-%d"), "heure": p.strftime("%H:%M"),
            "compteurs": compteurs, "erreurs": erreurs or {}}


def _xid(d):
    d = d or {}
    return str(d.get("ID") or d.get("Id") or "")


def passage_doc(tx, area_id):
    """Transaction HSH (parse_transactions) -> doc musee_passages, ou None si
    ce n'est pas une entree dans l'Area du musee. Un checkpoint mobile sert
    aussi ailleurs : on filtre sur l'AREA, jamais sur le checkpoint."""
    if not area_id or _xid(tx.get("area")) != str(area_id):
        return None
    if tx.get("direction") == "Sortie":
        return None
    tid = tx.get("transaction_id")
    if not isinstance(tid, int):
        return None
    dp = tx.get("date_paris") or ""
    try:
        local = _dt.datetime.strptime(dp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_PARIS)
    except ValueError:
        return None
    cp = tx.get("checkpoint") or {}
    return {
        "_id": tid,
        "ts": local.astimezone(UTC),
        "date": dp[:10],
        "heure": dp[11:19],
        "checkpoint_id": _xid(cp),
        "checkpoint_nom": cp.get("Name") or "",
        "gate_id": _xid(tx.get("gate")),
        "gate_nom": (tx.get("gate") or {}).get("Name") or "",
        "area_id": str(area_id),
        "status": str(tx.get("status")),
        "releases": to_int(tx.get("releases")) or 1,
    }


def site_passage_doc(tx, locs_by_id):
    """Transaction HSH -> doc musee_site_passages, ou None si le checkpoint
    n'est pas un checkpoint inclus du perimetre visites libres. Filtre sur le
    CHECKPOINT (l'Area de l'enceinte couvre tout le site). Sens : celui de la
    transaction, sinon l'indication `sens` du checkpoint, sinon '?'."""
    cp = tx.get("checkpoint") or {}
    loc = locs_by_id.get(_xid(cp))
    if not loc:
        return None
    tid = tx.get("transaction_id")
    if not isinstance(tid, int):
        return None
    dp = tx.get("date_paris") or ""
    try:
        local = _dt.datetime.strptime(dp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_PARIS)
    except ValueError:
        return None
    direction = tx.get("direction")
    if direction == "Entree":
        sens, src = "E", "transaction"
    elif direction == "Sortie":
        sens, src = "S", "transaction"
    elif loc.get("sens") in ("entree", "sortie"):
        sens, src = ("E" if loc["sens"] == "entree" else "S"), "indication"
    else:
        sens, src = "?", None
    return {
        "_id": tid,
        "ts": local.astimezone(UTC),
        "date": dp[:10],
        "heure": dp[11:19],
        "checkpoint_id": _xid(cp),
        "checkpoint_nom": cp.get("Name") or "",
        "gate_id": _xid(tx.get("gate")),
        "area_id": _xid(tx.get("area")),
        "sens": sens,
        "sens_source": src,
        "status": str(tx.get("status")),
        "releases": to_int(tx.get("releases")) or 1,
    }


# ---------------------------------------------------------------------------
# Structure Handshake (choix du perimetre dans l'onglet Musee)
# ---------------------------------------------------------------------------

def _rid(d, key):
    v = d.get(key) if isinstance(d, dict) else None
    return str(v.get("id") or "") if isinstance(v, dict) else ""


def build_structure(inventaire, archives, passages=None):
    """Areas Handshake avec leurs portes et checkpoints, pour le formulaire.

    inventaire : [{location_id, location_type, location_name}] (Inquiry
                 Counter global de la borne) ou None si injoignable.
    archives   : [(edition, [docs hsh_archive_structure_*])] de la plus
                 ancienne a la plus recente : seules sources des relations
                 (l'inventaire est a plat).
    passages   : [{checkpoint_id, checkpoint_nom, gate_id, area_id}] observes
                 par le collecteur (relations les plus recentes).
    Un checkpoint vu sous plusieurs Areas selon les editions est signale
    `mobile_suggere` (cas du PDA.3.11, musee ou helpdesk)."""
    noms, types = {}, {}
    for _ed, docs in archives:
        for d in docs:
            lid, typ = str(d.get("location_id") or ""), d.get("location_type")
            if lid and typ in LOC_TYPES:
                types[lid] = typ
                if d.get("location_name"):
                    noms[lid] = d["location_name"]
    borne_ids = set()
    for c in inventaire or []:
        lid, typ = str(c.get("location_id") or ""), c.get("location_type")
        if lid and typ in LOC_TYPES:
            borne_ids.add(lid)
            types[lid] = typ
            if c.get("location_name"):
                noms[lid] = c["location_name"]

    gate_area = {}                  # (edition, gate) -> area
    area_gates = {}                 # area -> {gate: set(editions)}
    cp_rel = {}                     # cp -> {area: {"gates": set, "editions": [..]}}

    def _add_gate(area, gate, ed):
        area_gates.setdefault(area, {}).setdefault(gate, set()).add(ed)

    def _add_cp(cp, area, gate, ed):
        slot = cp_rel.setdefault(cp, {}).setdefault(area, {"gates": set(), "editions": []})
        if gate:
            slot["gates"].add(gate)
        if ed not in slot["editions"]:
            slot["editions"].append(ed)

    for ed, docs in archives:
        for d in docs:
            typ, lid = d.get("location_type"), str(d.get("location_id") or "")
            if typ == "Gate" and _rid(d, "parent_area"):
                gate_area[(ed, lid)] = _rid(d, "parent_area")
                _add_gate(_rid(d, "parent_area"), lid, ed)
            elif typ == "Area":
                for e in d.get("enfants") or []:
                    if e.get("type") == "Gate" and e.get("id"):
                        gate_area.setdefault((ed, str(e["id"])), lid)
                        _add_gate(lid, str(e["id"]), ed)
        for d in docs:
            typ, lid = d.get("location_type"), str(d.get("location_id") or "")
            if typ == "Checkpoint":
                area = _rid(d, "parent_area") or gate_area.get((ed, _rid(d, "parent_gate")), "")
                if area:
                    _add_cp(lid, area, _rid(d, "parent_gate"), ed)
            elif typ == "Gate":
                area = gate_area.get((ed, lid))
                for e in d.get("enfants") or []:
                    if area and e.get("type") == "Checkpoint" and e.get("id"):
                        _add_cp(str(e["id"]), area, lid, ed)
    for p in passages or []:
        cp, area = str(p.get("checkpoint_id") or ""), str(p.get("area_id") or "")
        if not cp or not area:
            continue
        types.setdefault(cp, "Checkpoint")
        if p.get("checkpoint_nom"):
            noms.setdefault(cp, p["checkpoint_nom"])
        gate = str(p.get("gate_id") or "")
        if gate:
            types.setdefault(gate, "Gate")
            _add_gate(area, gate, "collecte musee")
        _add_cp(cp, area, gate, "collecte musee")

    areas = []
    for aid in sorted((i for i, t in types.items() if t == "Area"), key=lambda i: (noms.get(i) or i)):
        gates = [{"id": g, "nom": noms.get(g) or g, "editions": sorted(eds),
                  "borne": g in borne_ids}
                 for g, eds in sorted(area_gates.get(aid, {}).items(), key=lambda kv: noms.get(kv[0]) or kv[0])]
        cps = []
        for cp, rel in cp_rel.items():
            if aid not in rel:
                continue
            autres = [{"id": a, "nom": noms.get(a) or a, "editions": r["editions"]}
                      for a, r in rel.items() if a != aid]
            cps.append({"id": cp, "nom": noms.get(cp) or cp,
                        "gates": sorted(rel[aid]["gates"]),
                        "editions": rel[aid]["editions"],
                        "autres_areas": autres,
                        "mobile_suggere": bool(autres),
                        "borne": cp in borne_ids})
        cps.sort(key=lambda c: c["nom"])
        areas.append({"id": aid, "nom": noms.get(aid) or aid, "gates": gates, "checkpoints": cps,
                      "borne": aid in borne_ids})
    known = {i: {"type": t, "nom": noms.get(i) or i} for i, t in types.items()}
    # Tous les checkpoints connus : ajout manuel quand aucune relation n'est
    # encore connue pour l'Area (installation nouvelle, pas encore archivee).
    tous = sorted(({"id": i, "nom": noms.get(i) or i, "borne": i in borne_ids}
                   for i, t in types.items() if t == "Checkpoint"), key=lambda c: c["nom"])
    return {"areas": areas, "known": known, "checkpoints": tous}


# ---------------------------------------------------------------------------
# Calcul d'une journee
# ---------------------------------------------------------------------------

def cumul_compteur(series):
    """Somme des increments d'un compteur cumulatif [(ts, entries), ...] trie.

    Robuste a une remise a zero (chute d'entries : le compteur repart de 0, le
    nouveau releve compte en entier) et a une lecture fautive isolee (chute
    suivie d'un retour au niveau precedent : ignoree), comme
    presents_etat.counter_baseline. Retourne (total, [(ts, delta)], resets)."""
    if not series:
        return 0, [], 0
    total, resets = 0, 0
    deltas = []
    prev = series[0][1]
    n = len(series)
    for i in range(1, n):
        ts, e = series[i]
        if e >= prev:
            d = e - prev
        else:
            nxt = series[i + 1][1] if i + 1 < n else None
            if nxt is not None and nxt >= prev:
                continue                      # lecture fautive isolee
            d = e                             # remise a zero
            resets += 1
        prev = e
        if d:
            total += d
            deltas.append((ts, d))
    return total, deltas, resets


def _series(releves, loc_id, champ="entries"):
    out = []
    for r in releves:
        c = (r.get("compteurs") or {}).get(loc_id) or {}
        e = c.get(champ)
        if isinstance(e, int):
            out.append((r["ts"], e))
    return out


def compute_day(db, date_str, cfg, until=None):
    """Visiteurs d'une journee (heure de Paris), eventuellement jusqu'a `until`
    ("HH:MM", pour comparer a meme heure). Ne lit que musee_*. La base du
    compteur est le dernier releve avant l'ouverture DE CE JOUR."""
    q = {"date": date_str}
    if until:
        q["heure"] = {"$lte": until}
    releves = list(db[COL_RELEVES].find(q, {"ts": 1, "heure": 1, "compteurs": 1}).sort("ts", 1))
    for r in releves:
        r["ts"] = as_utc(r["ts"])
    area = cfg["area_id"]
    open_dt = paris_at(date_str, heure_base(cfg, date_str))

    # --- Compteur de l'Area : delta depuis la base du jour -----------------
    serie = _series(releves, area)
    base_idx = None
    for i, (ts, _e) in enumerate(serie):
        if ts < open_dt:
            base_idx = i
    avant_ouverture = base_idx is not None
    if base_idx is None and serie:
        base_idx = 0
    cpt_total, cpt_deltas, resets = (0, [], 0)
    baseline = None
    if base_idx is not None:
        cpt_total, cpt_deltas, resets = cumul_compteur(serie[base_idx:])
        b_ts, b_e = serie[base_idx]
        baseline = {"ts": b_ts, "heure": paris(b_ts).strftime("%H:%M"), "entries": b_e,
                    "avant_ouverture": avant_ouverture}

    cpt_heure = {}
    for ts, d in cpt_deltas:
        h = paris(ts).strftime("%H")
        cpt_heure[h] = cpt_heure.get(h, 0) + d
    cpt_cp = {}
    for loc in cfg["locations"]:
        if loc["role"] != "checkpoint":
            continue
        s = _series(releves, loc["id"])
        b = 0
        for i, (ts, _e) in enumerate(s):
            if ts < open_dt:
                b = i
        n, _d, _r = cumul_compteur(s[b:]) if s else (0, [], 0)
        cpt_cp[loc["id"]] = {"nom": loc["nom"], "n": n, "mobile": loc["mobile"]}

    # --- Passages (transactions) -----------------------------------------
    jour = db[COL_JOURS].find_one({"_id": date_str},
                                  {"tx_depuis": 1, "tx_jusqu_a": 1, "tx_retard": 1}) or {}
    # Couverture complete = premiere fenetre partie de minuit et pas de retard
    # de pagination en cours (plafond MAX_PAGES_TX du collecteur).
    tx_complet = jour.get("tx_depuis") == "00:00:00" and not jour.get("tx_retard")
    pq = {"date": date_str, "status": {"$in": list(cfg["statuts_passage"])}}
    if until:
        pq["heure"] = {"$lte": until + ":59"}
    tx_total = 0
    tx_heure, tx_cp = {}, {}
    noms = {loc["id"]: loc for loc in cfg["locations"]}
    for p in db[COL_PASSAGES].find(pq, {"heure": 1, "checkpoint_id": 1, "checkpoint_nom": 1}):
        tx_total += 1
        h = (p.get("heure") or "00")[:2]
        tx_heure[h] = tx_heure.get(h, 0) + 1
        cid = p.get("checkpoint_id") or "?"
        loc = noms.get(cid) or {}
        slot = tx_cp.setdefault(cid, {"nom": loc.get("nom") or p.get("checkpoint_nom") or cid,
                                      "n": 0, "mobile": bool(loc.get("mobile"))})
        slot["n"] += 1

    # --- Choix de la source ------------------------------------------------
    partiel_depuis = None
    if serie and avant_ouverture:
        visiteurs, source = cpt_total, "compteur"
    elif tx_complet:
        visiteurs, source = tx_total, "transactions"
    elif serie:
        visiteurs, source = cpt_total, "compteur_partiel"
        partiel_depuis = baseline["heure"] if baseline else None
    elif tx_total:
        visiteurs, source = tx_total, "transactions_partiel"
        partiel_depuis = jour.get("tx_depuis")
    else:
        visiteurs, source = None, None

    if tx_complet:
        par_heure, par_cp = tx_heure, tx_cp
    elif serie:
        par_heure, par_cp = cpt_heure, cpt_cp
    else:
        par_heure, par_cp = tx_heure, tx_cp

    pic = None
    if par_heure:
        h, n = max(par_heure.items(), key=lambda kv: (kv[1], -int(kv[0])))
        if n > 0:
            pic = {"heure": h, "n": n}

    return {
        "date": date_str,
        "visiteurs": visiteurs,
        "source": source,
        "partiel_depuis": partiel_depuis,
        "visiteurs_compteur": cpt_total if serie else None,
        "visiteurs_tx": tx_total if (tx_complet or tx_total) else None,
        "tx_complet": tx_complet,
        "par_heure": dict(sorted(par_heure.items())),
        "par_checkpoint": par_cp,
        "pic_heure": pic,
        "baseline": baseline,
        "resets": resets,
        "nb_releves": len(releves),
        "premier_releve": releves[0]["ts"] if releves else None,
        "dernier_releve": releves[-1]["ts"] if releves else None,
    }


def save_day(db, date_str, cfg):
    """Recalcule et enregistre l'agregat musee_jours d'une date (idempotent)."""
    day = compute_day(db, date_str, cfg)
    doc = {k: v for k, v in day.items() if k != "date"}
    doc["date"] = date_str
    doc["maj"] = now_utc()
    db[COL_JOURS].update_one({"_id": date_str}, {"$set": doc}, upsert=True)
    return day


# ---------------------------------------------------------------------------
# Site - visites libres (jours publics SAISON, hors epreuve)
# ---------------------------------------------------------------------------

def _min(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _hhmm(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


def site_fenetre(site, info):
    """Fenetre de comptage d'un jour de visites libres : horaires SAISON
    -/+ marge, bornee a la journee. info = {open, close, is24h}."""
    if not info:
        return None
    if info.get("is24h"):
        return {"debut": "00:00", "fin": "23:59"}
    o = info.get("open") if hhmm_ok(info.get("open")) else "00:00"
    c = info.get("close") if hhmm_ok(info.get("close")) else "23:59"
    if c <= o:                       # fermeture apres minuit ou saisie bancale
        c = "23:59"
    m = int(site.get("marge_min") or 0)
    return {"debut": _hhmm(max(_min(o) - m, 0)), "fin": _hhmm(min(_min(c) + m, 23 * 60 + 59))}


def site_gate(site, now_p, info, epreuve):
    """Faut-il collecter / compter le site maintenant ? Pur.

    info    : horaires SAISON du jour ({open, close, is24h}) ou None (pas un
              jour de visites libres).
    epreuve : une epreuve est active (bascule : les checkpoints servent au
              delestage, ce sont des spectateurs).
    `collecte` : on releve toute la journee (la base du compteur est le releve
    precedant le debut de la fenetre) ; `dans_fenetre` : on compte."""
    date = now_p.strftime("%Y-%m-%d")
    hm = now_p.strftime("%H:%M")
    fen = site_fenetre(site, info) if info else None
    g = {"date": date, "jour_public": info is not None,
         "horaires": ({"ouverture": info.get("open"), "fermeture": info.get("close"),
                       "is24h": bool(info.get("is24h"))} if info else None),
         "fenetre": fen, "collecte": False, "dans_fenetre": False}
    if not site.get("enabled"):
        g.update(raison="desactive", libelle="Visites libres desactivees")
    elif not site.get("configure"):
        g.update(raison="a_configurer", libelle="Visites libres a configurer")
    elif info is None:
        g.update(raison="pas_jour_public", libelle="Pas de visites libres aujourd'hui")
    elif epreuve:
        g.update(raison="epreuve", libelle="Epreuve en cours : pas de comptage")
    else:
        g["collecte"] = True
        if hm < fen["debut"]:
            g.update(raison="avant", libelle="Visites libres a partir de %s"
                     % info.get("open", fen["debut"]).replace(":", "h"))
        elif hm > fen["fin"]:
            g.update(raison="termine", libelle="Visites libres terminees")
        else:
            g.update(raison="ouvert", dans_fenetre=True,
                     libelle="Visites libres %s - %s" % (
                         (info.get("open") or fen["debut"]).replace(":", "h"),
                         (info.get("close") or fen["fin"]).replace(":", "h")))
    return g


def site_gate_db(db, cfg, now=None):
    """site_gate avec les jours publics SAISON et les epreuves actives
    (event_courant.is_saison_public_day : False des qu'une epreuve est active)."""
    now = as_utc(now) if now else now_utc()
    now_p = now.astimezone(TZ_PARIS)
    site = cfg["site"]
    if not site["enabled"] or not site["configure"]:
        return site_gate(site, now_p, None, False)
    import event_courant
    try:
        ok, info = event_courant.is_saison_public_day(db, now_p.strftime("%Y-%m-%d"), now)
    except Exception as exc:             # parametrage illisible : on ne compte pas
        g = site_gate(site, now_p, None, False)
        g.update(raison="saison_illisible", libelle="Jours publics SAISON illisibles (%s)" % str(exc)[:80])
        return g
    return site_gate(site, now_p, info, bool(info) and not ok)


def site_prochains_jours(db, today, n=10):
    """Prochains jours de visites libres saisis sur SAISON (lecture seule),
    avec l'epreuve dont la fenetre les recouvre (ils ne seront pas comptes)."""
    import event_courant
    jours = {}
    y = int(today[:4])
    for annee in (y, y + 1):
        jours.update(event_courant.saison_public_days(db, annee))
    try:
        wins = event_courant.windows(db)
    except Exception:
        wins = []
    out = []
    for d in sorted(k for k in jours if k >= today)[:n]:
        info = jours[d]
        o = info["open"] if hhmm_ok(info.get("open")) else "12:00"
        t = paris_at(d, o).astimezone(UTC)
        ep = next(("%s %s" % (w["event"], w["year"]) for w in wins if w["start"] <= t <= w["end"]), None)
        out.append({"date": d, "ouverture": info.get("open"), "fermeture": info.get("close"),
                    "is24h": bool(info.get("is24h")), "epreuve": ep})
    return out


def _running(events):
    """[(cle_tri, heure 'HH:MM', +entrees, +sorties)] -> (presents_max, a quelle heure)."""
    # Meme instant (releve ou seconde) : entrees et sorties nettees ensemble,
    # sinon le pic serait gonfle par l'ordre de lecture des compteurs.
    net = {}
    for k, h, e, s in events:
        slot = net.setdefault(k, [h, 0])
        slot[1] += e - s
    cur, best, best_h = 0, None, None
    for _k, (h, d) in sorted(net.items(), key=lambda kv: kv[0]):
        cur += d
        if best is None or cur > best:
            best, best_h = cur, h
    return (max(best, 0), best_h) if best is not None else (None, None)


def compute_site_day(db, date_str, cfg, fenetre=None):
    """Entrees / sorties / presents du site pour une journee, dans la fenetre
    (horaires SAISON +/- marge). Base du compteur : dernier releve AVANT le
    debut de la fenetre (meme regle robuste que le musee : remise a zero,
    lecture fautive isolee). Repli sur les transactions si la couverture part
    d'avant la fenetre."""
    site = cfg["site"]
    jour = db[COL_SITE_JOURS].find_one({"_id": date_str}) or {}
    fen = fenetre or jour.get("fenetre") or {"debut": "00:00", "fin": "23:59"}
    start = paris_at(date_str, fen["debut"])
    end = paris_at(date_str, fen["fin"]) + _dt.timedelta(seconds=59)
    ids = {loc["id"]: loc for loc in site["locations"]}

    releves = list(db[COL_SITE_RELEVES].find({"date": date_str},
                                              {"ts": 1, "heure": 1, "compteurs": 1}).sort("ts", 1))
    for r in releves:
        r["ts"] = as_utc(r["ts"])
    base_idx = None
    for i, r in enumerate(releves):
        if r["ts"] < start:
            base_idx = i
    avant = base_idx is not None
    if base_idx is None and releves:
        base_idx = 0
    sub = [r for r in releves[base_idx:] if r["ts"] <= end] if base_idx is not None else []

    c_e = c_s = 0
    c_events, c_heure, c_cp = [], {}, {}
    for cid, loc in ids.items():
        slot = c_cp.setdefault(cid, {"nom": loc["nom"], "entrees": 0, "sorties": 0})
        for champ, key in (("entries", "entrees"), ("exits", "sorties")):
            tot, deltas, _r = cumul_compteur(_series(sub, cid, champ))
            slot[key] += tot
            for ts, d in deltas:
                p = paris(ts)
                c_events.append((ts, p.strftime("%H:%M"), d if key == "entrees" else 0,
                                 d if key == "sorties" else 0))
                hs = c_heure.setdefault(p.strftime("%H"), {"entrees": 0, "sorties": 0})
                hs[key] += d
            if key == "entrees":
                c_e += tot
            else:
                c_s += tot

    tx_depuis = jour.get("tx_depuis")
    tx_complet = bool(tx_depuis) and tx_depuis <= fen["debut"] + ":00" and not jour.get("tx_retard")
    statuts = set(cfg["statuts_passage"])
    t_e = t_s = 0
    t_events, t_heure, t_cp = [], {}, {}
    h0, h1 = fen["debut"] + ":00", fen["fin"] + ":59"
    for p in db[COL_SITE_PASSAGES].find({"date": date_str},
                                        {"heure": 1, "checkpoint_id": 1, "sens": 1, "status": 1}):
        h = p.get("heure") or ""
        cid = p.get("checkpoint_id")
        if cid not in ids or p.get("status") not in statuts or not h0 <= h <= h1:
            continue
        sens = p.get("sens")
        if sens not in ("E", "S"):
            continue
        e, s = (1, 0) if sens == "E" else (0, 1)
        t_e += e
        t_s += s
        t_events.append((h, h[:5], e, s))
        hs = t_heure.setdefault(h[:2], {"entrees": 0, "sorties": 0})
        hs["entrees"] += e
        hs["sorties"] += s
        slot = t_cp.setdefault(cid, {"nom": ids[cid]["nom"], "entrees": 0, "sorties": 0})
        slot["entrees"] += e
        slot["sorties"] += s

    partiel = None
    if len(sub) and avant:
        source = "compteur"
    elif tx_complet:
        source = "transactions"
    elif sub:
        source = "compteur_partiel"
        partiel = paris(sub[0]["ts"]).strftime("%H:%M")
    elif t_events:
        source = "transactions_partiel"
        partiel = (tx_depuis or "")[:5] or None
    else:
        source = None

    if source in ("compteur", "compteur_partiel"):
        entrees, sorties, events = c_e, c_s, c_events
    elif source:
        entrees, sorties, events = t_e, t_s, t_events
    else:
        entrees = sorties = None
        events = []
    # Courbe et repartition : transactions (heure exacte) si completes.
    if tx_complet:
        par_heure, par_cp = t_heure, t_cp
    elif sub:
        par_heure, par_cp = c_heure, c_cp
    else:
        par_heure, par_cp = t_heure, t_cp
    pmax, pmax_h = _running(events)
    return {
        "date": date_str,
        "fenetre": fen,
        "entrees": entrees,
        "sorties": sorties,
        "presents_now": max(entrees - sorties, 0) if source else None,
        "presents_max": {"n": pmax, "heure": pmax_h} if pmax is not None else None,
        "source": source,
        "partiel_depuis": partiel,
        "entrees_compteur": c_e if sub else None,
        "sorties_compteur": c_s if sub else None,
        "entrees_tx": t_e if (tx_complet or t_events) else None,
        "sorties_tx": t_s if (tx_complet or t_events) else None,
        "tx_complet": tx_complet,
        "par_heure": dict(sorted(par_heure.items())),
        "par_checkpoint": par_cp,
        "nb_releves": len(releves),
        "dernier_releve": releves[-1]["ts"] if releves else None,
    }


def save_site_day(db, date_str, cfg, fenetre=None):
    day = compute_site_day(db, date_str, cfg, fenetre)
    doc = {k: v for k, v in day.items() if k != "date"}
    doc["date"] = date_str
    doc["maj"] = now_utc()
    db[COL_SITE_JOURS].update_one({"_id": date_str}, {"$set": doc}, upsert=True)
    return day


def build_site_state(db, cfg, now=None):
    """Section `site` de /api/musee/state. `visible` : jour de visites libres
    ou donnees du jour ; jamais de 0 invente (None sans donnee)."""
    now = as_utc(now) if now else now_utc()
    now_p = now.astimezone(TZ_PARIS)
    today = now_p.strftime("%Y-%m-%d")
    site = cfg["site"]
    gate = site_gate_db(db, cfg, now)
    out = {"visible": False, "libelle": site["libelle"], "actif": gate["collecte"],
           "dans_fenetre": gate["dans_fenetre"], "jour_public": gate["jour_public"],
           "raison": gate["raison"], "raison_libelle": gate["libelle"],
           "horaires": gate["horaires"], "fenetre": gate["fenetre"]}
    out["enabled"] = bool(site["enabled"] and site["configure"])
    if not out["enabled"]:
        return out
    # Prochain jour de visites libres (bloc de l'accueil, hors jour de visite)
    try:
        nxt = [j for j in site_prochains_jours(db, today, n=5) if j["date"] > today and not j.get("epreuve")]
        out["prochain"] = nxt[0] if nxt else None
    except Exception:
        out["prochain"] = None
    jour = db[COL_SITE_JOURS].find_one({"_id": today}, {"fenetre": 1, "derniere_erreur": 1})
    day = None
    if gate["collecte"] or jour:
        day = compute_site_day(db, today, cfg, gate["fenetre"] or (jour or {}).get("fenetre"))
    a_donnees = bool(day and day["source"])
    out["visible"] = bool(gate["jour_public"] or a_donnees)
    if not out["visible"]:
        return out
    last = db[COL_SITE_RELEVES].find_one({"date": today}, {"ts": 1}, sort=[("ts", -1)])
    last_ts = as_utc(last["ts"]) if last else None
    age = int((now - last_ts).total_seconds()) if last_ts else None
    fen = (day or {}).get("fenetre") or gate["fenetre"]
    heures = set((day or {}).get("par_heure", {}).keys())
    if fen:
        heures |= set("%02d" % h for h in range(int(fen["debut"][:2]), int(fen["fin"][:2]) + 1))
    ph = (day or {}).get("par_heure", {})
    out.update({
        "entrees": day["entrees"] if day else None,
        "sorties": day["sorties"] if day else None,
        "presents": day["presents_now"] if day else None,
        "presents_max": day["presents_max"] if day else None,
        "source": day["source"] if day else None,
        "partiel_depuis": day["partiel_depuis"] if day else None,
        "par_heure": ([{"heure": h, "entrees": ph.get(h, {}).get("entrees", 0),
                        "sorties": ph.get(h, {}).get("sorties", 0)} for h in sorted(heures)]
                      if a_donnees else []),
        "par_checkpoint": ([{"id": k, "nom": v["nom"], "entrees": v["entrees"], "sorties": v["sorties"]}
                            for k, v in (day or {}).get("par_checkpoint", {}).items()]
                           if a_donnees else []),
        "dernier_releve_heure": paris(last_ts).strftime("%H:%M") if last_ts else None,
        "age_releve_s": age,
        "releve_perime": bool(gate["dans_fenetre"] and (age is None or age > cfg["releve_perime_s"])),
        "derniere_erreur": (jour or {}).get("derniere_erreur"),
    })
    return out


# ---------------------------------------------------------------------------
# Etat pour l'API
# ---------------------------------------------------------------------------

def _iso(ts):
    return as_utc(ts).isoformat() if ts else None


def etat_collecte(db, now=None):
    """Signes de vie de la tache : dernier releve, derniere collecte, erreur."""
    now = as_utc(now) if now else now_utc()
    last = db[COL_RELEVES].find_one({}, {"ts": 1, "erreurs": 1}, sort=[("ts", -1)])
    jour = db[COL_JOURS].find_one({}, {"derniere_collecte": 1, "derniere_erreur": 1},
                                  sort=[("derniere_collecte", -1)]) or {}
    last_ts = as_utc(last["ts"]) if last else None
    coll_ts = as_utc(jour.get("derniere_collecte")) if jour.get("derniere_collecte") else None
    return {
        "dernier_releve": _iso(last_ts),
        "dernier_releve_heure": paris(last_ts).strftime("%d/%m %H:%M") if last_ts else None,
        "age_releve_s": int((now - last_ts).total_seconds()) if last_ts else None,
        "erreurs_releve": (last or {}).get("erreurs") or {},
        "derniere_collecte": _iso(coll_ts),
        "derniere_collecte_heure": paris(coll_ts).strftime("%d/%m %H:%M") if coll_ts else None,
        "derniere_erreur": jour.get("derniere_erreur"),
    }


def _comparaison(db, cfg, date_str, until):
    """Total (agregat enregistre) et valeur a la meme heure d'une date passee."""
    jour = db[COL_JOURS].find_one({"_id": date_str}, {"visiteurs": 1, "source": 1})
    if not jour or jour.get("visiteurs") is None:
        return None
    meme = compute_day(db, date_str, cfg, until=until)
    return {"date": date_str, "total": jour.get("visiteurs"), "source": jour.get("source"),
            "a_meme_heure": meme.get("visiteurs")}


def build_state(db, now=None, cfg=None):
    cfg = cfg if cfg is not None else get_config(db)
    now = as_utc(now) if now else now_utc()
    now_p = now.astimezone(TZ_PARIS)
    today = now_p.strftime("%Y-%m-%d")

    if cfg is None or not cfg["configure"]:
        return {"ok": True, "configure": False,
                "enabled": True if cfg is None else bool(cfg["enabled"]),
                "date": today, "maintenant": now_p.strftime("%H:%M"),
                "statut": "a_configurer", "statut_libelle": "A configurer",
                "site": {"visible": False}}

    statut, libelle = statut_ouverture(cfg, now_p)
    hj = horaires_du_jour(cfg, today)

    last = db[COL_RELEVES].find_one({}, {"ts": 1, "erreurs": 1}, sort=[("ts", -1)])
    last_ts = as_utc(last["ts"]) if last else None
    age = int((now - last_ts).total_seconds()) if last_ts else None
    perime = age is None or age > cfg["releve_perime_s"]

    day = compute_day(db, today, cfg)
    jour = db[COL_JOURS].find_one({"_id": today}, {"derniere_erreur": 1, "derniere_collecte": 1}) or {}

    # Comparaisons a meme heure : bornee a la fermeture du jour (apres, on
    # compare des journees completes). Chaque date passee garde SES horaires.
    until = now_p.strftime("%H:%M")
    if hj["fermeture"]:
        until = min(until, hj["fermeture"])
    comp = {
        "semaine_derniere": _comparaison(db, cfg, shift_date(today, days=-7), until),
        "n_1": _comparaison(db, cfg, shift_date(today, years=-1), until),
    }

    heures = set(day["par_heure"].keys())
    if not hj["ferme"]:
        h0 = int(hj["ouverture"][:2])
        h1 = int(hj["fermeture"][:2]) + (0 if hj["fermeture"].endswith(":00") else 1)
        heures |= set("%02d" % h for h in range(h0, h1))
    par_heure = [{"heure": h, "n": day["par_heure"].get(h, 0)} for h in sorted(heures)]

    cps = []
    for loc in cfg["locations"]:
        if loc["role"] != "checkpoint":
            continue
        c = day["par_checkpoint"].get(loc["id"]) or {}
        cps.append({"id": loc["id"], "nom": loc["nom"], "n": c.get("n", 0),
                    "mobile": loc["mobile"]})
    for cid, c in day["par_checkpoint"].items():
        if not any(x["id"] == cid for x in cps):
            cps.append({"id": cid, "nom": c.get("nom") or cid, "n": c.get("n", 0),
                        "mobile": bool(c.get("mobile"))})

    return {
        "ok": True,
        "configure": True,
        "enabled": bool(cfg["enabled"]),
        "date": today,
        "maintenant": now_p.strftime("%H:%M"),
        "ouverture": hj["ouverture"],
        "fermeture": hj["fermeture"],
        "horaires_jour": hj,
        "area_nom": cfg["area_nom"],
        "statut": statut,
        "statut_libelle": libelle,
        "visiteurs": day["visiteurs"],
        "source": day["source"],
        "partiel_depuis": day["partiel_depuis"],
        "visiteurs_compteur": day["visiteurs_compteur"],
        "visiteurs_tx": day["visiteurs_tx"],
        "resets": day["resets"],
        "dernier_releve": _iso(last_ts),
        "dernier_releve_heure": paris(last_ts).strftime("%d/%m %H:%M") if last_ts else None,
        "age_releve_s": age,
        "releve_perime": perime,
        "derniere_erreur": jour.get("derniere_erreur"),
        "par_heure": par_heure,
        "par_checkpoint": cps,
        "pic_heure": day["pic_heure"],
        "comparaisons": comp,
        "site": _site_state_safe(db, cfg, now),
    }


def _site_state_safe(db, cfg, now):
    """La section site ne doit jamais casser l'etat du musee."""
    try:
        return build_site_state(db, cfg, now)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("musee site state : %s", exc)
        return {"visible": False, "erreur": str(exc)[:200]}
