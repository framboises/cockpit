"""
intrusion_dispatch.py - Intrusions confirmees envoyees aux tablettes Field
(livraison 3 des patrouilles, docs/claude/patrouilles.md 5.4).

Sans toucher au recepteur Hik (hors depot) : on lit les episodes
`hik_intrusions`. Reglage par groupe de cameras (`detection_groups.field_dispatch`) :

    mode         off (defaut) | ai (l'IA confirme : envoi automatique)
                 | operator (envoi a l'avis "vrai" de l'operateur)
    labels       ["personne"] par defaut, "vehicule" en option
    burst_min    anti-rafale : une camera deja envoyee depuis moins de N min
                 ne relance pas d'envoi, l'episode s'ajoute a la fiche en cours
    max_per_hour plafond d'envois par heure pour le groupe ; au-dela la fiche
                 est creee pour le PC Org, sans proposition aux tablettes
    category     categorie de la fiche (Securite par defaut)

Envoi = fiche de main courante (position de la camera, photo de l'IA jointe,
`content_category.intrusion_*`) puis proposition par le dispatch des fiches
(dispatch_auto.start) : tablette la plus proche de la categorie, zone de
competence couvrant la camera, 60 s pour repondre, tablette suivante, puis
file du service. Acceptee : une patrouille programmee en cours passe en pause
(patrols.sync_pause).

Mode `ai` : `tick` est appele par patrol_engine.py (tache planifiee, passage
toutes les 3 s), JAMAIS par un thread de Cockpit. Mode `operator` et bouton
"Envoyer a une patrouille" : appel direct depuis la route de la page Detection.

Un seul envoi par episode : `hik_intrusions.field_dispatch` est pose par
find_one_and_update conditionne (absent) avant tout le reste.
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pcorg_history as PH

logger = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
MODES = ("off", "ai", "operator")
LABELS = ("personne", "vehicule")
CATEGORIES = ("PCO.Securite", "PCO.Secours", "PCO.Technique", "PCO.Flux", "PCO.Information", "PCO.MainCourante")
DEFAULTS = {"mode": "off", "labels": ["personne"], "burst_min": 10, "max_per_hour": 6,
            "category": "PCO.Securite"}
TIMEOUT_S = 60              # delai de reponse d'une tablette (proposition courte)
RECENT_MIN = 15             # au-dela, un episode n'est plus envoye (perime)
BOT = "Intrusions"
HIK_IMAGE_DIR = "E:/TITAN/production/hik_images"
LABEL_TEXT = {"personne": "personne", "vehicule": "vehicule"}


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def clean_settings(raw):
    """Reglage d'envoi d'un groupe de cameras, defauts appliques."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if raw.get("mode") in MODES:
        out["mode"] = raw["mode"]
    if isinstance(raw.get("labels"), list):
        out["labels"] = [l for l in LABELS if l in raw["labels"]] or ["personne"]
    for key, lo, hi in (("burst_min", 0, 120), ("max_per_hour", 1, 60)):
        try:
            out[key] = max(lo, min(hi, int(raw.get(key, out[key]))))
        except (TypeError, ValueError):
            pass
    if raw.get("category") in CATEGORIES:
        out["category"] = raw["category"]
    return out


def group_settings(g):
    return clean_settings((g or {}).get("field_dispatch"))


def episode_label(ep):
    """Ce qu'est la cible : avis de l'operateur s'il en a donne un (velo =
    personne), sinon le libelle de l'IA."""
    truth = (ep.get("ai_vote") or {}).get("truth")
    if truth in ("personne", "velo"):
        return "personne"
    if truth == "vehicule":
        return "vehicule"
    return (ep.get("ai") or {}).get("label")


def _groups_of(db, ep):
    import detection_arming as arming
    gs = list(db["detection_groups"].find({}, {"name": 1, "members": 1, "field_dispatch": 1}))
    return arming.channel_groups(gs, ep.get("camera_id"), ep.get("channel"))


def _settings_for(db, ep):
    """(groupe, reglage) qui s'applique a l'episode : le premier groupe dont
    l'envoi est actif, sinon le premier groupe (ou aucun)."""
    gs = _groups_of(db, ep)
    for g in gs:
        st = group_settings(g)
        if st["mode"] != "off":
            return g, st
    return (gs[0] if gs else None), (group_settings(gs[0]) if gs else dict(DEFAULTS))


