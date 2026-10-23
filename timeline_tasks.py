"""timeline_tasks.py - Taches de preparation des vignettes de la timeline.

Source de verite : collection `timeline_tasks`, une tache par document.
La chaine markdown `vignette.todo` du document timetable n'est plus qu'une
VUE CALCULEE, reecrite a chaque changement (refresh_vignette_cache) : la
montre, Alfred, les rapports IA et les onglets ouverts avant la mise a jour
continuent de la lire sans rien savoir de la collection.

Document :

    {
      _id: uuid hex,
      event, year (str, comme timetable), vignette_id, date,
      activity, place, category, todos_type,
      match_key: "activite|lieu" normalises (historique entre editions),
      template_item_id: id de l'item du modele (collection todos) | None,
      text, text_overridden (texte modele modifie a la main),
      order: rang (modele 0..n, ajouts manuels >= 1000),
      status: todo | done | na (sans objet) | blocked,
      note, critical, due_offset_min, due_at ("YYYY-MM-DDTHH:MM", Paris),
      source: template | manual | ia_reco,
      reco_ref: {summary_id, bullet_index} (source ia_reco),
      done_at, done_by, done_by_name, status_at, status_by, status_by_name,
      history: [{ts, by, status, note?}] (30 dernieres),
      created_at, created_by, created_by_name, updated_at,
      deleted, deleted_at, deleted_by_name (suppression douce)
    }

Regles :
- Le merge (merge.py) ne touche QUE les taches issues du modele : une tache
  ajoutee a la main n'est jamais supprimee par une resynchronisation.
- Une tache du modele retiree du modele disparait si elle etait encore a
  faire ; deja traitee, elle reste (historique).
- Une tache du modele supprimee par un operateur est masquee (deleted) et
  n'est pas recreee au merge suivant.
- Migration paresseuse : la premiere lecture (ou le premier merge) d'un
  document timetable convertit ses chaines `todo` (drapeau tasks_migrated_at).
"""

import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ_PARIS = ZoneInfo("Europe/Paris")
COLLECTION = "timeline_tasks"
MIGRATION_FLAG = "tasks_migrated_at"

STATUSES = ("todo", "done", "na", "blocked")
RESOLVED = ("done", "na")
OPEN = ("todo", "blocked")
SOURCES = ("template", "manual", "ia_reco")

TEXT_MAX = 300
NOTE_MAX = 1000
HISTORY_MAX = 30
MANUAL_ORDER_BASE = 1000
DUE_OFFSET_LIMIT = 2880  # 48 h avant / apres le debut de la vignette

VALID_PHASES = ("open", "close", "both", "switch_control", "switch_free")
VALID_CONTROLS = ("controle", "libre", "both")
TEMPLATE_ORIGINS = ("", "retex", "ia", "historique")

# Champs de contexte recopies de la vignette sur chaque tache
CONTEXT_FIELDS = ("date", "activity", "place", "category", "todos_type", "match_key")


# ---------------------------------------------------------------------------
# Helpers purs
# ---------------------------------------------------------------------------

def norm_text(s):
    """Minuscules, sans accents, espaces reduits : cle de comparaison."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip().lower()


def match_key(activity, place):
    """Cle stable d'un meme creneau d'une edition a l'autre (et d'un evenement
    a l'autre : un parking garde ses problemes quel que soit l'evenement)."""
    return norm_text(activity) + "|" + norm_text(place)


def template_item_id(todos_type, text):
    """Id deterministe d'un item de modele qui n'en a pas encore : la meme
    chaine donne toujours le meme id, la reprise est donc idempotente."""
    seed = "todo|%s|%s" % (todos_type or "", norm_text(text))
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:16]


def clean_text(s, limit=TEXT_MAX):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


def _parse_offset(raw):
    if raw is None or raw == "":
        return None
    try:
        v = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return max(-DUE_OFFSET_LIMIT, min(DUE_OFFSET_LIMIT, v))


def normalize_template_items(raw_items, todos_type=""):
    """Normalise les items d'un modele en
    [{id, text, phase, control, critical, due_offset_min, origin}].

    Accepte l'ancien format [str] (phase 'open'). Un item sans id recoit l'id
    deterministe template_item_id(type, texte). Ids en double : le second est
    regenere (un id doit designer un seul item).
    """
    out, seen = [], set()
    for item in (raw_items or []):
        if isinstance(item, str):
            item = {"text": item, "phase": "open"}
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get("text"))
        if not text:
            continue
        phase = item.get("phase", "both")
        if phase not in VALID_PHASES:
            phase = "both"
        control = item.get("control", "both")
        if control not in VALID_CONTROLS:
            control = "both"
        iid = str(item.get("id") or "").strip()[:40] or template_item_id(todos_type, text)
        if iid in seen:
            iid = uuid.uuid4().hex[:16]
        seen.add(iid)
        origin = str(item.get("origin") or "")
        out.append({
            "id": iid,
            "text": text,
            "phase": phase,
            "control": control,
            "critical": bool(item.get("critical")),
            "due_offset_min": _parse_offset(item.get("due_offset_min")),
            "origin": origin if origin in TEMPLATE_ORIGINS else "",
        })
    return out


_TODO_LINE_RE = re.compile(r"^-?\s*\[(x|X|\s)?\]\s*(.*)$")


def parse_todo_str(raw):
    """Chaine markdown ('- [x] ...') ou liste (ancien format) -> [{text, done}].

    Meme tolerance que splitTodo cote navigateur : lignes sans case, coches
    en coche unicode, liste de chaines ou d'objets {text}.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        lines = []
        for v in raw:
            if isinstance(v, dict):
                v = v.get("text") or ""
            lines.append(str(v))
    else:
        lines = str(raw).split("\n")
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = _TODO_LINE_RE.match(line)
        if m:
            text = m.group(2).strip()
            done = bool(m.group(1) and m.group(1).lower() == "x")
        else:
            done = line[:1] in ("✓", "✔")
            text = line.lstrip("✓✔").strip()
        if text:
            out.append({"text": text, "done": done})
    return out


def sort_key(t):
    created = t.get("created_at")
    return (t.get("order") or 0, created.isoformat() if hasattr(created, "isoformat") else str(created or ""))


def active(tasks):
    return sorted((t for t in (tasks or []) if not t.get("deleted")), key=sort_key)


def render_todo_str(tasks):
    """Vue markdown des taches actives (cache vignette.todo).
    'Sans objet' compte comme traitee : [x] pour les lecteurs historiques."""
    return "\n".join(
        "- [%s] %s" % ("x" if t.get("status") in RESOLVED else " ", t.get("text") or "")
        for t in active(tasks))


