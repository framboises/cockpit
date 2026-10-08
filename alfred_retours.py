"""Retours des operateurs sur les reponses d'Alfred (pouces du chat).

Le pouce est pose sur `alfred_chat_messages`, purge a 90 jours (TTL). Un
retour sert a corriger un outil (Cockpit), le prompt ou les garde-fous (VM),
et a nourrir le corpus de tests de la VM, voire un jeu d'entrainement : il
doit donc SURVIVRE a la purge. A chaque note, on fige ici un instantane
complet de l'echange (collection `alfred_chat_retours`, sans TTL) :
conversation jusqu'a la reponse, outils appeles avec les arguments RECUS par
Cockpit, ce que l'outil en a deduit et son resume.

L'export (`exporter`) est pseudonymise : ni e-mail ni nom d'operateur. Le
texte des questions reste tel que tape (un nom peut y figurer : a relire
avant de sortir l'export du service).

Fonctions pures et lectures Mongo, `db` en argument, aucun import Flask.
"""

import hashlib
import json
from datetime import datetime, timezone

COLLECTION = "alfred_chat_retours"
COL_MESSAGES = "alfred_chat_messages"
COL_TOOL_CALLS = "alfred_tool_calls"
HISTORIQUE_MAX = 10
COMMENTAIRE_MAX = 500
NOTE_ADMIN_MAX = 1000
ATTENDUE_MAX = 2000

MOTIFS = {
    "faux": "Faux",
    "incomplet": "Incomplet",
    "pas_compris": "Question mal comprise",
    "mauvais_evenement": "Mauvais événement ou date",
    "trop_long": "Trop long ou confus",
    "autre": "Autre",
}
STATUTS = ("nouveau", "traite", "ignore")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def request_id(msg_id):
    """Identifiant envoye au wrapper (alfred_chat._run_ask), recopie par la VM
    dans chaque appel d'outil."""
    return "chat-" + str(msg_id)[:12]


def _conversation(db, m):
    """Messages de la conversation jusqu'a la reponse notee incluse
    (reponses en erreur omises, comme ce que le wrapper a recu)."""
    docs = list(db[COL_MESSAGES].find(
        {"session_id": m.get("session_id"), "created_at": {"$lte": m["created_at"]}})
        .sort("created_at", -1).limit(HISTORIQUE_MAX * 2 + 1))
    docs.reverse()
    out = []
    for d in docs:
        if d.get("role") == "assistant" and d.get("status") != "done":
            continue
        c = (d.get("content") or "").strip()
        if c:
            out.append({"role": d.get("role"), "content": c})
    return out[-(HISTORIQUE_MAX + 1):]


def _appels(db, m):
    rid = request_id(m["_id"])
    out = []
    for c in db[COL_TOOL_CALLS].find({"request_id": rid}).sort("created_at", 1).limit(20):
        out.append({"outil": c.get("tool"), "args": c.get("args"), "resolu": c.get("resolu"),
                    "resume": c.get("resume"), "ok": c.get("ok"),
                    "duree_ms": c.get("duration_ms")})
    return out


def figer(db, m):
    """Cree ou met a jour l'instantane d'une reponse notee. Les champs de
    traitement (statut, note admin, reponse attendue) ne sont pas touches."""
    conv = _conversation(db, m)
    question = next((x["content"] for x in reversed(conv) if x["role"] == "user"), "")
    ctx = {}
    for u in db[COL_MESSAGES].find({"session_id": m.get("session_id"), "role": "user",
                                    "created_at": {"$lte": m["created_at"]}}
                                   ).sort("created_at", -1).limit(1):
        ctx = u.get("context") or {}
    doc = {
        "message_id": m["_id"], "session_id": m.get("session_id"),
        "request_id": request_id(m["_id"]),
        "rating": m.get("rating"), "motif": m.get("rating_motif"),
        "commentaire": m.get("rating_comment"), "rated_at": m.get("rated_at") or _now(),
        "user_email": m.get("user_email"), "user_name": ctx.get("user_name"),
        "event": ctx.get("event"), "year": ctx.get("year"), "page": ctx.get("page"),
        "question": question, "reponse": m.get("content") or "", "conversation": conv,
        "outils_modele": m.get("tool_calls") or [], "appels_cockpit": _appels(db, m),
        "model": m.get("model"), "hops": m.get("hops"), "duration_ms": m.get("duration_ms"),
        "asked_at": m.get("created_at"), "maj_at": _now(),
    }
    db[COLLECTION].update_one(
        {"_id": m["_id"]},
        {"$set": doc, "$setOnInsert": {"statut": "nouveau", "created_at": _now()}},
        upsert=True)
    return doc


def retirer(db, msg_id):
    """Pouce annule : l'instantane disparait s'il n'a pas encore ete traite."""
    db[COLLECTION].delete_one({"_id": str(msg_id), "statut": "nouveau"})
    db[COLLECTION].update_one({"_id": str(msg_id)}, {"$set": {"rating": None, "maj_at": _now(),
                                                              "annule_par_operateur": True}})


