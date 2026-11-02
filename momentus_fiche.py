"""momentus_fiche.py - Fiche d'exploitation d'un evenement Momentus (lecture seule).

Module pur (ni Flask ni connexion propre : `db` en argument).
⚠️ COPIE IDENTIQUE dans cockpit et looker (momentus_fiche.py, a la racine) :
toute modification se reporte dans les deux fichiers.

Repond a « de quoi a besoin cet evenement pour tourner ? », pour les
organisateurs (PC, regie, production), jamais pour le client :
  - en-tete : nom, statut, type, dates, effectif, equipe ACO (noms, sans mail) ;
  - resume : securite, regie, materiel, plans ;
  - espaces jour par jour avec la phase et le PILOTE (spaceUsageName) ;
  - personnel commande, grille de presence au format Bibler (48 plages de
    30 min par jour) ou jours seuls quand Momentus n'a pas d'heures ;
  - bon de preparation du materiel par categorie ;
  - plans et etats des lieux ;
  - alertes : pas de plan, personnel sans heures, stock depasse ce jour-la
    (tous evenements confondus).

Donnees : collections de cockpit/momentus_details.py (momentus_personnel,
momentus_item_allocations, momentus_booked_spaces, momentus_documents,
momentus_items) et de momentus_sync.py (momentus_events, momentus_functions).
REGLE : rien qui touche a l'argent (deja retire en base), ni contact client
(contactRoles jamais lu), ni mail du personnel ACO.
"""

import re
from datetime import date, datetime, timezone

import momentus_lieux as ML

EVENT_ID_RE = re.compile(r"event-[0-9]{1,9}-[A-Z]{1,3}")
DOC_ID_RE = re.compile(r"[0-9a-f]{32}")

LABOR = "labor"
MAX_JOURS = 62           # au-dela, la grille de presence n'est pas detaillee
MAX_LIGNES = 400         # garde-fou d'affichage par section

ALIVE = {"_sync.deleted_at": None}

# Libelle de phase d'un espace : le pilote saisi dit souvent mieux que usageType
# ("Montage par Julien" saisi en moveIn = montage, pas "reserve").
_PILOTE_PHASE = (
    (re.compile(r"d[ée]montage", re.I), "demontage"),
    (re.compile(r"montage|installation", re.I), "montage"),
    (re.compile(r"r[ée]gie|exploitation", re.I), "exploitation"),
    (re.compile(r"maintenance|travaux", re.I), "travaux"),
)
_PILOTE_NOM = re.compile(r"(?:r[ée]gie|montage(?: par)?|suivi)\s+([A-ZÉÈ][\w\-éèëêïîôûç]+)", re.I)
_NON_NOM = {"du", "de", "le", "la", "client", "par"}


