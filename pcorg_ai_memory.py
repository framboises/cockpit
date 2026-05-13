"""Memoire constitutionnelle de l'Assistant IA (Cockpit).

Stocke des directives durables (principes, vocabulaire, corrections,
contexte) que l'utilisateur valide au fil des rapports. Ces directives
sont injectees dans le system prompt des futurs appels Claude pour
professionnaliser progressivement les rapports.

Modele de donnees - collection `pcorg_ai_memory` :

    {
      _id: uuid hex,
      type: "principe" | "correction" | "vocabulaire" | "contexte",
      scope: {
        event:   "24H AUTOS" | None,       # None = global tout evenement
        section: "synthese" | ... | None,  # None = toutes sections
        phase:   "montage" | "course" | "demontage" | None,
        year:    int | None                # rarement utilise
      },
      content: "Tribune Mulsanne : altercations recurrentes...",
      source: {
        summary_id: "abc..." | None,
        feedback_index: int | None,
        original_comment: "..." | None
      },
      active: bool,
      weight: float (1.0),    # pour pondererer si besoin
      created_by, created_by_name, created_at, updated_at,
      used_count: int
    }

Conventions :
- Une directive avec scope.event=None s'applique a TOUS les evenements.
- Une directive avec scope.section=None s'applique a TOUTES les sections.
- L'injection prompt charge les directives matchant (scope.event in [event, None])
  AND (scope.section in [section, None]). C'est volontairement permissif : on
  prefere ajouter du contexte plutot que de manquer une regle pertinente.
- Plafond defensif : MAX_DIRECTIVES_PER_PROMPT pour eviter d'exploser le prompt.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)


COLLECTION = "pcorg_ai_memory"

# Types reconnus pour le champ `type`. Valeurs libres tolerees mais non
# encouragees (faciliter le filtrage et la presentation cote UI).
ALLOWED_TYPES = ("principe", "correction", "vocabulaire", "contexte")

# Sections valides (alignees sur pcorg_summary.SECTION_KEYS).
ALLOWED_SECTIONS = (
    "synthese", "faits_marquants", "secours", "securite",
    "technique", "flux", "fourriere", "recommandations", "prochaines_24h",
)

# Phases valides pour le scoping fin (montage / course / demontage). On
# laisse `None` quand non specifie.
ALLOWED_PHASES = ("montage", "course", "demontage")

# Plafond defensif : au-dela, le bloc devient trop long et noie le system.
# Si on depasse, on garde les directives les plus utilisees (used_count) et
# les plus recentes. Un warning est emis pour rappeler de fusionner.
MAX_DIRECTIVES_PER_PROMPT = 50

# Longueur max d'une directive : si trop longue, signal que c'est une
# specification complete plutot qu'une directive concise. On previent.
MAX_CONTENT_CHARS = 600


_indexes_ensured = False


def _ensure_indexes(db):
    global _indexes_ensured
    if _indexes_ensured:
        return
    try:
        db[COLLECTION].create_index(
            [("active", ASCENDING), ("scope.event", ASCENDING), ("scope.section", ASCENDING)],
            name="active_scope",
        )
        db[COLLECTION].create_index([("created_at", DESCENDING)], name="created_at")
        db[COLLECTION].create_index([("used_count", DESCENDING)], name="used_count")
        _indexes_ensured = True
    except Exception as e:
        logger.warning("ai_memory: creation index a echoue : %s", e)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _normalize_scope(scope: Optional[dict]) -> dict:
    """Normalise un dict scope : seuls les champs reconnus sont conserves,
    chaines vides converties en None.
    """
    s = scope or {}
    out = {}
    for k in ("event", "section", "phase", "year"):
        v = s.get(k)
        if v == "" or v is None:
            out[k] = None
        elif k == "year":
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = None
        else:
            out[k] = str(v).strip() or None
    # Validation soft : sections/phases inconnues -> log warning mais accept.
    if out.get("section") and out["section"] not in ALLOWED_SECTIONS:
        logger.warning("ai_memory: section inconnue '%s' (toleree)", out["section"])
    if out.get("phase") and out["phase"] not in ALLOWED_PHASES:
        logger.warning("ai_memory: phase inconnue '%s' (toleree)", out["phase"])
    return out


def _normalize_type(t: Optional[str]) -> str:
    if not t:
        return "principe"
    s = str(t).strip().lower()
    return s if s in ALLOWED_TYPES else "principe"


# ----------------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------------

def create_directive(db, content, type_=None, scope=None, source=None,
                     created_by_email=None, created_by_name=None,
                     active=True, weight=1.0):
    """Cree une directive. Retourne le doc insere.

    Leve ValueError si content vide.
    """
    _ensure_indexes(db)
    txt = (content or "").strip()
    if not txt:
        raise ValueError("content vide")
    if len(txt) > MAX_CONTENT_CHARS:
        logger.warning("ai_memory: directive tres longue (%d chars) -> envisager "
                       "de la decouper", len(txt))
    now = datetime.now(timezone.utc)
    doc = {
        "_id": uuid.uuid4().hex,
        "type": _normalize_type(type_),
        "scope": _normalize_scope(scope),
        "content": txt,
        "source": source or {},
        "active": bool(active),
        "weight": float(weight) if weight is not None else 1.0,
        "created_by": created_by_email or "",
        "created_by_name": created_by_name or "",
        "created_at": now,
        "updated_at": now,
        "used_count": 0,
    }
    db[COLLECTION].insert_one(doc)
    return doc


def update_directive(db, directive_id, patch, updated_by_email=None):
    """Met a jour les champs alterables (content, type, scope, active, weight).

    Retourne le doc apres update ou None si introuvable.
    """
    _ensure_indexes(db)
    set_ops = {"updated_at": datetime.now(timezone.utc)}
    if updated_by_email:
        set_ops["updated_by"] = updated_by_email
    if "content" in patch:
        v = (patch["content"] or "").strip()
        if not v:
            raise ValueError("content vide")
        set_ops["content"] = v
    if "type" in patch:
        set_ops["type"] = _normalize_type(patch["type"])
    if "scope" in patch:
        set_ops["scope"] = _normalize_scope(patch["scope"])
    if "active" in patch:
        set_ops["active"] = bool(patch["active"])
    if "weight" in patch:
        try:
            set_ops["weight"] = float(patch["weight"])
        except (TypeError, ValueError):
            pass
    res = db[COLLECTION].find_one_and_update(
        {"_id": directive_id},
        {"$set": set_ops},
        return_document=True,
    )
    return res


def delete_directive(db, directive_id):
    """Supprime physiquement une directive. Reserve admin."""
    return db[COLLECTION].delete_one({"_id": directive_id}).deleted_count > 0


def get_directive(db, directive_id):
    return db[COLLECTION].find_one({"_id": directive_id})


def list_directives(db, event=None, section=None, active_only=False,
                    type_=None, limit=200):
    """Liste les directives selon filtres. Tri : used_count desc puis recente.

    event/section : valeur exacte attendue OU None pour 'pas de filtre'.
    Pour matcher 'directives globales applicables a event X' (scope.event ==
    event OR scope.event is None), passer event mais utiliser
    `load_active_directives` qui implemente cette semantique.
    """
    _ensure_indexes(db)
    q = {}
    if active_only:
        q["active"] = True
    if event is not None:
        q["scope.event"] = event
    if section is not None:
        q["scope.section"] = section
    if type_:
        q["type"] = type_
    return list(
        db[COLLECTION].find(q)
            .sort([("used_count", DESCENDING), ("created_at", DESCENDING)])
            .limit(int(limit))
    )


# ----------------------------------------------------------------------------
# Chargement pour injection prompt
# ----------------------------------------------------------------------------

def load_active_directives(db, event=None, section=None, phase=None, year=None,
                            max_count=MAX_DIRECTIVES_PER_PROMPT):
    """Charge les directives actives applicables au contexte (event, section,
    phase, year). Semantique permissive : une directive de scope plus large
    (event=None par ex.) s'applique aussi.

    Tri : weight desc, used_count desc, created_at desc.
    """
    _ensure_indexes(db)
    q = {"active": True}

    # event : match exact ou None (global)
    if event is not None:
        q["scope.event"] = {"$in": [event, None]}
    # sinon (event=None dans le rapport), on prend uniquement les directives
    # globales pour eviter de melanger les events
    else:
        q["scope.event"] = None

    if section is not None:
        q["scope.section"] = {"$in": [section, None]}
    if phase is not None:
        q["scope.phase"] = {"$in": [phase, None]}
    if year is not None:
        try:
            q["scope.year"] = {"$in": [int(year), None]}
        except (TypeError, ValueError):
            q["scope.year"] = None

    cur = db[COLLECTION].find(q).sort([
        ("weight", DESCENDING),
        ("used_count", DESCENDING),
        ("created_at", DESCENDING),
    ]).limit(int(max_count) + 1)

    items = list(cur)
    overflow = len(items) > max_count
    if overflow:
        logger.warning(
            "ai_memory: %d directives matchent le scope (event=%s section=%s) "
            "-> tronquees a %d. Envisager fusion/archivage.",
            len(items), event, section, max_count,
        )
        items = items[:max_count]
    return items, overflow


def format_directives_block(directives) -> str:
    """Formate la liste des directives en bloc texte injecte dans le system
    prompt. Retourne '' si liste vide.

    Format :
    ---
    Connaissance accumulee et bonnes pratiques (directives validees par les
    operateurs PC Org au fil des rapports precedents, a appliquer
    systematiquement quand le contexte s'y prete) :

    - [PRINCIPE | scope: 24H AUTOS, section: flux]
      Texte de la directive 1.

    - [VOCABULAIRE | global]
      Texte de la directive 2.

    ...
    ---
    """
    if not directives:
        return ""
    lines = [
        "Connaissance accumulee et bonnes pratiques (directives validees par "
        "les operateurs PC Org au fil des rapports precedents, a appliquer "
        "systematiquement quand le contexte s'y prete) :",
    ]
    for d in directives:
        scope = d.get("scope") or {}
        scope_parts = []
        if scope.get("event"):
            scope_parts.append("event: " + str(scope["event"]))
        if scope.get("section"):
            scope_parts.append("section: " + str(scope["section"]))
        if scope.get("phase"):
            scope_parts.append("phase: " + str(scope["phase"]))
        if scope.get("year"):
            scope_parts.append("year: " + str(scope["year"]))
        scope_str = ", ".join(scope_parts) if scope_parts else "global"
        type_str = str(d.get("type") or "principe").upper()
        content = (d.get("content") or "").strip()
        lines.append("")
        lines.append("- [" + type_str + " | " + scope_str + "]")
        lines.append("  " + content)
    return "\n".join(lines)


def increment_usage(db, directive_ids):
    """Incremente used_count pour les directives effectivement injectees."""
    if not directive_ids:
        return
    try:
        db[COLLECTION].update_many(
            {"_id": {"$in": list(directive_ids)}},
            {"$inc": {"used_count": 1}},
        )
    except Exception as e:
        logger.warning("ai_memory: increment used_count a echoue : %s", e)


# ----------------------------------------------------------------------------
# Promotion depuis un feedback
# ----------------------------------------------------------------------------

def promote_from_feedback(db, summary_id, feedback_entry, rule_text,
                          scope=None, type_="principe",
                          created_by_email=None, created_by_name=None):
    """Cree une directive a partir d'un feedback de rapport.

    feedback_entry : dict serialisable (sera reference dans source).
    rule_text : texte final de la directive (peut etre different du commentaire
    brut, par exemple reformule par l'utilisateur).
    """
    src = {
        "summary_id": summary_id,
        "section": (feedback_entry or {}).get("section"),
        "target": (feedback_entry or {}).get("target"),
        "original_comment": (feedback_entry or {}).get("comment")
            or (feedback_entry or {}).get("rule_text"),
        "original_text": (feedback_entry or {}).get("original_text"),
    }
    return create_directive(
        db,
        content=rule_text,
        type_=type_,
        scope=scope,
        source=src,
        created_by_email=created_by_email,
        created_by_name=created_by_name,
        active=True,
    )


# ----------------------------------------------------------------------------
# Statistiques
# ----------------------------------------------------------------------------

def stats(db):
    """Retourne quelques compteurs pour la page admin."""
    _ensure_indexes(db)
    total = db[COLLECTION].count_documents({})
    active = db[COLLECTION].count_documents({"active": True})
    by_type = {}
    for r in db[COLLECTION].aggregate([
        {"$match": {"active": True}},
        {"$group": {"_id": "$type", "n": {"$sum": 1}}},
    ]):
        by_type[str(r.get("_id") or "?")] = int(r.get("n") or 0)
    by_event = {}
    for r in db[COLLECTION].aggregate([
        {"$match": {"active": True}},
        {"$group": {"_id": "$scope.event", "n": {"$sum": 1}}},
    ]):
        ev = r.get("_id") or "_global"
        by_event[str(ev)] = int(r.get("n") or 0)
    by_section = {}
    for r in db[COLLECTION].aggregate([
        {"$match": {"active": True}},
        {"$group": {"_id": "$scope.section", "n": {"$sum": 1}}},
    ]):
        sc = r.get("_id") or "_all"
        by_section[str(sc)] = int(r.get("n") or 0)
    most_used = list(
        db[COLLECTION].find({"active": True}, {"_id": 1, "content": 1, "used_count": 1, "scope": 1})
            .sort("used_count", DESCENDING)
            .limit(5)
    )
    return {
        "total": total,
        "active": active,
        "inactive": total - active,
        "by_type": by_type,
        "by_event": by_event,
        "by_section": by_section,
        "most_used": [
            {
                "id": str(d.get("_id")),
                "content": (d.get("content") or "")[:160],
                "used_count": int(d.get("used_count") or 0),
                "scope": d.get("scope") or {},
            }
            for d in most_used
        ],
    }


def suggest_rule_from_comment(comment, section=None, event=None,
                              original_text=None, corrected_text=None,
                              model=None):
    """Reformule un commentaire libre en directive concise (~1 phrase).

    Utilise Claude Haiku (rapide, ~256 tokens, cout negligeable). Retourne
    une chaine ou leve ClaudeError (importee depuis pcorg_summary).

    Le contexte fourni :
    - section ciblee (synthese, recommandations, ...) + event si dispo
    - texte original Claude (ce qui etait dans le rapport)
    - texte corrige si l'utilisateur a edite (sinon le commentaire seul)
    """
    import pcorg_summary  # import lazy pour eviter cycle

    section_label = {
        "synthese": "Synthese", "faits_marquants": "Faits marquants",
        "secours": "Secours", "securite": "Securite",
        "technique": "Technique", "flux": "Flux", "fourriere": "Fourriere",
        "recommandations": "Recommandations", "prochaines_24h": "Prochaines 24h",
    }.get(section, section or "(toutes sections)")

    system = (
        "Tu es un assistant qui transforme des retours libres d'operateurs PC "
        "Organisation en DIRECTIVES CONCISES pour un autre assistant IA qui "
        "redige les rapports d'evenement. Les directives sont injectees dans "
        "le system prompt de l'IA aux futurs rapports.\n"
        "\n"
        "Contraintes :\n"
        "- Reponds par UNE SEULE PHRASE en francais, mode imperatif ou "
        "declaratif, MAX 200 caracteres.\n"
        "- Pas de preambule, pas de guillemets, pas de markdown, pas de "
        "puces, pas de retour a la ligne.\n"
        "- Reste actionnable et concret : si le commentaire mentionne un "
        "lieu, une heure, une procedure, conserve l'information.\n"
        "- Pas de 'Il faut...', 'Penser a...', 'Ne pas oublier...' : ecris "
        "comme une regle directe ('Pour 24H Autos en synthese, mentionner "
        "systematiquement le pic veille avec son heure et son delta vs N-1').\n"
        "- N'invente pas d'information : si le commentaire est vague, garde "
        "le vague mais reste actionnable.\n"
        "- Aucune apostrophe ou guillemet typographique."
    )

    parts = []
    if event:
        parts.append("Evenement : " + str(event))
    parts.append("Section ciblee : " + str(section_label))
    if original_text:
        parts.append("Texte Claude (original) :\n" + str(original_text)[:1000])
    if corrected_text:
        parts.append("Texte corrige (utilisateur) :\n" + str(corrected_text)[:1000])
    parts.append("Commentaire utilisateur :\n" + str(comment or "")[:1500])
    parts.append("\nProduis la directive (une phrase, max 200 caracteres) :")
    user = "\n\n".join(parts)

    use_model = pcorg_summary._validate_model(model) or "claude-haiku-4-5"
    raw_text, usage, _stop = pcorg_summary._claude_stream_request(
        system, user, max_tokens=256, model=use_model,
        system_cache=False,  # bloc unique, pas la peine de cacher
    )
    rule = (raw_text or "").strip()
    # Nettoyages defensifs : si Claude a quand meme ajoute des guillemets ou
    # un saut de ligne, on tronque proprement.
    rule = rule.strip('"\'')
    if "\n" in rule:
        rule = rule.split("\n", 1)[0].strip()
    return rule, usage


def serialize(d):
    """Forme JSON-friendly d'une directive (pour les routes)."""
    if not d:
        return None
    return {
        "id": str(d.get("_id", "")),
        "type": d.get("type"),
        "scope": d.get("scope") or {},
        "content": d.get("content") or "",
        "source": d.get("source") or {},
        "active": bool(d.get("active", True)),
        "weight": float(d.get("weight") or 1.0),
        "created_by": d.get("created_by"),
        "created_by_name": d.get("created_by_name"),
        "created_at": d.get("created_at").isoformat() if hasattr(d.get("created_at"), "isoformat") else d.get("created_at"),
        "updated_at": d.get("updated_at").isoformat() if hasattr(d.get("updated_at"), "isoformat") else d.get("updated_at"),
        "used_count": int(d.get("used_count") or 0),
    }
