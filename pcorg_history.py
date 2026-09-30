"""
pcorg_history.py - Chronologie des fiches main courante (collection `pcorg`).

Module pur (ni Flask ni connexion Mongo propre) partage par :
  - app.py            (routes /api/pcorg/*)
  - field.py          (tablettes terrain)
  - uploads/pcorg/sync_pcorg_sql.py (synchro SQL Server -> Mongo)

Trois responsabilites :

1. Parser le champ texte `comment` des fiches SQL (format Prysm
   "dd/mm/yyyy HH:MM:SS , Operateur\\n texte") en entrees de chronologie.

2. Fusionner, a chaque synchro, la chronologie SQL avec les entrees ajoutees
   dans Cockpit ou depuis une tablette. La synchro est a sens unique
   (SQL -> Mongo) : sans fusion, chaque reecriture d'une ligne SQL effacait
   les commentaires, la cloture, le vehicule engage, la position et
   l'urgence saisis dans Cockpit (option A : Cockpit l'emporte sur ce qu'il
   a modifie, SQL sur le reste). Les champs modifies dans Cockpit sont
   listes dans `cockpit_owned`, que la synchro respecte.

3. Decorer la chronologie pour l'affichage : separer la ligne de statut
   ("Statut: En cours -> Termine"), les lignes de modification Prysm
   ("Texte: a -> b") et le commentaire libre qui les suit dans la meme
   entree ; signaler les entrees vides ; ajouter une entree de cloture
   quand la fiche est close sans que la chronologie le dise.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

# ---------------------------------------------------------------------------
# Parsing du commentaire SQL
# ---------------------------------------------------------------------------

_HEADER = r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}[ \t]*,"

# Une entree commence par un en-tete complet (date, heure, virgule) et court
# jusqu'a l'en-tete suivant. Prysm colle parfois l'en-tete au texte
# precedent ("...paddock04/04/2026 22:33:00 , ") : pas d'ancrage en debut
# de ligne. Deux defauts de l'ancienne regex corriges :
#  - l'en-tete final sans saut de ligne ("... , LEBERT Pierre" en fin de
#    champ) etait ignore : 2 887 fiches perdaient leur derniere entree ;
#  - une simple date citee dans le texte ("rdv le 12/10/2026") coupait
#    l'entree ; il faut desormais l'heure ET la virgule.
COMMENT_RE = re.compile(
    r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})[ \t]*,[ \t]*([^\n]*?)[ \t]*(?:\r?\n|\Z)"
    r"(.*?)(?=" + _HEADER + r"|\Z)",
    re.DOTALL,
)


def parse_comment(comment, origin="sql"):
    """Parse le champ comment brut en entrees {ts, operator, text, origin}."""
    if not comment:
        return []
    entries = []
    for m in COMMENT_RE.finditer(comment):
        ts_raw, operator, text = m.group(1), m.group(2).strip(), m.group(3).strip()
        try:
            dt = datetime.strptime(ts_raw, "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
            ts_iso = dt.isoformat()
        except ValueError:
            ts_iso = ts_raw
        entry = {"ts": ts_iso, "operator": operator, "text": text}
        if origin:
            entry["origin"] = origin
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Horodatages
# ---------------------------------------------------------------------------

def to_aware(val):
    """datetime | str ISO -> datetime aware, ou None.

    Les datetimes naifs viennent de pymongo (BSON = UTC). Une chaine ISO sans
    fuseau est, par convention Cockpit, en heure de Paris.
    """
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        s = val.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = datetime.strptime(s, "%d/%m/%Y %H:%M:%S")
            except ValueError:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=PARIS)
    return None


def iso_paris(val):
    """Horodatage normalise pour le JSON : ISO heure de Paris, a la seconde.

    Trois formats coexistaient dans comment_history (chaine Paris, chaine UTC,
    datetime BSON serialise par Flask en RFC 1123) : la chronologie etait
    triee et affichee de facon incoherente.
    """
    dt = to_aware(val)
    if dt is None:
        return val if isinstance(val, str) else None
    return dt.astimezone(PARIS).replace(microsecond=0).isoformat()


def now_paris():
    return datetime.now(PARIS)


def fmt_comment_ts(dt):
    return to_aware(dt).astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")


# ---------------------------------------------------------------------------
# Fusion SQL <-> Cockpit
# ---------------------------------------------------------------------------

def _norm_operator(op):
    op = re.sub(r"\s*\[.*?\]\s*$", "", str(op or ""))
    return re.sub(r"\s+", " ", op).strip().lower()


def entry_key(entry):
    """Cle d'identite d'une entree : (instant a la seconde, operateur)."""
    dt = to_aware(entry.get("ts"))
    ts = dt.astimezone(timezone.utc).replace(microsecond=0).isoformat() if dt else str(entry.get("ts"))
    return ts, _norm_operator(entry.get("operator"))


def is_local(entry):
    """Vrai si l'entree a ete ajoutee dans Cockpit ou par une tablette.

    Les entrees posterieures a ce module portent `origin`. Pour les plus
    anciennes, les ecritures Cockpit ont des microsecondes dans `ts`
    (datetime.now().isoformat()), les tablettes un datetime BSON ou un
    operateur "field:<nom>" ; le parseur SQL ne produit jamais ni l'un ni
    l'autre.
    """
    origin = entry.get("origin")
    if origin:
        return origin != "sql"
    ts = entry.get("ts")
    if isinstance(ts, datetime):
        return True
    if isinstance(ts, str) and "." in ts:
        return True
    if entry.get("system") or entry.get("photo") or entry.get("photos") or entry.get("codes"):
        return True
    return str(entry.get("operator") or "").startswith("field:")


def _sort_key(entry):
    dt = to_aware(entry.get("ts"))
    return (0, dt.timestamp()) if dt else (1, 0)


def merge_history(existing, sql_entries):
    """Chronologie fusionnee : entrees SQL fraiches + entrees locales conservees.

    Une entree SQL identique (meme seconde, meme operateur) a une entree
    locale est ecartee au profit de la locale, qui porte photos et codes :
    c'est le cas des anciennes fiches dont le champ `comment` contient deja
    les lignes ajoutees par Cockpit.
    """
    local = []
    for e in existing or []:
        if is_local(e):
            e = dict(e)
            if not e.get("origin"):
                e["origin"] = "field" if str(e.get("operator") or "").startswith("field:") else "cockpit"
            local.append(e)
    local_keys = {entry_key(e) for e in local}
    merged = [s for s in sql_entries if entry_key(s) not in local_keys] + local
    merged.sort(key=_sort_key)
    return merged


def render_comment(history):
    """Reconstruit le champ texte `comment` (format Prysm) depuis la chronologie."""
    out = []
    for e in history or []:
        dt = to_aware(e.get("ts"))
        head = dt.astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S") if dt else str(e.get("ts") or "")
        out.append(f"{head} , {e.get('operator') or ''}\n {e.get('text') or ''}\n")
    return "".join(out)


# Champs que Cockpit peut s'approprier. "status" est un groupe.
STATUS_FIELDS = ("status_code", "close_ts", "close_iso", "operator_close", "operator_id_close")


def merge_sync_doc(new_doc, existing):
    """Prepare le document SQL a ecrire en tenant compte des modifications Cockpit.

    `new_doc` : document produit par la synchro depuis la ligne SQL.
    `existing` : document Mongo actuel (ou None).
    Modifie et retourne `new_doc`.
    """
    new_doc["comment_sql"] = new_doc.get("comment")
    sql_entries = new_doc.get("comment_history") or []
    if not existing:
        return new_doc

    history = merge_history(existing.get("comment_history"), sql_entries)
    new_doc["comment_history"] = history
    if any(e.get("origin") != "sql" for e in history):
        new_doc["comment"] = render_comment(history)

    owned = set(existing.get("cockpit_owned") or [])

    if "status" in owned:
        # Cockpit l'emporte, sauf si SQL cloture APRES la derniere action
        # Cockpit sur le statut (fiche rouverte dans Cockpit puis close
        # dans Prysm) : la derniere decision gagne.
        sql_close = to_aware(new_doc.get("close_ts"))
        cockpit_at = to_aware(existing.get("cockpit_status_at"))
        sql_wins = (
            new_doc.get("status_code") == 10 and existing.get("status_code") != 10
            and sql_close is not None and cockpit_at is not None and sql_close > cockpit_at
        )
        if sql_wins:
            owned.discard("status")
        else:
            for f in STATUS_FIELDS:
                new_doc[f] = existing.get(f)

    cc = dict(new_doc.get("content_category") or {})
    ex_cc = existing.get("content_category") or {}
    cc_touched = False
    for f in owned:
        if f == "status":
            continue
        if f.startswith("content_category."):
            k = f.split(".", 1)[1]
            cc_touched = True
            if k in ex_cc:
                cc[k] = ex_cc[k]
            else:
                cc.pop(k, None)
        elif f == "area.desc":
            area = dict(new_doc.get("area") or {})
            area["desc"] = (existing.get("area") or {}).get("desc")
            new_doc["area"] = area
        else:
            new_doc[f] = existing.get(f)
    if cc_touched:
        new_doc["content_category"] = cc or None

    if owned:
        new_doc["cockpit_owned"] = sorted(owned)
    return new_doc


# ---------------------------------------------------------------------------
# Ecriture atomique d'une entree
# ---------------------------------------------------------------------------

def make_entry(operator, text, origin="cockpit", ts=None, **extra):
    entry = {
        "ts": iso_paris(ts or now_paris()),
        "operator": operator,
        "text": text or "",
        "origin": origin,
    }
    entry.update({k: v for k, v in extra.items() if v is not None})
    return entry


def append_entry(col, doc_id, entry, set_fields=None, owned=None,
                 inc_bounce=False, extra_filter=None):
    """Ajoute `entry` a la chronologie ET au champ texte `comment`, en une
    seule operation atomique (pipeline d'update, MongoDB >= 4.2).

    L'ancien code lisait `comment`, concatenait en Python puis reecrivait :
    deux commentaires simultanes en perdaient un. `owned` : champs que Cockpit
    s'approprie (respectes par la synchro SQL). Retourne le nombre de
    documents modifies (0 si introuvable ou filtre non satisfait).
    `entry` peut etre une liste d'entrees.
    """
    entries = entry if isinstance(entry, list) else [entry]
    lines = "".join(
        f"{fmt_comment_ts(e['ts'])} , {e.get('operator') or ''}\n {e.get('text') or ''}\n" for e in entries
    )
    stage = {
        "comment_history": {"$concatArrays": [
            {"$ifNull": ["$comment_history", []]}, [{"$literal": e} for e in entries]]},
        "comment": {"$concat": [{"$ifNull": ["$comment", ""]}, {"$literal": lines}]},
        "cockpit_rev": {"$add": [{"$ifNull": ["$cockpit_rev", 0]}, 1]},
    }
    if inc_bounce:
        stage["bounce_rev"] = {"$add": [{"$ifNull": ["$bounce_rev", 0]}, 1]}
    for k, v in (set_fields or {}).items():
        stage[k] = {"$literal": v}
    if owned:
        stage["cockpit_owned"] = {"$setUnion": [{"$ifNull": ["$cockpit_owned", []]}, list(owned)]}
    flt = {"_id": doc_id}
    flt.update(extra_filter or {})
    res = col.update_one(flt, [{"$set": stage}])
    return res.modified_count


def own_fields(col, doc_id, sets, owned, extra_filter=None):
    """$set simple + marquage des champs possedes par Cockpit (sans entree)."""
    stage = {k: {"$literal": v} for k, v in sets.items()}
    stage["cockpit_rev"] = {"$add": [{"$ifNull": ["$cockpit_rev", 0]}, 1]}
    if owned:
        stage["cockpit_owned"] = {"$setUnion": [{"$ifNull": ["$cockpit_owned", []]}, list(owned)]}
    flt = {"_id": doc_id}
    flt.update(extra_filter or {})
    return col.update_one(flt, [{"$set": stage}]).matched_count


# ---------------------------------------------------------------------------
# Decoration pour l'affichage
# ---------------------------------------------------------------------------

_STATUS_RE = re.compile(r"^Statut\s*:\s*(.*?)\s*->\s*(.*?)\s*$")
_STATUS_ONLY_RE = re.compile(r"^Statut\s*:\s*(.+?)\s*$")
# Ligne de modification Prysm : "Libelle: ancien -> nouveau", libelle en un
# seul mot (Texte, Appelant, Carroye, SousClassification...), ou la forme
# "Description  -> nouveau". Un libelle a espaces ("Patrouille 4: ...") est
# du texte libre.
_CHANGE_RE = re.compile(r"^([A-Za-zÀ-ÿ]{2,30})\s*:\s?(.*?)\s*->\s*(.*?)\s*$")
_DESC_CHANGE_RE = re.compile(r"^(Description)\s+->\s*(.*?)\s*$")

_STATUS_LABELS = {
    "Termine": "Terminé",
    "UserMessage.Status.0": "Nouvelle",
}


def _status_label(s):
    s = (s or "").strip()
    return _STATUS_LABELS.get(s, s)


def _is_closed_label(s):
    return (s or "").lower().startswith("termin")


def decorate_entry(entry):
    e = dict(entry)
    e["ts"] = iso_paris(e.get("ts"))
    raw = e.get("text") or e.get("comment") or ""
    lines = [ln.rstrip("\r") for ln in str(raw).replace("\r\n", "\n").split("\n")]
    status_from = status_to = None
    # Modifications structurees ecrites par Cockpit (route update)
    changes = [c for c in (entry.get("changes") or []) if isinstance(c, dict)]
    i = 0
    # Lignes systeme en tete d'entree (statut puis modifications)
    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        if status_to is None:
            m = _STATUS_RE.match(ln)
            if m:
                status_from, status_to = _status_label(m.group(1)), _status_label(m.group(2))
                i += 1
                continue
            m = _STATUS_ONLY_RE.match(ln)
            if m and "->" not in ln:
                status_from, status_to = None, _status_label(m.group(1))
                i += 1
                continue
        m = _DESC_CHANGE_RE.match(ln)
        if m:
            changes.append({"field": m.group(1), "old": "", "new": m.group(2)})
            i += 1
            continue
        m = _CHANGE_RE.match(ln)
        if m and m.group(1) != "Statut":
            changes.append({"field": m.group(1), "old": m.group(2), "new": m.group(3)})
            i += 1
            continue
        break
    rest = "\n".join(lines[i:]).strip()

    has_media = bool(e.get("photo") or e.get("photos") or e.get("codes"))
    if status_to is not None:
        kind = "status"
    elif e.get("system") or rest.startswith("Niveau d'urgence :") or rest.startswith("Heure d'intervention"):
        kind = "system"
    elif changes and not rest:
        kind = "change"
    elif not rest and not has_media:
        kind = "empty"
    else:
        kind = "comment"

    e["kind"] = kind
    e["status_from"] = status_from
    e["status_to"] = status_to
    e["changes"] = changes
    e["body"] = rest
    return e


def decorate_history(history, doc):
    """Chronologie prete a afficher, triee, avec une entree de cloture
    synthetique si la fiche est close sans que la chronologie le dise."""
    items = [decorate_entry(h) for h in (history or [])]
    if (doc or {}).get("status_code") == 10:
        closes = [h for h in items if h["kind"] == "status" and _is_closed_label(h["status_to"])]
        if not closes:
            items.append({
                "ts": iso_paris(doc.get("close_ts")),
                "operator": re.sub(r"\s*\[.*?\]\s*$", "", str(doc.get("operator_close") or "")).strip(),
                "text": "", "body": "", "kind": "status",
                "status_from": None, "status_to": "Terminé",
                "changes": [], "synthetic": True,
            })
        else:
            # Cloture sans operateur (anciennes ecritures) : completer
            last = closes[-1]
            if not last.get("operator") and doc.get("operator_close"):
                last["operator"] = str(doc.get("operator_close"))
    items.sort(key=_sort_key)
    return items