def _camera_point(db, ep):
    import detection as D
    cam = db["cockpit_cameras"].find_one({"_id": ep.get("camera_id")},
                                         {"name": 1, "ip": 1, "map": 1, "channels": 1})
    if not cam:
        return None
    pos, _src, _geo = D._device_position(db, cam)
    if not pos or pos.get("lat") is None:
        return None
    return float(pos["lat"]), float(pos["lng"])


def _copy_photo(rel, event, year):
    """Photo de l'IA copiee dans les photos des tablettes (les images de la
    page Detection sont reservees aux admins). (photo_url, thumb_url) ou None."""
    import field as F
    if not rel or not F._PIL_AVAILABLE:
        return None
    src = os.path.normpath(os.path.join(HIK_IMAGE_DIR, rel))
    if not src.startswith(os.path.normpath(HIK_IMAGE_DIR)) or not os.path.isfile(src):
        return None
    try:
        from PIL import Image
        img = Image.open(src)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > F.FIELD_PHOTO_MAX_DIM:
            img.thumbnail((F.FIELD_PHOTO_MAX_DIM, F.FIELD_PHOTO_MAX_DIM), Image.LANCZOS)
        sub = os.path.join(str(event or "unknown"), str(year or "unknown"))
        full_dir = os.path.join(F.FIELD_PHOTOS_DIR, sub)
        os.makedirs(os.path.join(full_dir, "thumbs"), exist_ok=True)
        name = "%s_intrusion.jpg" % uuid.uuid4().hex[:8]
        img.save(os.path.join(full_dir, name), format="JPEG", quality=F.FIELD_PHOTO_JPEG_QUALITY, optimize=True)
        th = img.copy()
        th.thumbnail((F.FIELD_PHOTO_THUMB_DIM, F.FIELD_PHOTO_THUMB_DIM), Image.LANCZOS)
        th.save(os.path.join(full_dir, "thumbs", name), format="JPEG", quality=F.FIELD_PHOTO_THUMB_QUALITY, optimize=True)
        sub = sub.replace(os.sep, "/")
        return "/field/photos/%s/%s" % (sub, name), "/field/photos/%s/thumbs/%s" % (sub, name)
    except Exception as e:
        logger.warning("intrusion : photo %s non copiee (%s)", rel, e)
        return None


def _describe(ep, label):
    cam = ep.get("camera_name") or "camera"
    if ep.get("channel_name") and str(ep["channel_name"]) != cam:
        cam += " - " + str(ep["channel_name"])
    at = _aware(ep.get("started_at"))
    hm = at.astimezone(PARIS).strftime("%H:%M") if isinstance(at, datetime) else "?"
    ai = ep.get("ai") or {}
    conf = (" (IA %d %%)" % round(ai["conf"] * 100)) if isinstance(ai.get("conf"), (int, float)) else ""
    return cam, hm, conf, LABEL_TEXT.get(label, label or "cible")


def _entry_for(ep, label, photo, now, by=None):
    cam, hm, conf, what = _describe(ep, label)
    text = "Intrusion detectee a %s : %s, camera %s%s" % (hm, what, cam, conf)
    if by:
        text += "\nEnvoyee par " + by
    extra = {"photos": [{"photo": photo[0], "thumb": photo[1]}], "photo": photo[0], "thumb": photo[1]} if photo else {}
    return PH.make_entry(by or BOT, text, ts=now, system=None if by else True, **extra)


def _cam_key(ep):
    return "%s:%s" % (ep.get("camera_id"), ep.get("channel"))


