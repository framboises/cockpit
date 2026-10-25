"""Catalogue des modeles de cameras (collection `cameras_models`).

Un modele = un appareil a commander : marque, reference, motif reconnu dans
le modele des cameras reelles, objectifs (ouverture horizontale, portee DORI
"observer") et fiche technique PDF facultative. Sert a la fois :
- aux objectifs des canaux (prereglage du cone sur la carte, cameras.js) ;
- aux cameras en projet (champ `model_id`, canaux poses d'apres le modele) ;
- au dossier de planification des travaux (materiel, fiches en annexe).

multi = True : UN appareil a plusieurs capteurs ; ses objectifs, avec leur
nombre (`count`), decrivent la disposition des canaux (DS-2DP7D836 : 1 PTZ +
4 fixes). multi = False : objectifs au choix, un canal = un appareil
(iDS-2CD7A87G0 en 2,8 ou 12 mm).

Cles d'objectif (`k`) : uniques dans tout le catalogue, stockees sur les
canaux en projet (champ `model`). Les cles du catalogue initial reprennent
l'ancienne table LENSES de cameras.js (projets deja saisis inchanges).
"""
import os
import re
import secrets
from datetime import datetime, timezone

from bson.objectid import ObjectId

ROOT = os.path.dirname(os.path.abspath(__file__))
DATASHEET_DIR = os.path.join(ROOT, "uploads", "camera_models")
DATASHEET_MAX_BYTES = 25 * 1024 * 1024
MAX_LENSES = 8
OPTICS = ("mobile", "fixe")
# Objectif par defaut d'un canal sans modele, selon son optique
FALLBACK = {"mobile": "ptz", "fixe": "bullet-wide"}

# Catalogue initial (fiches techniques Hikvision) : ouverture horizontale et
# portee = distance DORI "Observer" (63 px/m) ; PTZ : grand angle.
SEED = [
    {"brand": "Hikvision", "ref": "DS-2DP7D836", "match": "2DP7D836", "multi": True,
     "note": "Multi-capteurs 1 PTZ + 4 objectifs fixes",
     "lenses": [{"k": "2dp-ptz", "label": "PTZ", "optics": "mobile", "fov": 59, "range": 48, "count": 1},
                # Portee harmonisee a 30 m sur les objectifs fixes (demande du 21/10/2026, fiche : 22 m)
                {"k": "2dp-pano", "label": "objectif fixe", "optics": "fixe", "fov": 106, "range": 30, "count": 4}]},
    {"brand": "Hikvision", "ref": "DS-2SF8C442", "match": "2SF8C442", "multi": True,
     "note": "1 PTZ + 1 panoramique",
     "lenses": [{"k": "2sf-ptz", "label": "PTZ", "optics": "mobile", "fov": 59, "range": 28, "count": 1},
                {"k": "2sf-pano", "label": "panoramique", "optics": "fixe", "fov": 190, "range": 31, "count": 1}]},
    {"brand": "Hikvision", "ref": "iDS-2CD7A87G0", "match": "2CD7A87G0", "multi": False,
     "note": "Modele de base des fixes",
     "lenses": [{"k": "bullet-wide", "label": "fixe 2,8 mm", "optics": "fixe", "fov": 100, "range": 37, "count": 1},
                {"k": "bullet-tele", "label": "fixe 12 mm", "optics": "fixe", "fov": 44, "range": 81, "count": 1}]},
    {"brand": "Hikvision", "ref": "DS-2CD63C5G1", "match": "2CD63C5G1", "multi": False,
     "note": "Fisheye 12 Mpx",
     "lenses": [{"k": "fisheye-360", "label": "360° au plafond", "optics": "fixe", "fov": 360, "range": 11, "count": 1},
                {"k": "fisheye-180", "label": "180° au mur", "optics": "fixe", "fov": 180, "range": 11, "count": 1}]},
    {"brand": "", "ref": "PTZ générique", "match": "", "multi": False,
     "note": "Modele a definir",
     "lenses": [{"k": "ptz", "label": "PTZ", "optics": "mobile", "fov": 59, "range": 50, "count": 1}]},
]


