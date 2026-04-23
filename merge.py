"""
merge.py - Moteur de merge parametrages -> timetable

Deux modes d'utilisation :
  1. Import depuis app.py : from merge import run_merge; run_merge(db, event, year)
  2. Standalone : python merge.py (interactif, choix event/year)

Le merge:
  - Lit les parametrages (globalHoraires hardcode + categories dynamiques via merge_config)
  - Genere des vignettes (ouverture/fermeture) avec IDs deterministes
  - Fait un patch partiel : preserve preparation_checked, todo coches, remark editee
  - Nettoie les vignettes orphelines (origin=parametrage dont l'ID n'existe plus)
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_id(seed: str) -> str:
    """UUID5 deterministe : meme seed => meme id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _mk_seed_id(event: str, year: str, date: str, activity: str,
                 place: str = "", param_id: str = "") -> str:
    return _mk_id(f"{event}|{year}|{date}|{activity}|{place}|{param_id}")


def compute_duration(open_time: str, close_time: str) -> str:
    try:
        t_open = datetime.strptime(open_time, "%H:%M")
        t_close = datetime.strptime(close_time, "%H:%M")
    except Exception as e:
        logger.error(f"Erreur conversion heures '{open_time}'/'{close_time}': {e}")
        return ""
    if t_close > t_open:
        delta = t_close - t_open
    else:
        delta = (t_close + timedelta(days=1)) - t_open
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02}:{minutes:02}"


def _parse_iso_to_local(iso_str: Optional[str], tz_hours: int = 2) -> Optional[datetime]:
    """Parse ISO datetime to local datetime (Europe/Paris +2 ete)."""
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone(timedelta(hours=tz_hours)))
    except Exception as e:
        logger.error(f"_parse_iso_to_local: impossible de parser '{iso_str}': {e}")
        return None


def _strict_pc_skips(open_h: str, close_h: str) -> Tuple[bool, bool]:
    """Regles skip pour PC (Organisation/Autorites).
    00:00-23:59 => skip both ; 00:00-HH:MM => skip_open ; HH:MM-23:59 => skip_close.
    """
    if open_h == "00:00" and close_h == "23:59":
        return True, True
    return (open_h == "00:00"), (close_h == "23:59")


# ---------------------------------------------------------------------------
# Todos : cache + resolution
# ---------------------------------------------------------------------------

_TODOS_CACHE: Optional[dict] = None


def _reset_todos_cache():
    global _TODOS_CACHE
    _TODOS_CACHE = None


def _get_todos_cache(db) -> dict:
    """Charge les todos. Retourne {type: [{text, phase}, ...]}."""
    global _TODOS_CACHE
    if _TODOS_CACHE is not None:
        return _TODOS_CACHE
    try:
        _TODOS_CACHE = {}
        for doc in db.todos.find({}, {"type": 1, "todos": 1}):
            t = doc.get("type")
            raw = doc.get("todos") or []
            items = []
            for x in raw:
                if isinstance(x, str):
                    if x.strip():
                        items.append({"text": x.strip(), "phase": "open"})
                elif isinstance(x, dict):
                    text = str(x.get("text", "")).strip()
                    phase = x.get("phase", "both")
                    if phase not in ("open", "close", "both"):
                        phase = "both"
                    if text:
                        items.append({"text": text, "phase": phase})
            _TODOS_CACHE[t] = items
    except Exception as e:
        logger.error(f"Impossible de charger la collection 'todos': {e}")
        _TODOS_CACHE = {}
    return _TODOS_CACHE


def _resolve_todo_type(base_activity: str, category: str = "",
                       place: str = "") -> Optional[str]:
    """Devine le type de todos a partir du libelle d'activite."""
    s = (base_activity or "").lower()
    if s.startswith("porte"):
        return "portes"
    if "parking" in s:
        return "parkings"
    if "aire d'accueil" in s or "camping" in s:
        return "campings"
    if "pc organisation" in s:
        return "pcorga"
    if "pc autorit" in s:
        return "pcauthorities"
    if "centre accr" in s:
        return "centreaccreditation"
    if "help desk" in s:
        return "helpdesk"
    if "badge" in s:
        return "badges"
    if "scan" in s:
        return "scan"
    if "demontage" in s or "fin du montage" in s:
        return "demontage"
    if "montage" in s or "debut du montage" in s:
        return "montage"
    if "tribune" in s:
        return "tribunes"
    if "passerelle" in s:
        return "passerelles"
    if "sanitaire" in s:
        return "sanitaires"
    if s.strip() == "au public":
        return "global"
    return None


