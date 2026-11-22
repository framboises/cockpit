"""Import de l'export Forms (xlsx) : upsert des candidats par (campagne, e-mail) et rapport."""
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import openpyxl

from formation import store
from formation.normalise import normaliser_email, normaliser_tel

_PARIS = ZoneInfo("Europe/Paris")
_COLONNES = {
    "id": "Id", "fin": "Heure de fin", "nom": "Nom1", "prenom": "Prénom",
    "tel": "Téléphone mobile", "email": "Adresse e-mail",
}
_CHAMPS_MAJ = ("forms_id", "nom", "prenom", "tel", "tel4", "forms_repondu_le")


def _txt(v):
    return "" if v is None else " ".join(str(v).split())


def _utc(v):
    """Datetime naive de l'export (heure de Paris) -> datetime aware UTC."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_PARIS)
        return v.astimezone(timezone.utc)
    return None


def _forms_id(v):
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return v


def _lire(fichier):
    src = io.BytesIO(fichier) if isinstance(fichier, (bytes, bytearray)) else fichier
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        lignes = ws.iter_rows(values_only=True)
        entete = next(lignes, None) or ()
        idx = {}
        for i, h in enumerate(entete):
            idx.setdefault(_txt(h), i)
        manquantes = [c for c in _COLONNES.values() if c not in idx]
        if manquantes:
            raise ValueError("colonnes absentes : " + ", ".join(manquantes))
        pos = {k: idx[v] for k, v in _COLONNES.items()}
        out = []
        for row in lignes:
            if row is None or all(c is None or _txt(c) == "" for c in row):
                continue
            out.append({k: (row[i] if i < len(row) else None) for k, i in pos.items()})
        return out
    finally:
        wb.close()


def _cle_recence(c):
    fin = c["forms_repondu_le"]
    return (fin or datetime.min.replace(tzinfo=timezone.utc), c["forms_id"] if isinstance(c["forms_id"], int) else 0)


def _egal(a, b):
    if isinstance(a, datetime) and isinstance(b, datetime):
        a = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
        b = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return a == b


def importer(db, fichier, auteur, now=None):
    now = now or datetime.now(timezone.utc)
    lignes = _lire(fichier)
    rapport = {"lignes": len(lignes), "crees": 0, "mis_a_jour": 0, "inchanges": 0,
               "doublons": [], "inexploitables": [], "absents_du_fichier": []}
    par_email = {}
    for l in lignes:
        fid = _forms_id(l["id"])
        email = normaliser_email(_txt(l["email"]))
        tel = normaliser_tel(l["tel"])
        if not email:
            rapport["inexploitables"].append({"forms_id": fid, "motif": "e-mail absent ou invalide"})
            continue
        if not tel:
            rapport["inexploitables"].append({"forms_id": fid, "motif": "mobile absent ou invalide"})
            continue
        par_email.setdefault(email, []).append({
            "forms_id": fid, "nom": _txt(l["nom"]), "prenom": _txt(l["prenom"]),
            "email": email, "tel": tel, "tel4": tel[-4:], "forms_repondu_le": _utc(l["fin"])})

    col = db["candidats"]
    for email, cands in par_email.items():
        cands.sort(key=_cle_recence)
        garde = cands[-1]
        if len(cands) > 1:
            rapport["doublons"].append({"email": email, "ids_forms": [c["forms_id"] for c in cands],
                                        "garde": garde["forms_id"]})
        existant = col.find_one({"campagne": store.CAMPAGNE, "email": email})
        champs = {k: garde[k] for k in _CHAMPS_MAJ}
        if existant is None:
            col.update_one(
                {"campagne": store.CAMPAGNE, "email": email},
                {"$set": dict(champs, maj_le=now),
                 "$setOnInsert": {"amenagement": False, "fictif": False, "importe_le": now}},
                upsert=True)
            rapport["crees"] += 1
        elif all(_egal(existant.get(k), champs[k]) for k in _CHAMPS_MAJ):
            rapport["inchanges"] += 1
        else:
            col.update_one({"_id": existant["_id"]}, {"$set": dict(champs, maj_le=now)})
            rapport["mis_a_jour"] += 1

    presents = set(par_email)
    rapport["absents_du_fichier"] = sorted(
        d["email"] for d in col.find({"campagne": store.CAMPAGNE, "fictif": {"$ne": True}}, {"email": 1})
        if d["email"] not in presents)

    db["imports"].insert_one(dict(rapport, campagne=store.CAMPAGNE, auteur=auteur, t=now))
    store.journal(db, "import", auteur, lignes=rapport["lignes"], crees=rapport["crees"],
                  mis_a_jour=rapport["mis_a_jour"], inchanges=rapport["inchanges"],
                  doublons=len(rapport["doublons"]), inexploitables=len(rapport["inexploitables"]))
    return rapport