def rattraper(db, limite=500):
    """Fige les notes posees avant l'existence de cette collection."""
    deja = {d["_id"] for d in db[COLLECTION].find({}, {"_id": 1})}
    n = 0
    for m in db[COL_MESSAGES].find({"role": "assistant", "rating": {"$in": [1, -1]}}).limit(limite):
        if m["_id"] not in deja:
            figer(db, m)
            n += 1
    return n


def filtre(rating=None, statut=None, motif=None):
    q = {}
    if str(rating) in ("1", "-1"):
        q["rating"] = int(rating)
    if statut in STATUTS:
        q["statut"] = statut
    if motif in MOTIFS:
        q["motif"] = motif
    return q


def compteurs(db):
    out = {"total": 0, "positifs": 0, "negatifs": 0, "nouveaux_negatifs": 0, "motifs": {}}
    for d in db[COLLECTION].find({"rating": {"$in": [1, -1]}}, {"rating": 1, "statut": 1, "motif": 1}):
        out["total"] += 1
        if d.get("rating") == 1:
            out["positifs"] += 1
        else:
            out["negatifs"] += 1
            if d.get("statut") == "nouveau":
                out["nouveaux_negatifs"] += 1
            k = d.get("motif") or "sans_motif"
            out["motifs"][k] = out["motifs"].get(k, 0) + 1
    return out


def traiter(db, rid, data, auteur):
    """Mise a jour admin : statut, note, reponse attendue. (doc, erreur)."""
    s = {}
    if "statut" in data:
        if data["statut"] not in STATUTS:
            return None, "statut_invalide"
        s["statut"] = data["statut"]
    if "note_admin" in data:
        s["note_admin"] = str(data.get("note_admin") or "").strip()[:NOTE_ADMIN_MAX] or None
    if "reponse_attendue" in data:
        s["reponse_attendue"] = str(data.get("reponse_attendue") or "").strip()[:ATTENDUE_MAX] or None
    if not s:
        return None, "rien_a_modifier"
    s["traite_par"] = auteur
    s["traite_at"] = s["maj_at"] = _now()
    r = db[COLLECTION].find_one_and_update({"_id": str(rid)}, {"$set": s}, return_document=True)
    if not r:
        return None, "introuvable"
    return r, None


def _iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def publier(d):
    """Vue de la page admin (l'admin voit l'operateur, pour le recontacter)."""
    return {
        "id": d["_id"], "request_id": d.get("request_id"),
        "rating": d.get("rating"), "motif": d.get("motif"),
        "motif_label": MOTIFS.get(d.get("motif") or "", None),
        "commentaire": d.get("commentaire"), "statut": d.get("statut"),
        "user_name": d.get("user_name") or d.get("user_email"),
        "event": d.get("event"), "year": d.get("year"), "page": d.get("page"),
        "question": d.get("question"), "reponse": d.get("reponse"),
        "conversation": d.get("conversation") or [],
        "outils_modele": d.get("outils_modele") or [],
        "appels_cockpit": d.get("appels_cockpit") or [],
        "model": d.get("model"), "hops": d.get("hops"), "duration_ms": d.get("duration_ms"),
        "asked_at": _iso(d.get("asked_at")), "rated_at": _iso(d.get("rated_at")),
        "note_admin": d.get("note_admin"), "reponse_attendue": d.get("reponse_attendue"),
        "traite_par": d.get("traite_par"), "traite_at": _iso(d.get("traite_at")),
    }


def _pseudo(email):
    return "op-" + hashlib.sha256(str(email or "").encode("utf-8")).hexdigest()[:8] if email else None


def exporter(db, q, annules=False):
    """Lignes JSONL pseudonymisees : un echange par ligne, au format messages
    (role/content) attendu par les outils d'evaluation et d'entrainement.
    annules=True (synchro VM) : un pouce retire sort avec note null, pour
    que la VM l'efface de son corpus."""
    for d in db[COLLECTION].find(q).sort("rated_at", 1):
        if d.get("rating") not in (1, -1) and not annules:
            continue
        yield json.dumps({
            "id": d["_id"], "request_id": d.get("request_id"),
            "operateur": _pseudo(d.get("user_email")),
            "date": _iso(d.get("asked_at")), "evenement": d.get("event"), "annee": d.get("year"),
            "page": d.get("page"),
            "note": d.get("rating"), "motif": d.get("motif"), "commentaire": d.get("commentaire"),
            "statut": d.get("statut"), "note_admin": d.get("note_admin"),
            "reponse_attendue": d.get("reponse_attendue"),
            "messages": d.get("conversation") or [],
            "outils_modele": d.get("outils_modele") or [],
            "appels_cockpit": d.get("appels_cockpit") or [],
            "modele": d.get("model"), "hops": d.get("hops"), "maj_at": _iso(d.get("maj_at")),
        }, ensure_ascii=False, default=str) + "\n"