def prep_from_tasks(tasks):
    """Statut de preparation deduit des taches, None s'il n'y en a aucune.
    Valeurs du champ timetable : 'true' (prete), 'progress', 'non'."""
    act = active(tasks)
    if not act:
        return None
    resolved = sum(1 for t in act if t.get("status") in RESOLVED)
    if resolved == len(act):
        return "true"
    if resolved or any(t.get("status") == "blocked" for t in act):
        return "progress"
    return "non"


def compute_due_at(date_str, start, offset_min=None):
    """Echeance 'YYYY-MM-DDTHH:MM' (heure de Paris) = debut de la vignette
    moins offset_min minutes. None si l'heure n'est pas exploitable (TBC)."""
    try:
        dt = datetime.strptime("%s %s" % (str(date_str)[:10], str(start or "").strip()), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    dt -= timedelta(minutes=int(offset_min or 0))
    return dt.strftime("%Y-%m-%dT%H:%M")


def due_base(v):
    """Heure de reference des echeances : debut de la vignette, sinon sa fin
    (vignettes de fermeture, qui ne portent qu'une heure de fin)."""
    for k in ("start", "end"):
        h = str((v or {}).get(k) or "").strip()
        if re.match(r"^\d{1,2}:\d{2}$", h):
            return h
    return None


def now_local_str(now_utc=None):
    now = now_utc or datetime.now(timezone.utc)
    return now.astimezone(TZ_PARIS).strftime("%Y-%m-%dT%H:%M")


def is_late(task, now_str):
    due = task.get("due_at")
    return bool(due and task.get("status") in OPEN and due < now_str)


def vignette_context(v, todos_type=None):
    """Champs de contexte d'une tache depuis sa vignette."""
    ctx = {
        "date": str(v.get("date") or "")[:10],
        "activity": (v.get("activity") or "").strip(),
        "place": (v.get("place") or "").strip(),
        "category": (v.get("category") or "").strip(),
        "todos_type": todos_type if todos_type is not None else (v.get("todos_type") or None),
    }
    ctx["match_key"] = match_key(ctx["activity"], ctx["place"])
    return ctx


def _user_fields(user):
    user = user or {}
    return (str(user.get("email") or "")[:200], str(user.get("name") or user.get("email") or "")[:200])


def _status_set(status, user, now):
    email, name = _user_fields(user)
    s = {"status": status, "status_at": now, "status_by": email,
         "status_by_name": name, "updated_at": now}
    if status == "done":
        s.update(done_at=now, done_by=email, done_by_name=name)
    else:
        s.update(done_at=None, done_by=None, done_by_name=None)
    return s


def new_task_doc(event, year, vignette_id, ctx, text, *, start=None, source="manual",
                 status="todo", order=MANUAL_ORDER_BASE, template_item=None,
                 user=None, now=None, reco_ref=None, note=None, critical=False):
    now = now or datetime.now(timezone.utc)
    email, name = _user_fields(user)
    offset = (template_item or {}).get("due_offset_min")
    doc = {
        "_id": uuid.uuid4().hex,
        "event": event, "year": str(year), "vignette_id": str(vignette_id),
        "template_item_id": (template_item or {}).get("id"),
        "text": clean_text(text), "text_overridden": False,
        "order": order, "status": "todo", "note": clean_text(note, NOTE_MAX) if note else "",
        "critical": bool((template_item or {}).get("critical") or critical),
        "due_offset_min": offset,
        "due_at": compute_due_at(ctx.get("date"), start, offset),
        "source": source if source in SOURCES else "manual",
        "reco_ref": reco_ref,
        "created_at": now, "created_by": email, "created_by_name": name, "updated_at": now,
        "history": [],
        "deleted": False,
    }
    doc.update({k: ctx.get(k) for k in CONTEXT_FIELDS})
    doc.update(_status_set("todo", user, now))
    if status != "todo":
        doc.update(_status_set(status, user, now))
        doc["history"] = [{"ts": now, "by": name or email, "status": status}]
    return doc


# ---------------------------------------------------------------------------
# Plans de synchronisation (purs : testables sans base)
# ---------------------------------------------------------------------------

def plan_template_sync(existing, tpl_items, ctx, start, event, year, vignette_id,
                       now=None):
    """Aligne les taches d'une vignette sur les items du modele.

    existing : taches de la vignette (y compris supprimees).
    tpl_items : items du modele retenus pour cette vignette (phase, mode).
    Retourne {inserts: [doc], updates: [(id, set)], deletes: [id]}.
    Les taches manuelles ne sont jamais supprimees ; une tache manuelle au
    meme texte qu'un item du modele y est rattachee (pas de doublon).
    """
    now = now or datetime.now(timezone.utc)
    inserts, updates, deletes = [], [], []
    by_tpl = {t.get("template_item_id"): t for t in existing if t.get("template_item_id")}
    claimed = set()
    tpl_ids = set()
    for i, it in enumerate(tpl_items):
        tpl_ids.add(it["id"])
        cur = by_tpl.get(it["id"])
        s = {}
        if cur is None:
            key = norm_text(it["text"])
            cur = next((t for t in existing
                        if not t.get("template_item_id") and not t.get("deleted")
                        and t["_id"] not in claimed and norm_text(t.get("text")) == key), None)
            if cur is not None:
                s.update(template_item_id=it["id"], source="template")
        if cur is None:
            inserts.append(new_task_doc(event, year, vignette_id, ctx, it["text"],
                                        start=start, source="template", order=i,
                                        template_item=it, now=now,
                                        user={"name": "Modele"}))
            continue
        claimed.add(cur["_id"])
        if not cur.get("text_overridden") and cur.get("text") != it["text"] and not s.get("source"):
            s["text"] = it["text"]
        if cur.get("order") != i:
            s["order"] = i
        if bool(cur.get("critical")) != bool(it.get("critical")):
            s["critical"] = bool(it.get("critical"))
        if cur.get("due_offset_min") != it.get("due_offset_min"):
            s["due_offset_min"] = it.get("due_offset_min")
        due = compute_due_at(ctx.get("date"), start, it.get("due_offset_min"))
        if cur.get("due_at") != due:
            s["due_at"] = due
        s.update({k: ctx.get(k) for k in CONTEXT_FIELDS if cur.get(k) != ctx.get(k)})
        if s:
            s["updated_at"] = now
            updates.append((cur["_id"], s))
    for t in existing:
        if t["_id"] in claimed:
            continue
        if (t.get("source") == "template" and t.get("template_item_id")
                and t["template_item_id"] not in tpl_ids and not t.get("deleted")):
            if t.get("status") == "todo":
                deletes.append(t["_id"])
                continue
            # Deja traitee : on la garde (historique), detachee du modele
            updates.append((t["_id"], {"template_item_id": None, "source": "manual",
                                       "template_removed": True, "updated_at": now}))
            continue
        s = {k: ctx.get(k) for k in CONTEXT_FIELDS if t.get(k) != ctx.get(k)}
        due = compute_due_at(ctx.get("date"), start, t.get("due_offset_min"))
        if t.get("due_at") != due:
            s["due_at"] = due
        if s:
            s["updated_at"] = now
            updates.append((t["_id"], s))
    return {"inserts": inserts, "updates": updates, "deletes": deletes}


def plan_editor_sync(existing, items, ctx, start, event, year, vignette_id,
                     user=None, now=None, known=None):
    """Applique la liste saisie dans l'editeur (modale) ou une ancienne
    chaine `todo` : items = [{id?, text, done?}].

    Avec id : la tache est mise a jour. Sans id (chaine historique) : appariement
    par texte. Nouvelles lignes : taches manuelles.
    Suppression douce des taches absentes de la liste, MAIS seulement parmi
    `known` (ids affiches par l'editeur) : une tache creee entre-temps (merge,
    autre poste) ne disparait pas parce qu'un onglet ne la connaissait pas.
    known=None : ancien client sans ids, aucune suppression.
    Retourne {inserts, updates, deletes, changed}.
    """
    now = now or datetime.now(timezone.utc)
    act = {t["_id"]: t for t in existing if not t.get("deleted")}
    seen = set()
    inserts, updates = [], []
    next_order = max([MANUAL_ORDER_BASE - 1] + [t.get("order") or 0 for t in act.values()]) + 1
    email, name = _user_fields(user)
    for it in items or []:
        text = clean_text(it.get("text"))
        if not text:
            continue
        tid = str(it.get("id") or "")
        cur = act.get(tid) if tid else None
        if cur is None and not tid:
            key = norm_text(text)
            cur = next((t for t in act.values()
                        if t["_id"] not in seen and norm_text(t.get("text")) == key), None)
        if cur is None:
            status = "done" if it.get("done") else "todo"
            inserts.append(new_task_doc(event, year, vignette_id, ctx, text, start=start,
                                        status=status, order=next_order, user=user, now=now))
            next_order += 1
            continue
        seen.add(cur["_id"])
        s = {}
        if text != cur.get("text"):
            s["text"] = text
            if cur.get("template_item_id"):
                s["text_overridden"] = True
        if "done" in it:
            want = cur.get("status")
            if it.get("done") and want not in RESOLVED:
                want = "done"
            elif not it.get("done") and want in RESOLVED:
                want = "todo"
            if want != cur.get("status"):
                s.update(_status_set(want, user, now))
                s["_history"] = {"ts": now, "by": name or email, "status": want}
        if s:
            s["updated_at"] = now
            updates.append((cur["_id"], s))
    known = set(known or ())
    deletes = [tid for tid in act if tid not in seen and tid in known]
    return {"inserts": inserts, "updates": updates, "deletes": deletes,
            "changed": bool(inserts or updates or deletes)}


# ---------------------------------------------------------------------------
# Acces base
# ---------------------------------------------------------------------------

_indexes_ok = False


def _col(db):
    return db[COLLECTION]


def ensure_indexes(db):
    global _indexes_ok
    if _indexes_ok:
        return
    try:
        col = _col(db)
        col.create_index([("event", 1), ("year", 1), ("vignette_id", 1)], name="ev_year_vignette")
        col.create_index([("match_key", 1)], name="match_key")
        col.create_index([("todos_type", 1)], name="todos_type")
        col.create_index([("reco_ref.summary_id", 1)], name="reco_summary", sparse=True)
        _indexes_ok = True
    except Exception as e:
        logger.warning("timeline_tasks : index non crees (%s)", e)


def _apply_plan(db, plan, user=None, deleted_hard=True):
    col = _col(db)
    if plan.get("inserts"):
        col.insert_many(plan["inserts"])
    for tid, s in plan.get("updates") or []:
        s = dict(s)
        hist = s.pop("_history", None)
        op = {"$set": s}
        if hist:
            op["$push"] = {"history": {"$each": [hist], "$slice": -HISTORY_MAX}}
        col.update_one({"_id": tid}, op)
    if plan.get("deletes"):
        if deleted_hard:
            col.delete_many({"_id": {"$in": list(plan["deletes"])}})
        else:
            now = datetime.now(timezone.utc)
            _email, name = _user_fields(user)
            col.update_many({"_id": {"$in": list(plan["deletes"])}},
                            {"$set": {"deleted": True, "deleted_at": now,
                                      "deleted_by_name": name, "updated_at": now}})


def load_templates(db):
    """{type: [items normalises]} depuis la collection todos."""
    out = {}
    for doc in db["todos"].find({}, {"type": 1, "todos": 1}):
        t = doc.get("type")
        if t:
            out[t] = normalize_template_items(doc.get("todos"), t)
    return out


def ensure_template_ids(db):
    """Ecrit les ids manquants des items de modele (ids deterministes, donc
    identiques a ceux deja utilises en memoire). Idempotent."""
    try:
        for doc in db["todos"].find({}, {"type": 1, "todos": 1}):
            raw = doc.get("todos") or []
            if raw and all(isinstance(x, dict) and x.get("id") for x in raw):
                continue
            items = normalize_template_items(raw, doc.get("type"))
            db["todos"].update_one({"_id": doc["_id"]}, {"$set": {"todos": items}})
    except Exception as e:
        logger.warning("ensure_template_ids : %s", e)


def tasks_by_vignette(db, event, year, vignette_ids=None, include_deleted=False,
                      projection=None):
    q = {"event": event, "year": str(year)}
    if vignette_ids is not None:
        q["vignette_id"] = {"$in": [str(v) for v in vignette_ids]}
    if not include_deleted:
        q["deleted"] = {"$ne": True}
    out = {}
    for t in _col(db).find(q, projection):
        out.setdefault(t["vignette_id"], []).append(t)
    for v in out.values():
        v.sort(key=sort_key)
    return out


def _timetable_doc(db, event, year, projection=None):
    return db["timetable"].find_one({"event": event, "year": str(year)}, projection)


def find_vignette(doc, vignette_id):
    """(date, index, vignette) dans un document timetable, ou (None, None, None)."""
    vid = str(vignette_id)
    for date, items in ((doc or {}).get("data") or {}).items():
        if not isinstance(items, list):
            continue
        for idx, v in enumerate(items):
            if isinstance(v, dict) and str(v.get("_id")) == vid:
                return date, idx, v
    return None, None, None


def migrate_doc_tasks(doc, templates, existing_vids, event, year, now=None):
    """Taches a creer depuis les chaines `todo` d'un document timetable (pur).

    Un texte identique a un item du modele de la vignette y est rattache
    (source template) ; le reste devient manuel. L'etat coche est conserve.
    """
    now = now or datetime.now(timezone.utc)
    docs = []
    for date, items in ((doc or {}).get("data") or {}).items():
        if not isinstance(items, list):
            continue
        for v in items:
            if not isinstance(v, dict) or not v.get("_id"):
                continue
            vid = str(v["_id"])
            if vid in existing_vids:
                continue
            parsed = parse_todo_str(v.get("todo"))
            if not parsed:
                continue
            ctx = vignette_context(dict(v, date=v.get("date") or date))
            by_text = {norm_text(it["text"]): it for it in templates.get(v.get("todos_type")) or []}
            used = set()
            for i, p in enumerate(parsed):
                it = by_text.get(norm_text(p["text"]))
                if it is not None and it["id"] in used:
                    it = None
                if it is not None:
                    used.add(it["id"])
                d = new_task_doc(event, year, vid, ctx, p["text"], start=due_base(v),
                                 source="template" if it else "manual",
                                 status="done" if p["done"] else "todo",
                                 order=i if it else MANUAL_ORDER_BASE + i,
                                 template_item=it, now=now,
                                 user={"name": "Reprise"})
                docs.append(d)
    return docs


def ensure_migrated(db, event, year, doc=None):
    """Convertit une fois les chaines `todo` d'un document timetable en
    taches. Le drapeau est pose AVANT la conversion (update conditionnel) :
    deux requetes simultanees ne migrent pas deux fois. Rend True si migre."""
    col = db["timetable"]
    if doc is None:
        doc = _timetable_doc(db, event, year, {"data": 1, MIGRATION_FLAG: 1})
    if not doc or doc.get(MIGRATION_FLAG):
        return False
    now = datetime.now(timezone.utc)
    res = col.update_one({"_id": doc["_id"], MIGRATION_FLAG: {"$exists": False}},
                         {"$set": {MIGRATION_FLAG: now}})
    if getattr(res, "modified_count", 0) != 1:
        return False
    ensure_indexes(db)
    try:
        if "data" not in doc:
            doc = col.find_one({"_id": doc["_id"]}, {"data": 1})
        existing = set(_col(db).distinct("vignette_id", {"event": event, "year": str(year)}))
        docs = migrate_doc_tasks(doc, load_templates(db), existing, event, str(year), now)
        if docs:
            _col(db).insert_many(docs)
        logger.info("timeline_tasks : reprise %s %s, %d tache(s)", event, year, len(docs))
    except Exception:
        # Reprise ratee : on retire le drapeau pour retenter a la prochaine lecture
        col.update_one({"_id": doc["_id"]}, {"$unset": {MIGRATION_FLAG: ""}})
        raise
    return True


def refresh_vignette_cache(db, event, year, vignette_ids, update_prep=True, doc=None):
    """Reecrit vignette.todo (et preparation_checked) depuis la collection.
    Rend {vignette_id: {"todo", "preparation_checked"}}."""
    vids = {str(v) for v in vignette_ids}
    if doc is None:
        doc = _timetable_doc(db, event, year, {"data": 1})
    if not doc or not vids:
        return {}
    tasks = tasks_by_vignette(db, event, year, vids)
    sets, out = {}, {}
    for date, items in (doc.get("data") or {}).items():
        if not isinstance(items, list):
            continue
        for idx, v in enumerate(items):
            vid = str((v or {}).get("_id"))
            if vid not in vids:
                continue
            ts = tasks.get(vid, [])
            todo = render_todo_str(ts)
            sets["data.%s.%d.todo" % (date, idx)] = todo
            prep = v.get("preparation_checked")
            if update_prep:
                p = prep_from_tasks(ts)
                if p is not None:
                    prep = p
                    sets["data.%s.%d.preparation_checked" % (date, idx)] = p
            out[vid] = {"todo": todo, "preparation_checked": prep}
    if sets:
        db["timetable"].update_one({"_id": doc["_id"]}, {"$set": sets, "$inc": {"version": 1}})
    return out


def public_task(t):
    """Forme envoyee au navigateur."""
    def iso(dt):
        if not dt:
            return None
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return {
        "id": t["_id"],
        "text": t.get("text") or "",
        "status": t.get("status") or "todo",
        "source": t.get("source") or "manual",
        "template": bool(t.get("template_item_id")),
        "critical": bool(t.get("critical")),
        "due_at": t.get("due_at"),
        "note": t.get("note") or "",
        "done_by_name": t.get("done_by_name") or "",
        "done_at": iso(t.get("done_at")),
        "status_by_name": t.get("status_by_name") or "",
        "status_at": iso(t.get("status_at")),
        "reco": bool(t.get("reco_ref")),
    }


_LIGHT_PROJECTION = {"history": 0}


def attach_tasks(db, event, year, timetable_doc):
    """Ajoute `tasks` (forme publique) a chaque vignette qui en a. Migre le
    document au passage si besoin. Ne leve jamais : sans taches, la timeline
    retombe sur la chaine `todo`."""
    try:
        if not timetable_doc.get(MIGRATION_FLAG):
            ensure_migrated(db, event, year)
        tasks = tasks_by_vignette(db, event, year, projection=_LIGHT_PROJECTION)
    except Exception:
        logger.exception("timeline_tasks : taches indisponibles pour %s %s", event, year)
        return timetable_doc
    for items in (timetable_doc.get("data") or {}).values():
        if not isinstance(items, list):
            continue
        for v in items:
            ts = tasks.get(str((v or {}).get("_id")))
            if ts:
                v["tasks"] = [public_task(t) for t in ts]
    timetable_doc.pop(MIGRATION_FLAG, None)
    return timetable_doc


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

class TaskError(Exception):
    """Erreur metier : message affichable, code HTTP."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _load_task(db, task_id):
    t = _col(db).find_one({"_id": str(task_id)})
    if not t or t.get("deleted"):
        raise TaskError("Tâche introuvable.", 404)
    return t


def _result(db, t, cache):
    tasks = tasks_by_vignette(db, t["event"], t["year"], [t["vignette_id"]],
                              projection=_LIGHT_PROJECTION).get(t["vignette_id"], [])
    c = cache.get(t["vignette_id"], {})
    return {"task_id": t["_id"], "vignette_id": t["vignette_id"],
            "tasks": [public_task(x) for x in tasks],
            "todo": c.get("todo", render_todo_str(tasks)),
            "preparation_checked": c.get("preparation_checked")}


def set_status(db, task_id, status, user, note=None):
    if status not in STATUSES:
        raise TaskError("Statut invalide.")
    t = _load_task(db, task_id)
    now = datetime.now(timezone.utc)
    s = _status_set(status, user, now)
    if note is not None:
        s["note"] = clean_text(note, NOTE_MAX)
    if status == "blocked" and not (s.get("note") or t.get("note")):
        raise TaskError("Indiquer le motif du blocage.")
    email, name = _user_fields(user)
    entry = {"ts": now, "by": name or email, "status": status}
    if note:
        entry["note"] = clean_text(note, NOTE_MAX)
    _col(db).update_one({"_id": t["_id"]}, {
        "$set": s, "$push": {"history": {"$each": [entry], "$slice": -HISTORY_MAX}}})
    t.update(s)
    cache = refresh_vignette_cache(db, t["event"], t["year"], [t["vignette_id"]])
    if status == "done" and t.get("reco_ref"):
        _mark_reco_applied(db, t["reco_ref"], email)
    return _result(db, t, cache)


def _mark_reco_applied(db, reco_ref, by_email):
    """Tache issue d'une recommandation IA faite -> recommandation appliquee."""
    try:
        import pcorg_summary
        pcorg_summary.set_recommendation_status(
            db, reco_ref.get("summary_id"), reco_ref.get("bullet_index"), "applied",
            by_email=by_email)
    except Exception as e:
        logger.warning("timeline_tasks : statut de recommandation non mis a jour (%s)", e)


def add_task(db, event, year, vignette_id, text, user, source="manual",
             reco_ref=None, note=None, critical=False):
    text = clean_text(text)
    if not text:
        raise TaskError("Texte de la tâche vide.")
    doc = _timetable_doc(db, event, year, {"data": 1})
    date, _idx, v = find_vignette(doc, vignette_id)
    if v is None:
        raise TaskError("Vignette introuvable.", 404)
    ensure_migrated(db, event, year)
    ensure_indexes(db)
    existing = tasks_by_vignette(db, event, year, [vignette_id], include_deleted=True).get(str(vignette_id), [])
    order = max([MANUAL_ORDER_BASE - 1] + [t.get("order") or 0 for t in existing]) + 1
    ctx = vignette_context(dict(v, date=v.get("date") or date))
    t = new_task_doc(event, year, vignette_id, ctx, text, start=due_base(v),
                     source=source, order=order, user=user, reco_ref=reco_ref,
                     note=note, critical=critical)
    _col(db).insert_one(t)
    cache = refresh_vignette_cache(db, event, year, [vignette_id], doc=doc)
    return _result(db, t, cache)


def edit_task(db, task_id, user, text=None, note=None, critical=None):
    t = _load_task(db, task_id)
    s = {"updated_at": datetime.now(timezone.utc)}
    if text is not None:
        text = clean_text(text)
        if not text:
            raise TaskError("Texte de la tâche vide.")
        if text != t.get("text"):
            s["text"] = text
            if t.get("template_item_id"):
                s["text_overridden"] = True
    if note is not None:
        s["note"] = clean_text(note, NOTE_MAX)
    if critical is not None:
        s["critical"] = bool(critical)
    _col(db).update_one({"_id": t["_id"]}, {"$set": s})
    t.update(s)
    cache = refresh_vignette_cache(db, t["event"], t["year"], [t["vignette_id"]], update_prep=False)
    return _result(db, t, cache)


def delete_task(db, task_id, user):
    t = _load_task(db, task_id)
    _apply_plan(db, {"deletes": [t["_id"]]}, user=user, deleted_hard=False)
    cache = refresh_vignette_cache(db, t["event"], t["year"], [t["vignette_id"]])
    return _result(db, t, cache)


def sync_editor(db, event, year, vignette_id, payload, user, refresh=True):
    """Liste de la modale (ou chaine `todo` d'un ancien client) -> collection.
    payload : rendu par editor_payload. Rend le cache reecrit si quelque
    chose a change, sinon None."""
    doc = _timetable_doc(db, event, year, {"data": 1, MIGRATION_FLAG: 1})
    date, _idx, v = find_vignette(doc, vignette_id)
    if v is None:
        raise TaskError("Vignette introuvable.", 404)
    if not doc.get(MIGRATION_FLAG):
        ensure_migrated(db, event, year, doc)
    ensure_indexes(db)
    existing = tasks_by_vignette(db, event, year, [vignette_id], include_deleted=True).get(str(vignette_id), [])
    ctx = vignette_context(dict(v, date=v.get("date") or date))
    plan = plan_editor_sync(existing, payload["items"], ctx, due_base(v), event, year,
                            vignette_id, user=user, known=payload.get("known"))
    _apply_plan(db, plan, user=user, deleted_hard=False)
    if refresh and plan["changed"]:
        return refresh_vignette_cache(db, event, year, [vignette_id], doc=doc).get(str(vignette_id))
    return None


def editor_payload(data):
    """Liste de l'editeur depuis la requete : `tasks` structure (+ `tasks_known`,
    ids affiches a l'ouverture), sinon la chaine `todo` des anciens clients.
    None si aucun des deux n'est fourni.
    Rend {"items": [{id, text, done}], "known": set | None}."""
    raw = data.get("tasks")
    if isinstance(raw, list):
        items = [{"id": str(x.get("id") or ""), "text": x.get("text"), "done": bool(x.get("done"))}
                 for x in raw if isinstance(x, dict)]
        known = data.get("tasks_known")
        known = {str(k) for k in known} if isinstance(known, list) else {i["id"] for i in items if i["id"]}
        return {"items": items, "known": known}
    if "todo" in data and data.get("todo") is not None:
        return {"items": parse_todo_str(data.get("todo")), "known": None}
    return None


def update_context(db, event, year, vignette_id):
    """Vignette deplacee ou modifiee (date, horaires, libelle) : recopie le
    contexte et recalcule les echeances de ses taches."""
    doc = _timetable_doc(db, event, year, {"data": 1})
    date, _idx, v = find_vignette(doc, vignette_id)
    if v is None:
        return
    ctx = vignette_context(dict(v, date=v.get("date") or date))
    for t in tasks_by_vignette(db, event, year, [vignette_id], include_deleted=True).get(str(vignette_id), []):
        s = {k: ctx[k] for k in CONTEXT_FIELDS if k != "todos_type" and t.get(k) != ctx[k]}
        due = compute_due_at(ctx["date"], due_base(v), t.get("due_offset_min"))
        if t.get("due_at") != due:
            s["due_at"] = due
        if s:
            _col(db).update_one({"_id": t["_id"]}, {"$set": s})


def delete_for_vignette(db, event, year, vignette_id):
    _col(db).delete_many({"event": event, "year": str(year), "vignette_id": str(vignette_id)})


def copy_tasks(db, event, year, src_vignette_id, new_vignette_id, user):
    """Duplication de vignette : copie des taches, toutes a faire, manuelles."""
    doc = _timetable_doc(db, event, year, {"data": 1})
    date, _idx, v = find_vignette(doc, new_vignette_id)
    if v is None:
        return
    ctx = vignette_context(dict(v, date=v.get("date") or date))
    src = tasks_by_vignette(db, event, year, [src_vignette_id]).get(str(src_vignette_id), [])
    docs = [new_task_doc(event, year, new_vignette_id, ctx, t["text"], start=due_base(v),
                         order=MANUAL_ORDER_BASE + i, user=user, critical=t.get("critical"))
            for i, t in enumerate(src)]
    if docs:
        _col(db).insert_many(docs)
        refresh_vignette_cache(db, event, year, [new_vignette_id], doc=doc)


def apply_merge(db, event, year, timetable_doc, entries, removed_ids):
    """Appele par merge.update_timetable_document AVANT l'ecriture du document.

    entries : {vignette_id: {"vignette": dict, "tpl": [items]}} pour chaque
    vignette du parametrage dont le type de taches est connu.
    removed_ids : vignettes supprimees par le nettoyage des orphelines.
    Met a jour la collection puis reecrit EN MEMOIRE `todo` (et le statut de
    preparation des vignettes dont les taches ont change) dans
    timetable_doc["data"] : l'appelant ecrit le document ensuite.
    """
    ensure_indexes(db)
    if timetable_doc.get("_id") is not None and not timetable_doc.get(MIGRATION_FLAG):
        ensure_migrated(db, event, year, {"_id": timetable_doc["_id"],
                                          "data": timetable_doc.get("data") or {}})
        timetable_doc[MIGRATION_FLAG] = datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    vids = list(entries)
    existing = tasks_by_vignette(db, event, year, vids, include_deleted=True) if vids else {}
    changed = set()
    for vid, e in entries.items():
        v = e["vignette"]
        ctx = vignette_context(v, v.get("todos_type"))
        plan = plan_template_sync(existing.get(vid, []), e["tpl"], ctx, due_base(v),
                                  event, year, vid, now=now)
        if plan["inserts"] or plan["deletes"]:
            changed.add(vid)
        _apply_plan(db, plan)
    if removed_ids:
        _col(db).delete_many({"event": event, "year": str(year),
                              "vignette_id": {"$in": [str(x) for x in removed_ids]}})
    tasks = tasks_by_vignette(db, event, year)
    for items in (timetable_doc.get("data") or {}).values():
        if not isinstance(items, list):
            continue
        for v in items:
            vid = str((v or {}).get("_id"))
            ts = tasks.get(vid)
            if ts:
                v["todo"] = render_todo_str(ts)
                if vid in changed:
                    p = prep_from_tasks(ts)
                    if p is not None:
                        v["preparation_checked"] = p
            elif vid in entries:
                v["todo"] = ""
    return changed


# ---------------------------------------------------------------------------
# Lectures pour l'IA
# ---------------------------------------------------------------------------

def open_and_late(db, event, year, now_utc=None, lookback_hours=12, ahead_hours=6, limit=25):
    """Taches en retard (echeance passee, non traitees) et bloquees, sur
    [maintenant - lookback, maintenant + ahead]. Pour le briefing."""
    now_str = now_local_str(now_utc)
    now = (now_utc or datetime.now(timezone.utc)).astimezone(TZ_PARIS)
    lo = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M")
    hi = (now + timedelta(hours=ahead_hours)).strftime("%Y-%m-%dT%H:%M")
    q = {"event": event, "year": str(year), "deleted": {"$ne": True},
         "status": {"$in": list(OPEN)}, "due_at": {"$gte": lo, "$lte": hi}}
    late, blocked, upcoming_critical = [], [], []
    for t in _col(db).find(q, {"history": 0}):
        row = {"echeance": (t.get("due_at") or "").replace("T", " "),
               "tache": t.get("text"), "vignette": t.get("activity"),
               "lieu": t.get("place") or None, "critique": bool(t.get("critical"))}
        if t.get("status") == "blocked":
            row["motif"] = t.get("note") or None
            blocked.append(row)
        elif is_late(t, now_str):
            late.append(row)
        elif t.get("critical"):
            upcoming_critical.append(row)
    key = lambda r: (not r["critique"], r["echeance"])
    late.sort(key=key)
    blocked.sort(key=key)
    upcoming_critical.sort(key=lambda r: r["echeance"])
    return {"en_retard": late[:limit], "bloquees": blocked[:limit],
            "critiques_a_venir": upcoming_critical[:limit],
            "total_en_retard": len(late), "total_bloquees": len(blocked)}


def summarize_for_prompt(item):
    """Resume compact des taches d'une vignette depuis sa chaine `todo`
    (cache a jour) : {faites, total, a_faire[<=4]} ou None."""
    parsed = parse_todo_str(item.get("todo"))
    if not parsed:
        return None
    todo = [p["text"][:80] for p in parsed if not p["done"]]
    return {"faites": len(parsed) - len(todo), "total": len(parsed), "a_faire": todo[:4]}


# ---------------------------------------------------------------------------
# Historique entre editions
# ---------------------------------------------------------------------------

def _edition_stats(tasks, now_str):
    st = {"total": 0, "faites": 0, "sans_objet": 0, "bloquees": 0, "non_faites": 0,
          "faites_en_retard": 0, "ajouts_manuels": [], "notes": []}
    manual_seen = set()
    for t in tasks:
        st["total"] += 1
        s = t.get("status")
        if s == "done":
            st["faites"] += 1
            due, done_at = t.get("due_at"), t.get("done_at")
            if due and done_at:
                if getattr(done_at, "tzinfo", None) is None:
                    done_at = done_at.replace(tzinfo=timezone.utc)
                if done_at.astimezone(TZ_PARIS).strftime("%Y-%m-%dT%H:%M") > due:
                    st["faites_en_retard"] += 1
        elif s == "na":
            st["sans_objet"] += 1
        elif s == "blocked":
            st["bloquees"] += 1
        elif t.get("due_at") and t["due_at"] < now_str:
            st["non_faites"] += 1
        if t.get("source") in ("manual", "ia_reco") and not t.get("template_removed"):
            k = norm_text(t.get("text"))
            if k not in manual_seen:
                manual_seen.add(k)
                st["ajouts_manuels"].append(t.get("text"))
        if t.get("note"):
            st["notes"].append({"tache": t.get("text"), "note": t["note"],
                                "statut": s, "par": t.get("status_by_name") or ""})
    st["ajouts_manuels"] = st["ajouts_manuels"][:10]
    st["notes"] = st["notes"][:8]
    return st


def vignette_history(db, event, year, vignette_id, max_editions=6):
    """Ce qui s'est passe sur le meme creneau (meme activite, meme lieu) lors
    des autres editions, tous evenements confondus, et les autres jours de
    l'edition courante."""
    doc = _timetable_doc(db, event, year, {"data": 1})
    date, _idx, v = find_vignette(doc, vignette_id)
    if v is None:
        raise TaskError("Vignette introuvable.", 404)
    mk = match_key(v.get("activity"), v.get("place"))
    groups = {}
    for t in _col(db).find({"match_key": mk, "deleted": {"$ne": True}}, {"history": 0}):
        if t.get("vignette_id") == str(vignette_id):
            continue
        groups.setdefault((t.get("event"), t.get("year")), []).append(t)
    now_str = now_local_str()
    out = []
    for (ev, yr), ts in groups.items():
        st = _edition_stats(ts, now_str)
        st.update(event=ev, year=yr, courante=(ev == event and str(yr) == str(year)),
                  jours=sorted({t.get("date") for t in ts if t.get("date")}))
        out.append(st)
    out.sort(key=lambda s: (not s["courante"], -int(s["year"]) if str(s["year"]).isdigit() else 0, s["event"] or ""))
    return {"activity": v.get("activity"), "place": v.get("place"), "editions": out[:max_editions]}


def template_insights(db, todos_type, min_editions=2, min_vignettes=3):
    """Usage reel d'un modele de taches sur toutes les editions.

    - par item du modele : traitees, sans objet, non faites, en retard ;
    - ajouts manuels recurrents (candidats a l'ajout au modele) ;
    - items presque jamais faits (candidats au retrait) ou souvent en retard
      (echeance a avancer) ;
    - taux de realisation par edition.
    """
    now_str = now_local_str()
    items, manual, editions = {}, {}, {}
    tasks = list(_col(db).find({"todos_type": todos_type, "deleted": {"$ne": True}}, {"history": 0}))
    # Une edition ou presque aucune tache de ce type n'a ete traitee n'a pas
    # ete suivie dans Cockpit (outil non utilise) : elle ne dit rien de l'utilite
    # des taches et reste hors des statistiques par item.
    # Seuil : au moins 10 % des taches echues traitees (3 au minimum).
    due_n, res_n = {}, {}
    for t in tasks:
        if t.get("due_at") and t["due_at"] < now_str:
            ed = "%s %s" % (t.get("event"), t.get("year"))
            due_n[ed] = due_n.get(ed, 0) + 1
            if t.get("status") in RESOLVED:
                res_n[ed] = res_n.get(ed, 0) + 1
    tracked = {ed for ed, n in res_n.items() if n >= max(3, 0.1 * due_n.get(ed, 0))}
    for t in tasks:
        ed = "%s %s" % (t.get("event"), t.get("year"))
        e = editions.setdefault(ed, {"edition": ed, "year": str(t.get("year")), "total": 0,
                                     "traitees": 0, "echues": 0, "suivie": ed in tracked})
        due_passed = bool(t.get("due_at") and t["due_at"] < now_str)
        s = t.get("status")
        if due_passed:
            e["echues"] += 1
            e["total"] += 1
            if s in RESOLVED:
                e["traitees"] += 1
        tid = t.get("template_item_id")
        if tid and ed not in tracked:
            items.setdefault(tid, {"id": tid, "text": t.get("text"), "total": 0, "faites": 0,
                                   "sans_objet": 0, "non_faites": 0, "faites_en_retard": 0})
            continue
        if tid:
            it = items.setdefault(tid, {"id": tid, "text": t.get("text"), "total": 0, "faites": 0,
                                        "sans_objet": 0, "non_faites": 0, "faites_en_retard": 0})
            if not due_passed:
                continue
            it["total"] += 1
            if s == "done":
                it["faites"] += 1
                done_at = t.get("done_at")
                if done_at:
                    if getattr(done_at, "tzinfo", None) is None:
                        done_at = done_at.replace(tzinfo=timezone.utc)
                    if done_at.astimezone(TZ_PARIS).strftime("%Y-%m-%dT%H:%M") > t["due_at"]:
                        it["faites_en_retard"] += 1
            elif s == "na":
                it["sans_objet"] += 1
            else:
                it["non_faites"] += 1
        elif t.get("source") in ("manual", "ia_reco"):
            k = norm_text(t.get("text"))
            if not k:
                continue
            m = manual.setdefault(k, {"text": t.get("text"), "editions": set(), "vignettes": set(),
                                      "lieux": set(), "faites": 0, "ia": False})
            m["editions"].add(ed)
            m["vignettes"].add(t.get("vignette_id"))
            if t.get("place"):
                m["lieux"].add(t["place"])
            if s == "done":
                m["faites"] += 1
            if t.get("source") == "ia_reco":
                m["ia"] = True
    candidates_add = []
    for m in manual.values():
        if len(m["editions"]) >= min_editions or len(m["vignettes"]) >= min_vignettes or m["ia"]:
            candidates_add.append({"text": m["text"], "editions": sorted(m["editions"]),
                                   "vignettes": len(m["vignettes"]), "lieux": sorted(m["lieux"])[:6],
                                   "faites": m["faites"], "ia": m["ia"]})
    candidates_add.sort(key=lambda c: (-len(c["editions"]), -c["vignettes"]))
    item_rows = list(items.values())
    rarely_done = [i for i in item_rows if i["total"] >= 3 and (i["faites"] / i["total"]) < 0.2]
    often_late = [i for i in item_rows if i["faites"] >= 3 and i["faites_en_retard"] / i["faites"] >= 0.5]
    eds = sorted(editions.values(), key=lambda e: (e["year"], e["edition"]), reverse=True)
    for e in eds:
        e["taux"] = round(100.0 * e["traitees"] / e["total"]) if e["total"] else None
    return {"type": todos_type, "items": item_rows, "candidats_ajout": candidates_add[:20],
            "rarement_faites": rarely_done, "souvent_en_retard": often_late,
            "editions": eds[:12]}


def retex_recommendations(db, max_docs=3):
    """Puces 'recommandations pour la prochaine edition' des derniers RETEX
    (une version par edition, la plus recente)."""
    out, seen = [], set()
    try:
        cur = db["edition_retex"].find(
            {"sections.recommandations_prochaine_edition": {"$exists": True}},
            {"event": 1, "year": 1, "version": 1, "created_at": 1,
             "sections.recommandations_prochaine_edition": 1}).sort("created_at", -1).limit(30)
        for d in cur:
            key = (d.get("event"), str(d.get("year")))
            if key in seen:
                continue
            seen.add(key)
            text = ((d.get("sections") or {}).get("recommandations_prochaine_edition") or "")
            bullets = [re.sub(r"^[-*]\s+", "", ln.strip()) for ln in str(text).split("\n")
                       if re.match(r"^\s*[-*]\s+", ln)]
            if bullets:
                out.append({"retex_id": str(d["_id"]), "event": d.get("event"),
                            "year": str(d.get("year")), "bullets": bullets[:20]})
            if len(out) >= max_docs:
                break
    except Exception as e:
        logger.warning("retex_recommendations : %s", e)
    return out


# ---------------------------------------------------------------------------
# Suggestions IA sur un modele
# ---------------------------------------------------------------------------

SUGGEST_SYSTEM = (
    "Tu assistes le responsable du PC Organisation d'un circuit automobile "
    "(Le Mans) qui prepare les listes de taches recurrentes des operateurs. "
    "Chaque modele s'applique a un type de lieu (portes, parkings, campings...) "
    "a son ouverture, sa fermeture ou ses bascules de controle d'acces.\n"
    "A partir du modele actuel, de son usage reel sur les editions passees et "
    "des recommandations des RETEX, propose des modifications CONCRETES du modele.\n"
    "Regles :\n"
    "- 8 propositions au plus, les plus utiles d'abord. Aucune si rien n'est justifie.\n"
    "- Une tache = une action verifiable par un operateur, 120 caracteres max, a l'infinitif.\n"
    "- Chaque proposition s'appuie sur un chiffre ou un constat fourni : n'invente rien.\n"
    "- Ne propose pas une tache deja presente sous une autre formulation.\n"
    "- Une edition marquee \"suivie\": false n'a pas ete suivie dans Cockpit (taches "
    "non cochees) : n'en tire aucune conclusion, ne propose pas de retrait sur cette base.\n"
    "Reponds en JSON avec une seule cle \"propositions\" : une chaine, une "
    "proposition par ligne, au format exact :\n"
    "AJOUTER | <phase : open, close, both, switch_control ou switch_free> | <texte> | <justification>\n"
    "RETIRER | <id de l'item> | <texte actuel> | <justification>\n"
    "MODIFIER | <id de l'item> | <nouveau texte> | <justification>\n"
    "AVANCER | <id de l'item> | <minutes avant le debut de la vignette> | <justification>\n"
    "Chaine vide si aucune proposition."
)

_SUGGEST_ACTIONS = {"AJOUTER": "add", "RETIRER": "remove", "MODIFIER": "modify", "AVANCER": "advance"}


def build_suggest_prompt(set_doc, insights, retex):
    import json
    items = normalize_template_items(set_doc.get("todos"), set_doc.get("type"))
    data = {
        "type": set_doc.get("type"),
        "modele_actuel": [{"id": i["id"], "phase": i["phase"], "texte": i["text"],
                           "critique": i["critical"], "echeance_min_avant_debut": i["due_offset_min"]}
                          for i in items],
        "usage_par_item": [{k: r[k] for k in ("id", "text", "total", "faites", "sans_objet",
                                              "non_faites", "faites_en_retard")}
                           for r in insights.get("items") or [] if r.get("total")],
        "ajouts_manuels_recurrents": insights.get("candidats_ajout") or [],
        "taux_par_edition": insights.get("editions") or [],
        "recommandations_retex": [{"edition": "%s %s" % (r["event"], r["year"]),
                                   "puces": r["bullets"]} for r in retex or []],
    }
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)[:30000]