def _build_todo_string(todo_items: list) -> str:
    """Construit un string markdown de todos non coches."""
    if not todo_items:
        return ""
    return "\n".join(f"- [ ] {t}" for t in todo_items)


def _filter_todos_by_phase(todos_items: list, phase: str) -> list:
    """Filtre les todos par phase. phase='open' ou 'close'."""
    return [t["text"] for t in todos_items
            if t.get("phase") == phase or t.get("phase") == "both"]


def _attach_todos_str(vignette: dict, base_activity: str, category: str,
                      place: str, db=None, config_todos_type: str = None,
                      phase: str = "open") -> dict:
    """Attache les todos sous forme de string markdown sur la vignette.
    phase: 'open' pour vignette ouverture, 'close' pour fermeture."""
    try:
        todo_type = config_todos_type or _resolve_todo_type(base_activity, category, place)
        if not todo_type:
            return vignette
        cache = _get_todos_cache(db) if db is not None else {}
        todos_items = cache.get(todo_type) or []
        if not todos_items:
            return vignette
        filtered = _filter_todos_by_phase(todos_items, phase)
        if not filtered:
            return vignette
        vignette["todo"] = _build_todo_string(filtered)
        vignette["todos_type"] = todo_type
    except Exception as e:
        logger.error(f"_attach_todos_str ({base_activity}/{category}/{place}): {e}")
    return vignette


# ---------------------------------------------------------------------------
# Generation de vignettes
# ---------------------------------------------------------------------------