def ensure_seed(db):
    """Catalogue initial pose une seule fois (marqueur dans cameras_meta) : un
    catalogue vide par choix ne se remplit pas tout seul."""
    if db["cameras_meta"].find_one({"_id": "models_seeded"}):
        return
    if not db["cameras_models"].count_documents({}):
        now = datetime.now(timezone.utc)
        for m in SEED:
            db["cameras_models"].insert_one(dict(m, _id=ObjectId(), lenses=[dict(x) for x in m["lenses"]], by="Cockpit", at=now, updatedAt=now))
    db["cameras_meta"].update_one({"_id": "models_seeded"}, {"$set": {"at": datetime.now(timezone.utc)}}, upsert=True)


def catalogue(db):
    ensure_seed(db)
    models = list(db["cameras_models"].find())
    models.sort(key=lambda m: (not m.get("brand"), (m.get("brand") or "").lower(), (m.get("ref") or "").lower()))
    return models


def name(m):
    return ("%s %s" % (m.get("brand") or "", m.get("ref") or "")).strip()


def lens_index(models):
    """{cle d'objectif: (modele, objectif)}"""
    return {x["k"]: (m, x) for m in models for x in m.get("lenses") or []}


def layout(m):
    """Cles d'objectif des canaux du modele : multi = disposition complete
    (PTZ d'abord), sinon le premier objectif."""
    lenses = m.get("lenses") or []
    if not m.get("multi"):
        return [lenses[0]["k"]] if lenses else []
    return [x["k"] for x in lenses for _ in range(max(1, int(x.get("count") or 1)))]


def model_for_device(models, device_model):
    """Modele du catalogue reconnu dans le modele d'une camera reelle (motif
    le plus long d'abord : un motif court ne masque pas un plus precis)."""
    u = (device_model or "").upper()
    if not u:
        return None
    cands = [m for m in models if (m.get("match") or "").strip() and m["match"].strip().upper() in u]
    return max(cands, key=lambda m: len(m["match"].strip())) if cands else None


def lens_key(models, ch, device_model="", index=0, fallback=True, idx=None):
    """Objectif d'un canal : choisi (champ `model`), sinon deduit du modele de
    l'appareil (rang du canal dans la disposition), sinon profil par defaut de
    l'optique. Meme regle que lensKeyFor de cameras.js."""
    idx = lens_index(models) if idx is None else idx
    if ch.get("model") in idx:
        return ch["model"]
    m = model_for_device(models, device_model)
    if m:
        lay = layout(m) or [x["k"] for x in m.get("lenses") or []]
        if lay:
            return lay[index] if index < len(lay) else lay[-1]
    if not fallback:  # camera deplacee de modele inconnu : on ne lui prete pas un objectif
        return ""
    k = FALLBACK.get(ch.get("optics") or "")
    return k if k in idx else ""


def device_label(m, lens=None):
    """Libelle d'appareil a commander : 'Hikvision DS-2DP7D836 (1 PTZ + 4
    objectif fixe)', 'Hikvision iDS-2CD7A87G0 – fixe 2,8 mm'."""
    base = name(m) or "Caméra"
    if m.get("multi"):
        parts = []
        for x in m.get("lenses") or []:
            n = int(x.get("count") or 1)
            parts.append(("%d %s" % (n, _plural(x.get("label") or "") if n > 1 else x.get("label") or "")).strip())
        return "%s (%s)" % (base, " + ".join(parts))
    lenses = m.get("lenses") or []
    if lens and len(lenses) > 1:
        return "%s – %s" % (base, lens.get("label") or "")
    return base