def parse_suggestions(text, valid_ids):
    out = []
    for line in str(text or "").split("\n"):
        parts = [p.strip() for p in line.strip().lstrip("-* ").split("|")]
        if len(parts) < 3:
            continue
        action = _SUGGEST_ACTIONS.get(norm_text(parts[0]).upper())
        if not action:
            continue
        target, value = parts[1], clean_text(parts[2])
        reason = clean_text(" | ".join(parts[3:]), 400) if len(parts) > 3 else ""
        row = {"action": action, "reason": reason}
        if action == "add":
            row.update(phase=target if target in VALID_PHASES else "both", text=value)
        else:
            if target not in valid_ids:
                continue
            row["item_id"] = target
            if action == "advance":
                off = _parse_offset(re.sub(r"[^\d-]", "", value))
                if off is None:
                    continue
                row["due_offset_min"] = off
            else:
                row["text"] = value
        if row.get("text") == "" and action != "advance":
            continue
        out.append(row)
    return out[:8]


def suggest_template_changes(db, set_doc, model=None):
    """Propositions IA pour un modele. Rend {propositions, model, usage}.
    Leve pcorg_summary.ClaudeError (budget, cle absente, reseau)."""
    import pcorg_summary
    insights = template_insights(db, set_doc.get("type"))
    retex = retex_recommendations(db)
    user_prompt = build_suggest_prompt(set_doc, insights, retex)
    use_model = pcorg_summary._validate_model(model) or pcorg_summary.CLAUDE_MODEL
    sections, raw_text, usage = pcorg_summary.call_claude(
        SUGGEST_SYSTEM, user_prompt, model=use_model, section_keys=("propositions",),
        db=db, system_cache=False)
    pcorg_summary.record_ai_usage(db, "taches_modeles", use_model, usage,
                                  {"type": set_doc.get("type")})
    text = (sections or {}).get("propositions") if sections else raw_text
    valid = {i["id"] for i in normalize_template_items(set_doc.get("todos"), set_doc.get("type"))}
    return {"propositions": parse_suggestions(text, valid), "model": use_model,
            "usage": usage, "insights": insights}