def dispatch_episode(db, ep, by=None, now=None, settings=None, group=None):
    """Envoie un episode : rattache a la fiche en cours de la camera
    (anti-rafale), ou nouvelle fiche proposee aux tablettes (PC Org seul
    au-dela du plafond). Retourne (ok, code, fiche_id). Codes : sent,
    grouped, pc_org, deja_envoye, sans_position."""
    import dispatch_auto as DA
    import event_courant as EC
    import patrols as P
    now = now or _now()
    if settings is None:
        group, settings = _settings_for(db, ep)
    label = episode_label(ep) or "personne"
    claim = {"state": "claimed", "at": now, "by": by, "label": label,
             "group_id": str(group["_id"]) if group else None}
    got = db["hik_intrusions"].find_one_and_update(
        {"_id": ep["_id"], "field_dispatch": {"$exists": False}}, {"$set": {"field_dispatch": claim}})
    if not got:
        cur = (db["hik_intrusions"].find_one({"_id": ep["_id"]}, {"field_dispatch": 1}) or {}).get("field_dispatch") or {}
        return False, "deja_envoye", cur.get("fiche_id")

    def done(state, fiche_id, reason=None):
        db["hik_intrusions"].update_one({"_id": ep["_id"]}, {"$set": {
            "field_dispatch.state": state, "field_dispatch.fiche_id": fiche_id, "field_dispatch.reason": reason}})
        logger.info("intrusion %s (%s) : %s %s%s", ep["_id"], ep.get("camera_name"), state, fiche_id or "",
                    (" - " + reason) if reason else "")
        return state in ("sent", "grouped", "pc_org"), state, fiche_id

    try:
        event, year = EC.current_event(db, now)
    except Exception:
        event, year = None, None
    key = _cam_key(ep)
    # Anti-rafale : fiche ouverte de la meme camera envoyee depuis moins de burst_min
    if settings["burst_min"]:
        f = db["pcorg"].find_one({"content_category.intrusion_camera": key, "status_code": {"$ne": 10},
                                  "ts": {"$gte": now - timedelta(minutes=settings["burst_min"])}},
                                 sort=[("ts", -1)], projection={"_id": 1, "event": 1, "year": 1})
        if f:
            photo = _copy_photo((ep.get("ai") or {}).get("photo") or (ep.get("cover") or {}).get("photo"),
                                f.get("event"), f.get("year"))
            PH.append_entry(db["pcorg"], f["_id"], _entry_for(ep, label, photo, now, by), inc_bounce=True,
                            extra_filter={"status_code": {"$ne": 10}})
            return done("grouped", f["_id"], "fiche en cours de la camera")
    pt = _camera_point(db, ep)
    photo = _copy_photo((ep.get("ai") or {}).get("photo") or (ep.get("cover") or {}).get("photo"), event, year)
    cam, hm, conf, what = _describe(ep, label)
    text = "Intrusion : %s detecte%s - %s" % (what, "e" if what == "personne" else "", cam)
    try:
        y = int(year) if year is not None else None
    except (TypeError, ValueError):
        y = year
    fid = str(uuid.uuid5(uuid.NAMESPACE_URL, "intrusion|%s" % ep["_id"]))
    zone_ids = P.zones_of_point(db, pt[0], pt[1]) if pt else []
    cat = settings["category"]
    doc = {
        "_id": fid, "event": event, "year": y, "ts": now,
        "timestamp_iso": now.astimezone(PARIS).isoformat(), "close_ts": None, "close_iso": None,
        "category": cat, "source": cat, "text": text, "text_full": text, "comment": "", "comment_history": [],
        "operator": BOT, "operator_id_create": "intrusion:%s" % ep["_id"], "operator_close": None,
        "operator_id_close": None, "status_code": 0, "severity": 0, "niveau_urgence": None,
        "is_incident": False, "area": None,
        "gps": {"type": "Point", "coordinates": [pt[1], pt[0]]} if pt else None, "group": None,
        "content_category": {
            "intrusion_episode": str(ep["_id"]), "intrusion_camera": key,
            "intrusion_camera_name": cam, "intrusion_label": label,
            "intrusion_group": str(group["_id"]) if group else None,
            "intrusion_zone_ids": zone_ids,
            "intrusion_photo": photo[0] if photo else None, "intrusion_thumb": photo[1] if photo else None,
        },
        "dispatch": {"timeout_s": TIMEOUT_S},
        "extracted": {"phones": None, "plates": None}, "tags": ["intrusion"],
        "synced_at": None, "sql_id": None, "guid": None, "server": "COCKPIT", "bounce_rev": 1,
    }
    try:
        db["pcorg"].insert_one(doc)
    except Exception as e:      # deja creee (episode rejoue) : on garde la fiche existante
        logger.debug("fiche intrusion %s : %s", fid, e)
    PH.append_entry(db["pcorg"], fid, _entry_for(ep, label, photo, now, by), inc_bounce=True)
    if not pt:
        PH.append_entry(db["pcorg"], fid, PH.make_entry(BOT, "Camera sans position : pas de proposition aux tablettes",
                                                        ts=now, system=True))
        return done("pc_org", fid, "camera sans position")
    if group is not None:
        n = db["pcorg"].count_documents({"content_category.intrusion_group": str(group["_id"]),
                                         "ts": {"$gte": now - timedelta(hours=1)}, "_id": {"$ne": fid}})
        if n >= settings["max_per_hour"]:
            PH.append_entry(db["pcorg"], fid, PH.make_entry(
                BOT, "Plafond de %d envois par heure atteint pour le groupe %s : PC Org seulement"
                % (settings["max_per_hour"], group.get("name") or "?"), ts=now, system=True))
            return done("pc_org", fid, "plafond horaire")
    ok, code = DA.start(db, fid, trigger="intrusion", operator=by)
    if not ok:
        return done("pc_org", fid, "dispatch : " + code)
    return done("sent", fid)