def _plural(label):
    """'objectif fixe' -> 'objectifs fixes' ; sigles, nombres et unites
    (PTZ, 2,8 mm, Mpx) laisses tels quels."""
    return " ".join(w + "s" if w.isalpha() and w.islower() and len(w) > 2 and w[-1] not in "sxz" else w
                    for w in label.split(" "))


GENERIC_LENSES = ("ptz",)  # objectif "modele a definir"


def resolve_channels(models, chans, device_model="", fallback=True, idx=None):
    """Canaux avec leur objectif : [{..., lens, lens_model, lens_obj,
    explicit}] ; explicit = objectif choisi sur le canal (pas deduit)."""
    idx = lens_index(models) if idx is None else idx
    out = []
    for j, c in enumerate(chans):
        k = lens_key(models, c, device_model, j, fallback=fallback, idx=idx)
        m, lens = idx.get(k, (None, None))
        out.append(dict(c, lens=k, lens_model=m, lens_obj=lens, explicit=c.get("model") in idx))
    return out


def devices(chans, project_model=None):
    """[(libelle, modele ou None)] des appareils d'un emplacement. Regle :
    - modele multi-capteurs choisi pour l'emplacement : UN appareil par
      disposition complete (1 PTZ + 4 fixes = 1 ; 10 canaux = 2) ;
    - objectifs d'un modele multi-capteurs : idem, par modele ;
    - un PTZ generique (ou sans objectif) avec des fixes hors multi-capteurs =
      UNE camera multi-capteurs, modele a definir (canaux saisis un par un,
      boutons + Mobile / + Fixe) ;
    - sinon un appareil par canal (fixes simples d'un modele choisi).
    chans : canaux resolus (resolve_channels)."""
    pm = project_model
    if pm and pm.get("multi"):
        lay = layout(pm) or [None]
        return [(device_label(pm), pm)] * max(1, -(-len(chans) // len(lay)))
    out, multi, loose = [], {}, []
    for ch in chans:
        m, lens = ch.get("lens_model"), ch.get("lens_obj")
        if m and m.get("multi"):
            if m["_id"] not in multi:
                multi[m["_id"]] = {"m": m, "n": {}, "at": len(out)}
                out.append(None)  # place reservee : ordre d'apparition
            multi[m["_id"]]["n"][lens["k"]] = multi[m["_id"]]["n"].get(lens["k"], 0) + 1
        else:
            loose.append(ch)
    for g in sorted(multi.values(), key=lambda g: -g["at"]):
        per = {x["k"]: max(1, int(x.get("count") or 1)) for x in g["m"].get("lenses") or []}
        n = max(-(-cnt // per.get(k, 1)) for k, cnt in g["n"].items())
        out[g["at"]:g["at"] + 1] = [(device_label(g["m"]), g["m"])] * n

    ptz = [ch for ch in loose if ch.get("optics") == "mobile" and (not ch.get("lens_model") or ch.get("lens") in GENERIC_LENSES)]
    fixes = [ch for ch in loose if ch.get("optics") == "fixe"]
    if len(ptz) == 1 and fixes:
        lab = "Caméra multi-capteurs 1 PTZ + %d fixe%s (modèle à définir)" % (len(fixes), "s" if len(fixes) > 1 else "")
        out.append((lab, None))
        loose = [ch for ch in loose if ch not in ptz and ch not in fixes]
    for ch in loose:
        m, lens = ch.get("lens_model"), ch.get("lens_obj")
        if m:
            out.append((device_label(m, lens), m))
        elif pm:
            out.append((device_label(pm), pm))
        else:
            out.append(("Caméra (modèle et optique à définir)", None))
    return out


def pub(m, usage=None):
    ds = m.get("datasheet") or None
    return {"_id": str(m["_id"]), "brand": m.get("brand", ""), "ref": m.get("ref", ""), "name": name(m),
            "match": m.get("match", ""), "multi": bool(m.get("multi")), "note": m.get("note", ""),
            "lenses": [{k: x.get(k) for k in ("k", "label", "optics", "fov", "range", "count")} for x in m.get("lenses") or []],
            "datasheet": {"name": ds.get("name", ""), "size": ds.get("size", 0),
                          "at": (ds["at"] if ds["at"].tzinfo else ds["at"].replace(tzinfo=timezone.utc)).isoformat()
                          if isinstance(ds.get("at"), datetime) else ds.get("at")} if ds else None,
            "used": (usage or {}).get(m["_id"], 0)}


def usage(db, models):
    """{_id modele: nombre de cameras en projet qui l'utilisent} (modele de
    l'emplacement ou objectif d'un canal)."""
    idx = lens_index(models)
    out = {}
    for d in db["cameras_planned"].find({}, {"model_id": 1, "channels": 1}):
        used = {d["model_id"]} if d.get("model_id") else set()
        used |= {idx[c["model"]][0]["_id"] for c in d.get("channels") or [] if c.get("model") in idx}
        for o in used:
            out[o] = out.get(o, 0) + 1
    return out


def lens_in_use(db, key):
    return db["cameras_planned"].count_documents({"channels.model": key})


def _num(v, lo, hi, what):
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("%s invalide" % what)
    if not lo <= n <= hi:
        raise ValueError("%s invalide (%g a %g)" % (what, lo, hi))
    return n


def clean(data, current=None, all_keys=()):
    """Champs d'un modele saisis dans la configuration -> (champs, erreur).
    Les objectifs gardent leur cle (k) ; un nouvel objectif en recoit une."""
    current = current or {}
    f = {}
    ref = str(data.get("ref", current.get("ref", "")) or "").strip()[:80]
    if not ref:
        return None, "Reference du modele requise"
    f["ref"] = ref
    f["brand"] = str(data.get("brand", current.get("brand", "")) or "").strip()[:40]
    f["match"] = re.sub(r"\s+", "", str(data.get("match", current.get("match", "")) or ""))[:40]
    f["multi"] = bool(data.get("multi", current.get("multi", False)))
    f["note"] = str(data.get("note", current.get("note", "")) or "").strip()[:300]
    raw = data.get("lenses", current.get("lenses"))
    if not isinstance(raw, list) or not raw:
        return None, "Au moins un objectif"
    if len(raw) > MAX_LENSES:
        return None, "%d objectifs au plus" % MAX_LENSES
    own = {x["k"] for x in current.get("lenses") or []}
    taken = set(all_keys) - own
    lenses = []
    try:
        for x in raw:
            if not isinstance(x, dict):
                raise ValueError("Objectif invalide")
            label = str(x.get("label") or "").strip()[:60]
            if not label:
                raise ValueError("Nom de l'objectif requis (ex. fixe 2,8 mm, PTZ)")
            if x.get("optics") not in OPTICS:
                raise ValueError("Optique de l'objectif %s : mobile ou fixe" % label)
            k = str(x.get("k") or "")
            if not k or k not in own or k in {y["k"] for y in lenses}:
                k = "m" + secrets.token_hex(4)
                while k in taken:
                    k = "m" + secrets.token_hex(4)
            lenses.append({"k": k, "label": label, "optics": x["optics"],
                           "fov": round(_num(x.get("fov"), 1, 360, "Ouverture de %s" % label), 1),
                           "range": int(round(_num(x.get("range"), 1, 5000, "Portee de %s" % label))),
                           "count": int(_num(x.get("count") or 1, 1, 8, "Nombre de %s" % label)) if f["multi"] else 1})
    except ValueError as e:
        return None, str(e)
    if f["multi"] and sum(x["count"] for x in lenses) > 8:
        return None, "8 canaux au plus par appareil"
    f["lenses"] = lenses
    return f, None


def datasheet_path(model_id):
    return os.path.join(DATASHEET_DIR, "%s.pdf" % model_id)