def _jour(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _txt(v, n=300):
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return s[:n]


def phase_espace(usage_type, pilote):
    for rx, ph in _PILOTE_PHASE:
        if pilote and rx.search(pilote):
            return ph
    return ML.PHASES.get(usage_type, "reserve")


def pilotes_de(texte):
    """Prenoms des pilotes cites ("Regie Julien", "Montage du client (suivi Max)")."""
    out = []
    for m in _PILOTE_NOM.finditer(texte or ""):
        nom = m.group(1)
        if nom.lower() not in _NON_NOM and nom not in out:
            out.append(nom)
    return out


def _presences(doc):
    """{date: [48 entiers]} depuis donnees_presences (format Bibler)."""
    out = {}
    for d in doc.get("donnees_presences") or []:
        plages = d.get("plages_horaires") or []
        if len(plages) == 48 and d.get("date"):
            out[d["date"]] = [int(p.get("nombre_personnes") or 0) for p in plages]
    return out


def _quantite(q):
    if not isinstance(q, (int, float)):
        return None
    return int(q) if float(q).is_integer() else round(float(q), 2)


def _famille(metier, categorie):
    """securite | ssiap | regie | accueil | autre (couleur et resume)."""
    m = str(metier or "").lower()
    t = f"{m} {categorie}".lower()
    if "ssiap" in m:
        return "ssiap"
    # Le metier d'abord : un placier range en "Circuit SECURITE" reste un placier.
    if "hôtesse" in m or "hotesse" in m or "placier" in m or "accueil" in m:
        return "accueil"
    if "régisseur" in m or "regisseur" in m:
        return "regie"
    if "sécurité" in m or "securite" in m or "agent" in m:
        return "securite"
    if "ssiap" in t:
        return "ssiap"
    if "régisseur" in t or "regisseur" in t:
        return "regie"
    if "sécurité" in t or "securite" in t:
        return "securite"
    return "autre"


def _stock_alertes(db, items_stock, lignes_evt, statut_exclus):
    """Depassements de stock les jours ou l'evenement utilise un article a stock."""
    alertes = []
    besoins = {}
    for l in lignes_evt:
        iid = l.get("itemId")
        if iid in items_stock:
            for j in l.get("jours") or []:
                besoins.setdefault((iid, j), 0)
    if not besoins:
        return alertes
    ids = list({k[0] for k in besoins})
    jours = list({k[1] for k in besoins})
    totaux, qui = {}, {}
    for a in db["momentus_item_allocations"].find(
            {**ALIVE, "itemId": {"$in": ids}, "jours": {"$in": jours}},
            {"itemId": 1, "jours": 1, "quantite": 1, "eventId": 1, "eventName": 1}):
        if a.get("eventId") in statut_exclus:
            continue
        for j in a.get("jours") or []:
            k = (a["itemId"], j)
            if k in besoins:
                totaux[k] = totaux.get(k, 0) + (a.get("quantite") or 0)
                qui.setdefault(k, set()).add(_txt(a.get("eventName"), 80))
    for (iid, j), tot in sorted(totaux.items(), key=lambda x: (x[0][1], x[0][0])):
        nom, stock = items_stock[iid]
        if stock and tot > stock:
            alertes.append({"type": "stock", "niveau": "critique" if tot >= 1.5 * stock else "attention",
                            "date": j, "article": nom, "demande": _quantite(tot), "stock": stock,
                            "evenements": sorted(qui.get((iid, j), []))[:6]})
    return alertes[:20]


def fiche_payload(db, event_id):
    """Fiche d'exploitation d'un evenement. None si l'evenement n'existe pas."""
    ev = db["momentus_events"].find_one(
        {"_id": event_id},
        {"name": 1, "eventTypeName": 1, "accountName": 1, "start": 1, "end": 1, "isDefinite": 1,
         "isTentative": 1, "isProspect": 1, "isCanceled": 1, "isLost": 1, "isInternal": 1, "isBlackout": 1,
         "estimatedTotalAttendance": 1, "estimatedAttendance": 1, "staffAssignments": 1, "_sync": 1,
         "lastModifiedOn": 1})
    if not ev:
        return None
    nom = _txt(ev.get("name"), 160) or "Reservation"
    client = _txt(ev.get("accountName"), 120)

    # --- espaces : un segment par espace reserve et par jour -----------------
    espaces, pilotes = {}, []
    jours = set()
    for bs in db["momentus_booked_spaces"].find({**ALIVE, "eventId": event_id}).sort("startDate", 1).limit(2000):
        a, b = _jour(bs.get("startDate")), _jour(bs.get("endDate"))
        if not a:
            continue
        b = b or a
        pil = _txt(bs.get("spaceUsageName"), 80) or None
        for p in pilotes_de(pil):
            if p not in pilotes:
                pilotes.append(p)
        rid = bs.get("roomId") or bs.get("roomName")
        e = espaces.setdefault(rid, {"roomId": bs.get("roomId"), "nom": _txt(bs.get("roomName"), 80) or "Espace",
                                     "segments": []})
        e["segments"].append({
            "debut": a.isoformat(), "fin": b.isoformat(),
            "heureDebut": bs.get("startTime"), "heureFin": bs.get("endTime"),
            "journee": bool(bs.get("isAllDay")) or not bs.get("startTime"),
            "phase": phase_espace(bs.get("usageType"), pil), "pilote": pil,
            "statut": bs.get("bookedStatus"),
        })
        if (b - a).days <= MAX_JOURS:
            jours.update((a.fromordinal(o)).isoformat() for o in range(a.toordinal(), b.toordinal() + 1))
    mapping = {m["_id"]: m.get("feature_id") for m in db[ML.COL_MAPPING].find(
        {"_id": {"$in": [e["roomId"] for e in espaces.values() if e["roomId"]]}, "status": "valide"},
        {"feature_id": 1})}
    liste_espaces = []
    for e in espaces.values():
        e["featureId"] = mapping.get(e["roomId"])
        e["segments"].sort(key=lambda s: (s["debut"], s["heureDebut"] or ""))
        liste_espaces.append(e)
    liste_espaces.sort(key=lambda e: (e["segments"][0]["debut"], e["nom"]))

    # --- programme (fonctions avec un creneau ou un lieu) --------------------
    programme = []
    for f in db["momentus_functions"].find({**ALIVE, "eventId": event_id}).sort(
            [("startDate", 1), ("startTime", 1)]).limit(MAX_LIGNES):
        if f.get("functionStatus") == "canceled":
            continue
        if not f.get("startTime") and not f.get("roomName"):
            continue  # fonctions "Details ..." : elles portent le materiel, pas un creneau
        programme.append({"date": str(f.get("startDate") or "")[:10], "heureDebut": f.get("startTime"),
                          "heureFin": f.get("endTime"), "nom": _txt(f.get("name"), 160),
                          "type": _txt(f.get("functionTypeName"), 60) or None,
                          "lieu": _txt(f.get("roomName"), 80) or None,
                          "personnes": f.get("guaranteedAttendance") or f.get("expectedAttendance")
                          or f.get("agreedAttendance")})
        if f.get("startDate"):
            jours.add(str(f["startDate"])[:10])

    # --- personnel ------------------------------------------------------------
    personnel = []
    for p in db["momentus_personnel"].find({**ALIVE, "eventId": event_id}).limit(MAX_LIGNES):
        pres = _presences(p)
        personnel.append({
            "id": p["_id"], "metier": _txt(p.get("metier"), 80), "categorie": _txt(p.get("categorie"), 80) or None,
            "famille": _famille(p.get("metier") or "", p.get("categorie") or ""),
            "lieu": _txt(p.get("lieu"), 80) or None, "fonction": _txt(p.get("functionName"), 120) or None,
            "nombre": _quantite(p.get("nombre_personnes")), "unite": p.get("unite"),
            "duree": _quantite(p.get("duree")), "horaires": p.get("horaires"),
            "debut": p.get("debut"), "fin": p.get("fin"), "jours": p.get("jours") or [],
            "note": _txt(p.get("note"), 400) or None, "package": _txt(p.get("package"), 80) or None,
            "presences": pres,
        })
        jours.update(j for j in p.get("jours") or [])
    personnel.sort(key=lambda x: ((x["jours"] or ["9999"])[0], x["debut"] or "", x["famille"], x["metier"]))

    # --- materiel -------------------------------------------------------------
    lignes = list(db["momentus_item_allocations"].find(
        {**ALIVE, "eventId": event_id, "categoryType": {"$ne": LABOR}},
        {"name": 1, "itemId": 1, "quantite": 1, "unitName": 1, "categoryName": 1, "departmentName": 1,
         "categoryType": 1, "lieu": 1, "functionName": 1, "functionRoomName": 1, "jours": 1, "note": 1,
         "packageName": 1, "startTime": 1, "endTime": 1}).limit(MAX_LIGNES))
    groupes = {}
    for l in lignes:
        cat = _txt(l.get("categoryName"), 80) or "Sans categorie"
        g = groupes.setdefault(cat, {"categorie": cat, "departement": l.get("departmentName"),
                                     "type": l.get("categoryType"), "lignes": []})
        g["lignes"].append({"nom": _txt(l.get("name"), 120), "quantite": _quantite(l.get("quantite")),
                            "unite": l.get("unitName"), "lieu": _txt(l.get("lieu") or l.get("functionRoomName"), 80) or None,
                            "fonction": _txt(l.get("functionName"), 120) or None, "jours": l.get("jours") or [],
                            "heureDebut": l.get("startTime"), "heureFin": l.get("endTime"),
                            "note": _txt(l.get("note"), 300) or None, "package": _txt(l.get("packageName"), 80) or None})
    for g in groupes.values():
        g["lignes"].sort(key=lambda x: ((x["jours"] or [""])[0], x["lieu"] or "", x["nom"]))
    # Restauration apres le materiel : les equipes terrain cherchent d'abord tables, son, nettoyage.
    materiel = sorted(groupes.values(), key=lambda g: (g["type"] == "foodAndBeverage", g["categorie"]))

    # --- documents ------------------------------------------------------------
    documents = [{"id": d["_id"], "nom": _txt(d.get("name"), 160), "dossier": d.get("folderName"),
                  "date": d.get("dateUploaded"), "par": _txt(d.get("uploadedByName"), 80) or None,
                  "taille": ((d.get("fichier") or {}).get("taille")),
                  "disponible": bool((d.get("fichier") or {}).get("sha256")),
                  "lien": d.get("fileUrl") if not d.get("isUploaded") else None}
                 for d in db["momentus_documents"].find({**ALIVE, "eventId": event_id}).sort("dateUploaded", -1).limit(50)]

    # --- resume et alertes ----------------------------------------------------
    def compte(fam):
        lst = [p for p in personnel if p["famille"] == fam]
        return {"commandes": len(lst),
                "personnes": sum(p["nombre"] or 0 for p in lst if p["unite"] not in ("Jour", "Heure")) or None,
                "avecHeures": sum(1 for p in lst if p["presences"])}
    resume = {
        "securite": compte("securite"), "ssiap": compte("ssiap"), "regie": compte("regie"),
        "accueil": compte("accueil"), "autrePersonnel": compte("autre"),
        "materiel": {"lignes": len(lignes), "categories": len(materiel)},
        "documents": {"plans": sum(1 for d in documents if (d["dossier"] or "").lower().startswith("plan")),
                      "etatsDesLieux": sum(1 for d in documents if "lieux" in (d["dossier"] or "").lower())},
        "pilotes": pilotes,
        "espaces": len(liste_espaces),
    }
    alertes = []
    if not resume["documents"]["plans"] and (liste_espaces or lignes):
        alertes.append({"type": "plan", "niveau": "attention", "texte": "Aucun plan dans Momentus."})
    sans_heures = [p for p in personnel if not p["presences"]]
    if sans_heures:
        alertes.append({"type": "horaires", "niveau": "attention",
                        "texte": f"{len(sans_heures)} commande(s) de personnel sans heures dans Momentus "
                                 f"(lire la note de chaque commande)."})
    besoin = sorted({e["nom"] for e in liste_espaces for s in e["segments"]
                     if s["pilote"] and re.search(r"n[ée]cessite", s["pilote"], re.I)})
    if besoin:
        alertes.append({"type": "pilote", "niveau": "attention",
                        "texte": "Régisseur nécessaire, pilote à désigner : " + ", ".join(besoin[:6])
                                 + (" ..." if len(besoin) > 6 else "")})
    elif liste_espaces and not pilotes:
        alertes.append({"type": "pilote", "niveau": "info", "texte": "Aucun pilote de montage ou de régie saisi sur les espaces."})
    exclus = {e["_id"] for e in db["momentus_events"].find({"$or": [{"isCanceled": True}, {"isLost": True}]}, {"_id": 1})}
    stock = {i["_id"]: (_txt(i.get("name"), 120), i.get("stockLevel"))
             for i in db["momentus_items"].find({"_id": {"$in": list({l.get("itemId") for l in lignes if l.get("itemId")})},
                                                 "stockLevel": {"$gt": 0}}, {"name": 1, "stockLevel": 1})}
    alertes.extend(_stock_alertes(db, stock, lignes, exclus))

    jours_tries = sorted(j for j in jours if _jour(j))
    if len(jours_tries) > MAX_JOURS:
        jours_tries = jours_tries[:MAX_JOURS]
    etat = db["momentus_sync_state"].find_one({"_id": "state"}, {"last_success_at": 1}) or {}
    synced = etat.get("last_success_at")
    return {
        "ok": True,
        "evenement": {
            "id": event_id, "nom": nom, "client": client if client.upper() != nom.upper() else None,
            "type": _txt(ev.get("eventTypeName"), 60) or None, "statut": ML.event_status(ev),
            "annule": bool(ev.get("isCanceled") or ev.get("isLost")), "interne": bool(ev.get("isInternal")),
            "debut": str(ev.get("start") or "")[:10] or None, "fin": str(ev.get("end") or "")[:10] or None,
            "personnes": ev.get("estimatedTotalAttendance") or ev.get("estimatedAttendance"),
            "equipe": [{"role": _txt(s.get("staffAssignmentName"), 60), "nom": _txt(s.get("staffMemberName"), 80)}
                       for s in ev.get("staffAssignments") or [] if s.get("staffMemberName")],
        },
        "jours": jours_tries,
        "premierJour": jours_tries[0] if jours_tries else None,
        "dernierJour": jours_tries[-1] if jours_tries else None,
        "resume": resume,
        "alertes": alertes,
        "espaces": liste_espaces,
        "programme": programme,
        "personnel": personnel,
        "materiel": materiel,
        "documents": documents,
        # UTC naif (Cockpit) ou avec fuseau (client tz_aware de Looker) : toujours "...Z".
        "synced_at": (synced.astimezone(timezone.utc).replace(tzinfo=None) if synced.tzinfo else synced).isoformat() + "Z"
        if isinstance(synced, datetime) else None,
    }


def document_meta(db, doc_id):
    """(document, chemin relatif du fichier) d'un plan / etat des lieux, ou (None, None)."""
    d = db["momentus_documents"].find_one({"_id": doc_id, **ALIVE})
    f = (d or {}).get("fichier") or {}
    if not d or not f.get("sha256") or not f.get("chemin"):
        return None, None
    return d, f["chemin"]