def eligible(ep, settings, now):
    """Episode a envoyer automatiquement (mode ai) : dans le planning, recent,
    confirme par l'IA, pas ecarte par l'operateur, libelle retenu."""
    import detection as D
    if ep.get("armed") is False or (ep.get("ai") or {}).get("status") != "confirmed":
        return False
    if D.portee(ep) == "ecartee":
        return False
    last = _aware(ep.get("last_at") or ep.get("started_at"))
    if not isinstance(last, datetime) or now - last > timedelta(minutes=RECENT_MIN):
        return False
    return episode_label(ep) in settings["labels"]


def tick(db, now=None):
    """Passage du moteur : episodes recents des groupes en mode `ai`.
    Retourne le nombre d'envois."""
    now = now or _now()
    groups = [g for g in db["detection_groups"].find({"field_dispatch.mode": "ai"},
                                                     {"name": 1, "members": 1, "field_dispatch": 1})]
    if not groups:
        return 0
    n = 0
    for g in groups:
        st = group_settings(g)
        conds = [{"camera_id": m.get("camera_id"), "channel": m.get("channel")} for m in g.get("members") or []]
        if not conds:
            continue
        q = {"started_at": {"$gte": now - timedelta(minutes=RECENT_MIN)}, "armed": {"$ne": False},
             "ai.status": "confirmed", "field_dispatch": {"$exists": False}, "$or": conds}
        for ep in db["hik_intrusions"].find(q, {"photos": 0, "targets": 0}).sort("started_at", 1).limit(20):
            if not eligible(ep, st, now):
                continue
            try:
                ok, code, _fid = dispatch_episode(db, ep, now=now, settings=st, group=g)
                n += 1 if ok else 0
            except Exception:
                logger.exception("intrusion %s : envoi en echec", ep.get("_id"))
    return n


def on_operator_truth(db, ep_id, truth, by):
    """Avis "vrai" de l'operateur (personne, vehicule, velo) : envoi si le
    groupe de la camera est en mode `operator` et le libelle retenu. Ne leve
    jamais. Retourne le code ou None."""
    try:
        ep = db["hik_intrusions"].find_one({"_id": ep_id}, {"photos": 0, "targets": 0})
        if not ep or ep.get("field_dispatch") or ep.get("armed") is False:
            return None
        group, st = _settings_for(db, ep)
        if st["mode"] != "operator" or episode_label(ep) not in st["labels"]:
            return None
        last = _aware(ep.get("last_at") or ep.get("started_at"))
        if not isinstance(last, datetime) or _now() - last > timedelta(minutes=RECENT_MIN):
            return None
        return dispatch_episode(db, ep, by=by, settings=st, group=group)[1]
    except Exception:
        logger.exception("intrusion %s : envoi sur avis en echec", ep_id)
        return None


def pub_dispatch(db, ep):
    """Etat de l'envoi d'un episode pour la page Detection (fiche et
    propositions en direct)."""
    fd = ep.get("field_dispatch") or {}
    if not fd:
        return None
    out = {"state": fd.get("state"), "fiche_id": fd.get("fiche_id"), "by": fd.get("by"), "reason": fd.get("reason"),
           "at": _aware(fd.get("at")).isoformat() if isinstance(fd.get("at"), datetime) else None}
    f = db["pcorg"].find_one({"_id": fd.get("fiche_id")}, {"dispatch": 1, "content_category.patrouille": 1,
                                                          "status_code": 1}) if fd.get("fiche_id") else None
    if f:
        d = f.get("dispatch") or {}
        out.update(dispatch_state=d.get("state"), unit=(f.get("content_category") or {}).get("patrouille") or None,
                   closed=f.get("status_code") == 10,
                   current=(d.get("current") or {}).get("device_name"),
                   attempts=[{"device_name": a.get("device_name"), "answer": a.get("answer")}
                             for a in d.get("attempts") or []])
    return out