def generate_vignettes_for_entry(date_str, open_time, close_time,
                                 base_activity, category, place, details,
                                 id_source, v_type, merge_remark=True,
                                 extra_fields=None):
    """Genere une paire (open_vignette, close_vignette)."""
    duration = compute_duration(open_time, close_time)

    closing_date = date_str
    try:
        t_open = datetime.strptime(open_time, "%H:%M")
        t_close = datetime.strptime(close_time, "%H:%M")
        if t_close <= t_open:
            dt_date = datetime.strptime(date_str, "%Y-%m-%d")
            closing_date = (dt_date + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception as e:
        logger.error(f"Erreur calcul date fermeture {base_activity} {date_str}: {e}")

    open_activity = f"Ouverture {base_activity}"
    close_activity = f"Fermeture {base_activity}"
    open_id = _mk_id(f"{id_source}|{date_str}|{open_activity}")
    close_id = _mk_id(f"{id_source}|{date_str}|{closing_date}|{close_activity}")

    remark = ""
    if merge_remark and open_time != close_time and closing_date != date_str:
        remark = f"Fermeture prevue: {closing_date} {close_time}"

    open_vignette = {
        "_id": open_id,
        "date": date_str,
        "start": open_time,
        "end": close_time if closing_date == date_str and close_time != "23:59" else "",
        "duration": duration,
        "category": category,
        "activity": open_activity,
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "parametrage",
        "remark": remark,
        "param_id": id_source,
        "preparation_checked": "non",
    }
    close_vignette = {
        "_id": close_id,
        "date": closing_date,
        "start": "",
        "end": close_time,
        "duration": duration,
        "category": category,
        "activity": close_activity,
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "parametrage",
        "remark": "",
        "param_id": id_source,
        "preparation_checked": "non",
    }

    if extra_fields:
        for k, v in extra_fields.items():
            open_vignette[k] = v
            close_vignette[k] = v

    return open_vignette, close_vignette


def generate_single_vignette(date_str, time_str, activity, category, place,
                             id_source, v_type, extra_fields=None):
    """Genere une seule vignette (pas de paire ouverture/fermeture)."""
    vid = _mk_id(f"{id_source}|{date_str}|{activity}")
    v = {
        "_id": vid,
        "date": date_str,
        "start": time_str,
        "end": "",
        "duration": "",
        "category": category,
        "activity": activity,
        "place": place,
        "department": "SAFE",
        "type": v_type,
        "origin": "parametrage",
        "remark": "",
        "param_id": id_source,
        "preparation_checked": "non",
    }
    if extra_fields:
        v.update(extra_fields)
    return v


# ---------------------------------------------------------------------------
# globalHoraires - traitement hardcode (bloc fixe)
# ---------------------------------------------------------------------------

def _process_schedule_list(entries, base_activity, category, place,
                           v_type, id_prefix, db=None, skip_24h=False,
                           todos_type=None):
    """Traite une liste de {date, openTime, closeTime} -> paires ouv/ferm."""
    vignettes = []
    for entry in entries:
        try:
            date_str = entry["date"]
            open_t = entry.get("openTime")
            close_t = entry.get("closeTime")
            if not open_t or not close_t:
                continue
            if entry.get("is24h") or entry.get("closed"):
                continue
            id_source = entry.get("id", f"{id_prefix}_{date_str}_{open_t}")

            if skip_24h:
                skip_open, skip_close = _strict_pc_skips(open_t, close_t)
            else:
                skip_open, skip_close = False, False

            open_v, close_v = generate_vignettes_for_entry(
                date_str, open_t, close_t,
                base_activity, category, place,
                {}, id_source, v_type
            )
            open_v = _attach_todos_str(open_v, base_activity, category, place,
                                       db=db, config_todos_type=todos_type,
                                       phase="open")
            close_v = _attach_todos_str(close_v, base_activity, category, place,
                                        db=db, config_todos_type=todos_type,
                                        phase="close")

            if not skip_open:
                vignettes.append(open_v)
            if not skip_close:
                vignettes.append(close_v)
        except KeyError as e:
            logger.warning(f"{id_prefix}: cle manquante {e}")
    return vignettes


def _process_iso_single(iso_str, activity, category, place, id_source,
                         v_type, db=None, todos_type=None):
    """Traite une date ISO unique -> 1 vignette."""
    dt = _parse_iso_to_local(iso_str)
    if not dt:
        return []
    date_str = dt.strftime("%Y-%m-%d")
    v = generate_single_vignette(
        date_str, dt.strftime("%H:%M"),
        activity, category, place, id_source, v_type
    )
    v = _attach_todos_str(v, activity, category, place,
                          db=db, config_todos_type=todos_type,
                          phase="open")
    return [v]


def _process_iso_range(start_iso, end_iso, open_activity, close_activity,
                       category, place, id_source, v_type, db=None,
                       todos_type=None):
    """Traite une paire start/end ISO -> 1 vignette ouv + 1 vignette ferm."""
    start_dt = _parse_iso_to_local(start_iso)
    end_dt = _parse_iso_to_local(end_iso)
    if not start_dt or not end_dt:
        return []

    open_v = generate_single_vignette(
        start_dt.strftime("%Y-%m-%d"), start_dt.strftime("%H:%M"),
        open_activity, category, place, id_source, v_type,
        extra_fields={"remark": f"Fermeture prevue: {end_dt.strftime('%H:%M')}",
                      "duration": compute_duration(start_dt.strftime("%H:%M"),
                                                   end_dt.strftime("%H:%M"))}
    )
    open_v = _attach_todos_str(open_v, open_activity, category, place,
                               db=db, config_todos_type=todos_type,
                               phase="open")

    close_v = generate_single_vignette(
        end_dt.strftime("%Y-%m-%d"), end_dt.strftime("%H:%M"),
        close_activity, category, place, id_source, v_type
    )
    close_v["start"] = ""
    close_v["end"] = end_dt.strftime("%H:%M")
    close_v = _attach_todos_str(close_v, close_activity, category, place,
                                db=db, config_todos_type=todos_type,
                                phase="close")

    return [open_v, close_v]


def process_global_horaires(global_data, event, year, db=None):
    """Traitement hardcode de globalHoraires."""
    vignettes = []

    # center
    vignettes += _process_schedule_list(
        global_data.get("center", []),
        "Centre accreditation", "Accreditations", "Centre accreditation",
        "Organization", "center", db=db
    )

    # dates (au public)
    vignettes += _process_schedule_list(
        global_data.get("dates", []),
        "au public", "General", "Controle",
        "Timetable", "dates", db=db
    )

    # demontage
    dem = global_data.get("demontage") or {}
    if dem.get("start") and dem.get("end"):
        vignettes += _process_iso_range(
            dem["start"], dem["end"],
            "Demontage", "Fin demontage",
            "Controle", "Demontage", "demontage", "Timetable", db=db
        )

    # endBadge
    if global_data.get("endBadge"):
        vignettes += _process_iso_single(
            global_data["endBadge"],
            "Fin de la validite du badge salarie", "Controle", "Badges",
            "endBadge", "Timetable", db=db
        )

    # helpDesk - maintenant une liste de dates
    hd = global_data.get("helpDesk")
    if isinstance(hd, list):
        vignettes += _process_schedule_list(
            hd, "Help Desk", "Accreditations", "Help Desk",
            "Organization", "helpDesk", db=db
        )
    elif isinstance(hd, dict):
        # ancien format : {start, end, openTime, closeTime}
        start_d = hd.get("start")
        end_d = hd.get("end")
        open_t = hd.get("openTime")
        close_t = hd.get("closeTime")
        if start_d and end_d and open_t and close_t:
            for d in _iterate_date_range(start_d, end_d):
                pair = generate_vignettes_for_entry(
                    d, open_t, close_t,
                    "Help Desk", "Accreditations", "Help Desk",
                    {}, "helpDesk", "Organization"
                )
                pair[0] = _attach_todos_str(pair[0], "Help Desk",
                                            "Accreditations", "Help Desk",
                                            db=db, phase="open")
                pair[1] = _attach_todos_str(pair[1], "Help Desk",
                                            "Accreditations", "Help Desk",
                                            db=db, phase="close")
                vignettes.extend(pair)

    # montage
    mon = global_data.get("montage") or {}
    if mon.get("start") and mon.get("end"):
        vignettes += _process_iso_range(
            mon["start"], mon["end"],
            "Debut du montage", "Fin du montage",
            "Controle", "Montage", "montage", "Timetable", db=db
        )

    # paddockScan
    if global_data.get("paddockScan"):
        vignettes += _process_iso_single(
            global_data["paddockScan"],
            "Mise en place du controle par scan", "Controle", "Scan",
            "paddockScan", "Timetable", db=db
        )

    # scan
    if global_data.get("scan"):
        vignettes += _process_iso_single(
            global_data["scan"],
            "Mise en place du controle par scan", "Controle", "Scan",
            "scan", "Timetable", db=db
        )

    # pcOrga
    vignettes += _process_schedule_list(
        global_data.get("pcOrga", []),
        "PC Organisation", "Controle", "PC Organisation",
        "Organization", "pcOrga", db=db, skip_24h=True
    )

    # pcAuthorities
    vignettes += _process_schedule_list(
        global_data.get("pcAuthorities", []),
        "PC Autorites", "Controle", "PC Autorites",
        "Organization", "pcAuthorities", db=db, skip_24h=True
    )

    # marshall (nouveau - date ISO unique)
    if global_data.get("marshall"):
        vignettes += _process_iso_single(
            global_data["marshall"],
            "Arrivee commissaires", "Controle", "Commissaires",
            "marshall", "Timetable", db=db
        )

    # team (nouveau - date ISO unique)
    if global_data.get("team"):
        vignettes += _process_iso_single(
            global_data["team"],
            "Arrivee equipe", "Controle", "Equipe",
            "team", "Timetable", db=db
        )

    # centreMedical (nouveau - liste dates open/close + skip 24h)
    vignettes += _process_schedule_list(
        global_data.get("centreMedical", []),
        "Centre medical", "Controle", "Centre medical",
        "Organization", "centreMedical", db=db, skip_24h=True
    )

    # dps (nouveau - liste dates open/close + skip 24h)
    vignettes += _process_schedule_list(
        global_data.get("dps", []),
        "DPS", "Controle", "DPS",
        "Organization", "dps", db=db, skip_24h=True
    )

    # pressRoom (nouveau - comme centre accreditation)
    vignettes += _process_schedule_list(
        global_data.get("pressRoom", []),
        "Salle de presse", "Accreditations", "Salle de presse",
        "Organization", "pressRoom", db=db
    )

    # ticketing : ignore

    logger.info(f"{len(vignettes)} vignettes generees pour globalHoraires")
    return vignettes


def _iterate_date_range(start_date_str, end_date_str):
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception:
        return
    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Moteur generique pour categories dynamiques
# ---------------------------------------------------------------------------

def _get_merge_configs(db) -> List[dict]:
    """Charge toutes les configs de merge depuis merge_config."""
    try:
        return list(db.merge_config.find({}, {"_id": 0}))
    except Exception as e:
        logger.error(f"Erreur chargement merge_config: {e}")
        return []


def _get_category_meta(db) -> Dict[str, dict]:
    """Charge groundmaster_categories indexe par dataKey."""
    try:
        cats = list(db.groundmaster_categories.find({}, {"_id": 0}))
        return {c["dataKey"]: c for c in cats if c.get("dataKey")}
    except Exception:
        return {}


def _is_24h(entry, access_type: str = "") -> bool:
    """Determine si une entree est 24h/24."""
    if entry.get("is24h"):
        return True
    if access_type:
        sub = entry.get(access_type, {}) or {}
        if sub.get("is24h"):
            return True
    return False


def _process_dynamic_schedule(items, config, id_prefix, db=None):
    """Traite une categorie schedule ou addable_schedule.

    items: liste ou dict d'items, chacun ayant 'dates' avec sous-cles par access_type.
    config: merge_config document pour cette categorie.
    """
    vignettes = []
    label = config.get("activity_label", "{name}")
    category = config.get("timeline_category", "Controle")
    v_type = config.get("timeline_type", "Organization")
    department = config.get("department", "SAFE")
    todos_type = config.get("todos_type")
    vignette_fields_cfg = config.get("vignette_fields", [])

    # Normaliser items en liste
    if isinstance(items, dict):
        items_list = []
        for key, val in items.items():
            if isinstance(val, dict):
                val.setdefault("id", key)
                items_list.append(val)
        items = items_list

    # Determiner les access_types a traiter depuis la config ou auto-detect
    access_types = config.get("access_types", ["public", "organisation"])
    merge_access = config.get("merge_access_types", False)

    for item in items:
        item_name = item.get("name", "")
        item_id = item.get("id", item_name)
        try:
            activity_base = label.format(**item)
        except (KeyError, IndexError, ValueError):
            activity_base = label.replace("{name}", item_name)

        # Champs supplementaires a inclure dans la vignette
        extra = {}
        for field_key in vignette_fields_cfg:
            if field_key in item:
                extra[field_key] = item[field_key]

        dates_list = sorted(item.get("dates", []), key=lambda d: d.get("date", ""))
        if not dates_list:
            continue

        for i, date_entry in enumerate(dates_list):
            date_str = date_entry.get("date")
            if not date_str:
                continue

            # Champs jour supplementaires
            day_extra = dict(extra)
            for field_key in vignette_fields_cfg:
                if field_key in date_entry:
                    day_extra[field_key] = date_entry[field_key]
            if "dayControl" in date_entry and "dayControl" in vignette_fields_cfg:
                day_extra["dayControl"] = date_entry["dayControl"]
            if "dayComment" in date_entry and "dayComment" in vignette_fields_cfg:
                day_extra["dayComment"] = date_entry["dayComment"]

            # Collecter les horaires par access_type
            access_hours = {}
            for at in access_types:
                sub = date_entry.get(at, {}) or {}
                if sub.get("closed"):
                    continue
                if _is_24h(date_entry, at):
                    continue
                if "open" in sub and "close" in sub and sub["open"] and sub["close"]:
                    access_hours[at] = (sub["open"], sub["close"])

            if not access_hours:
                continue

            # Verifier si tous les types ont les memes horaires -> fusion (si active)
            unique_hours = set(access_hours.values())
            if merge_access and len(unique_hours) == 1 and len(access_hours) > 1:
                # Fusionne
                open_h, close_h = list(access_hours.values())[0]
                _emit_schedule_vignettes(
                    vignettes, dates_list, i, date_str,
                    open_h, close_h,
                    activity_base, category, item_name,
                    item_id, v_type, department, access_types,
                    day_extra, db, todos_type
                )
            else:
                # Separe par type d'acces
                for at, (open_h, close_h) in access_hours.items():
                    at_label = at.capitalize()
                    _emit_schedule_vignettes(
                        vignettes, dates_list, i, date_str,
                        open_h, close_h,
                        f"{activity_base} - {at_label}", category, item_name,
                        item_id, v_type, department, [at],
                        day_extra, db, todos_type
                    )

    return vignettes


def _emit_schedule_vignettes(vignettes, dates_list, idx, date_str,
                             open_h, close_h, activity_base, category,
                             place, id_source, v_type, department,
                             access_types_for_24h, extra_fields,
                             db, todos_type):
    """Genere ouv/ferm avec logique anti-redondance 24h."""

    # Voisins pour anti-redondance
    prev_entry = dates_list[idx - 1] if idx > 0 else {}
    next_entry = dates_list[idx + 1] if idx < len(dates_list) - 1 else {}

    def _any_24h(entry, at_list):
        return any(_is_24h(entry, at) for at in at_list)

    is_24h_prev = _any_24h(prev_entry, access_types_for_24h)
    is_24h_next = _any_24h(next_entry, access_types_for_24h)

    skip_open = (open_h == "00:00" and is_24h_prev)
    skip_close = (close_h == "23:59" and is_24h_next)

    open_v, close_v = generate_vignettes_for_entry(
        date_str, open_h, close_h,
        activity_base, category, place,
        {}, id_source, v_type,
        extra_fields=extra_fields
    )
    open_v["department"] = department
    close_v["department"] = department

    open_v = _attach_todos_str(open_v, activity_base, category, place,
                               db=db, config_todos_type=todos_type,
                               phase="open")
    close_v = _attach_todos_str(close_v, activity_base, category, place,
                                db=db, config_todos_type=todos_type,
                                phase="close")

    # Fermeture a 00:00 qui deborde sur jour+1 en 24h -> on jette
    drop_midnight = (
        close_h == "00:00" and
        close_v["date"] != date_str and
        is_24h_next
    )

    if not skip_open:
        vignettes.append(open_v)
    if not skip_close and not drop_midnight:
        vignettes.append(close_v)


def process_dynamic_categories(data, event, year, db=None):
    """Traite toutes les categories dynamiques via merge_config."""
    vignettes = []
    configs = _get_merge_configs(db) if db is not None else []

    for cfg in configs:
        if not cfg.get("enabled", True):
            continue
        data_key = cfg.get("data_key")
        if not data_key or data_key not in data:
            continue

        mode = cfg.get("mode", "schedule")
        if mode == "activation":
            # Pas de vignettes pour les activations
            continue

        items = data[data_key]
        cat_vignettes = _process_dynamic_schedule(items, cfg, data_key, db=db)
        vignettes.extend(cat_vignettes)
        logger.info(f"{len(cat_vignettes)} vignettes pour {data_key}")

    return vignettes


# ---------------------------------------------------------------------------
# Patch partiel + fusion todos
# ---------------------------------------------------------------------------

# Champs que le parametrage peut ecraser
PARAM_FIELDS = {
    "date", "start", "end", "duration", "activity",
    "category", "place", "type", "department",
    "origin", "param_id", "todos_type",
}

# Champs supplementaires possibles (vignette_fields dynamiques)
# Ceux-ci sont aussi ecrasables par le parametrage


def _merge_todos(old_todo_str: str, new_todo_str: str) -> str:
    """Fusionne les todos : preserve l'etat coche, ajoute les nouveaux, retire les supprimes.

    old_todo_str: string markdown existant dans la vignette (avec etats coches)
    new_todo_str: string markdown genere par le merge (tout non coche)

    Retourne le string fusionne.
    """
    if not new_todo_str:
        return ""

    # Parser les anciens todos avec leur etat
    old_tasks = _parse_todo_str(old_todo_str)
    new_tasks = _parse_todo_str(new_todo_str)

    # Map ancien : text -> done
    old_map = {t["text"]: t["done"] for t in old_tasks}

    # Construire le resultat : garder l'ordre du nouveau, preserver l'etat coche
    result = []
    for task in new_tasks:
        if task["text"] in old_map:
            # Tache existante : preserver l'etat coche
            done = old_map[task["text"]]
        else:
            # Nouvelle tache : non cochee
            done = False
        mark = "x" if done else " "
        result.append(f"- [{mark}] {task['text']}")

    # Les taches supprimees du merge sont retirees (meme si cochees)
    return "\n".join(result)


def _parse_todo_str(todo_str: str) -> list:
    """Parse un string markdown de todos en [{text, done}, ...]."""
    if not todo_str:
        return []
    tasks = []
    for line in todo_str.strip().split("\n"):
        line = line.strip()
        m = re.match(r'^-?\s*\[(x|X|\s)?\]\s*(.*)$', line)
        if m:
            tasks.append({
                "text": m.group(2).strip(),
                "done": bool(m.group(1) and m.group(1).lower() == "x")
            })
        elif line:
            tasks.append({"text": line, "done": False})
    return tasks


def _patch_vignette(existing: dict, new_v: dict) -> dict:
    """Applique un patch partiel : ecrase les champs parametrage, preserve les champs operateur."""
    patched = dict(existing)

    # Ecraser les champs parametrage
    for key in PARAM_FIELDS:
        if key in new_v:
            patched[key] = new_v[key]

    # Ecraser les champs supplementaires (extra_fields dynamiques)
    for key in new_v:
        if key not in PARAM_FIELDS and key not in (
            "_id", "preparation_checked", "todo", "remark"
        ):
            patched[key] = new_v[key]

    # Fusionner les todos
    old_todo = existing.get("todo", "")
    new_todo = new_v.get("todo", "")
    if new_todo:
        patched["todo"] = _merge_todos(old_todo, new_todo)
    elif "todo" in new_v:
        # Le merge dit explicitement pas de todo -> on vide
        patched["todo"] = ""

    # Remark : preserver si l'operateur l'a editee (origin manual-edit)
    if existing.get("origin") == "manual-edit":
        pass  # garder la remark de l'operateur
    else:
        if "remark" in new_v:
            patched["remark"] = new_v["remark"]

    # preparation_checked : toujours preserver
    # (deja dans patched via dict(existing))

    return patched


# ---------------------------------------------------------------------------
# Mise a jour du document timetable avec patch + nettoyage
# ---------------------------------------------------------------------------

def update_timetable_document(event, year, vignettes, db=None):
    """Met a jour le timetable avec patch partiel et nettoyage des orphelines."""
    if db is None:
        logger.error("Pas de connexion DB pour update_timetable_document")
        return

    timetable_col = db.timetable
    timetable_doc = timetable_col.find_one({"event": event, "year": year})
    if not timetable_doc:
        timetable_doc = {"event": event, "year": year, "data": {}}
        logger.info("Creation d'un nouveau document timetable")

    data_field = timetable_doc.get("data", {})

    # Set des IDs generes pour le nettoyage des orphelines
    generated_ids = set()

    for v in vignettes:
        date_key = v["date"]
        vid = v.get("_id")
        if not vid:
            vid = _mk_seed_id(
                event, year, date_key,
                str(v.get("activity", "")),
                str(v.get("place", "")),
                str(v.get("param_id", ""))
            )
            v["_id"] = vid

        generated_ids.add(vid)
        data_field.setdefault(date_key, [])
        items = data_field[date_key]

        # Chercher par _id
        idx = next((i for i, x in enumerate(items) if x.get("_id") == vid), None)

        if idx is not None:
            # Patch partiel
            items[idx] = _patch_vignette(items[idx], v)
        else:
            # Fallback : chercher par (param_id, activity) parmi les anciennes vignettes
            idx2 = next((i for i, x in enumerate(items)
                         if x.get("param_id") == v.get("param_id")
                         and x.get("activity") == v.get("activity")
                         and x.get("_id") not in generated_ids), None)
            if idx2 is not None:
                items[idx2] = _patch_vignette(items[idx2], v)
            else:
                # Nouvelle vignette
                items.append(v)

    # Nettoyage des orphelines : supprimer les vignettes origin=parametrage
    # dont l'ID n'est pas dans le set genere
    for date_key in list(data_field.keys()):
        items = data_field[date_key]
        data_field[date_key] = [
            item for item in items
            if item.get("origin") != "parametrage"
            or item.get("_id") in generated_ids
        ]
        # Supprimer la date si vide
        if not data_field[date_key]:
            del data_field[date_key]

    timetable_doc["data"] = data_field
    timetable_col.update_one(
        {"event": event, "year": year},
        {"$set": timetable_doc},
        upsert=True
    )
    logger.info(f"Timetable mis a jour : {len(vignettes)} vignettes mergees, orphelines nettoyees")


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

def run_merge(db, event: str, year: str):
    """Point d'entree principal. Appele depuis app.py ou en standalone."""
    _reset_todos_cache()
    logger.info(f"Debut merge pour {event} {year}")

    parametrage_doc = db.parametrages.find_one({"event": event, "year": year})
    if not parametrage_doc:
        logger.error(f"Aucun parametrage trouve pour {event} {year}")
        return {"error": f"Aucun parametrage pour {event} {year}"}

    data = parametrage_doc.get("data", {})
    vignettes = []

    # 1. globalHoraires (hardcode)
    if "globalHoraires" in data:
        vignettes.extend(process_global_horaires(data["globalHoraires"], event, year, db=db))

    # 2. Categories dynamiques (via merge_config)
    vignettes.extend(process_dynamic_categories(data, event, year, db=db))

    # 3. Mise a jour timetable avec patch + nettoyage
    update_timetable_document(event, year, vignettes, db=db)

    logger.info(f"Merge termine : {len(vignettes)} vignettes pour {event} {year}")
    return {"ok": True, "vignettes_count": len(vignettes)}


# ---------------------------------------------------------------------------
# Execution standalone (interactif)
# ---------------------------------------------------------------------------

EVENT_CHOICES = [
    "24H AUTOS",
    "24H MOTOS",
    "GPF",
    "GP EXPLORER",
    "SUPERBIKE",
    "LE MANS CLASSIC",
    "24H CAMIONS",
    "CONGRES SDIS",
]


def _prompt_year() -> str:
    while True:
        y = input("Annee (format YYYY) : ").strip()
        if len(y) == 4 and y.isdigit():
            return y
        print("Format invalide. Entrer une annee sur 4 chiffres, ex: 2025.")


def _prompt_event() -> str:
    print("\nSelectionne l'evenement :")
    for i, name in enumerate(EVENT_CHOICES, start=1):
        print(f"  {i}. {name}")
    while True:
        s = input("Numero ou nom exact : ").strip()
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(EVENT_CHOICES):
                return EVENT_CHOICES[idx - 1]
        for name in EVENT_CHOICES:
            if s.lower() == name.lower():
                return name
        print("Choix invalide.")


if __name__ == "__main__":
    import os as _os
    import pymongo

    # Config logging standalone
    logging.basicConfig(
        filename="merge.log",
        filemode="w",
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    _titan_env = _os.getenv("TITAN_ENV", "dev").strip().lower()
    _db_name = "titan" if _titan_env in {"prod", "production"} else "titan_dev"
    client = pymongo.MongoClient(_os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    db = client[_db_name]

    try:
        year_value = _prompt_year()
        event_value = _prompt_event()
        print(f"\nLancement du merge pour '{event_value}' {year_value}...\n")
        result = run_merge(db, event_value, year_value)
        if result.get("ok"):
            print(f"\nTermine. {result['vignettes_count']} vignettes generees.")
        else:
            print(f"\nErreur: {result.get('error')}")
    except KeyboardInterrupt:
        print("\nOperation annulee.")
