"""Outils de lecture exposes a Alfred (VM Linux) par Cockpit.

POURQUOI ICI ET PAS SUR LA VM

Alfred sait deja lire Mongo directement. Mais les chiffres qu'un operateur
voit sur Cockpit ne sont pas des documents bruts : ce sont des CALCULS
(presents corriges des vehicules, verdict trafic a double verrou, consigne
meteo du mur, factorisation de la timeline). Les reecrire sur la VM, c'est
garantir qu'un jour Alfred et l'ecran se contrediront. Ces outils appellent
donc LITTERALEMENT les fonctions qui alimentent la montre et les murs
(watch_pages, trafic_etat, meteo_etat, presents_etat, pcorg_summary) et ne
font que rendre leur sortie lisible par un modele de langage : des cles
francaises explicites, des heures de Paris, jamais de code compact.

Fonctions pures et lectures Mongo, `db` toujours passe en argument, aucun
import de Flask ni d'app (meme regle que watch_pages). Chaque outil rend un
dict JSON-serialisable et ne leve jamais vers l'appelant : une source
injoignable rend {"disponible": False, ...} pour que le modele dise << je ne
sais pas >> au lieu d'inventer.

LECTURE SEULE. Aucun outil n'ecrit, nulle part.

Le perimetre de l'operateur (categories main courante, slugs d'alertes) est
fourni par l'appelant dans `ctx` (alfred_chat.py le tire du jeton de portee
signe) : ce module ne decide d'aucun droit, il les applique.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ_PARIS = ZoneInfo("Europe/Paris")

VERDICTS_TRAFIC = ["FLUIDE", "VIGILANCE", "TENSION", "CRITIQUE"]
SEVERITES_AXE = ["fluide", "ralenti", "dense", "tres charge", "bloque"]
# Sens de watch_pages.build_trafic. "p" = tag Waze #P = AUTOROUTE (A28, A11),
# pas parking : cf. docs/claude/montre.md, la premiere version de la montre
# s'etait trompee dans les deux sens.
SENS_AXE = {"i": "entree", "o": "sortie", "p": "autoroute"}
STATUT_CLOS = 10
TEXTE_MAX = 400
LIMITE_FICHES_MAX = 25

CATEGORIES_RACCOURCIS = {
    "secours": "PCO.Secours",
    "securite": "PCO.Securite",
    "technique": "PCO.Technique",
    "flux": "PCO.Flux",
    "information": "PCO.Information",
    "fourriere": "PCO.Fourriere",
    "maincourante": "PCO.MainCourante",
    "main courante": "PCO.MainCourante",
    "surete": "PCS.Surete",
}

URGENCES = {"EU": "detresse vitale", "UA": "urgence absolue",
            "UR": "urgence relative", "IMP": "implique"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_utc(dt):
    """Datetime pymongo (naif UTC) ou ISO -> datetime conscient UTC, ou None."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _paris(dt, avec_date=True):
    """'07/10 14:35' en heure de Paris (ou '14:35'), None si inconnu."""
    u = _as_utc(dt)
    if u is None:
        return None
    p = u.astimezone(TZ_PARIS)
    return p.strftime("%d/%m %H:%M") if avec_date else p.strftime("%H:%M")


def _duree(secondes):
    """'4 min 20 s' / '45 s'. Meme lecture que le bloc Temps d'acces."""
    if secondes is None:
        return None
    s = int(secondes)
    if s < 60:
        return "%d s" % s
    m, s = divmod(s, 60)
    if m < 60:
        return "%d min %02d s" % (m, s)
    h, m = divmod(m, 60)
    return "%d h %02d min" % (h, m)


def _court(texte, n=TEXTE_MAX):
    t = re.sub(r"\s+", " ", str(texte or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _cat_label(cat):
    cat = str(cat or "")
    return cat.split(".", 1)[1] if "." in cat else cat


def _indisponible(source, exc=None):
    if exc is not None:
        logger.warning("alfred_tools : %s indisponible (%s)", source, exc)
    return {"disponible": False, "source": source,
            "note": "Donnee indisponible : ne pas l'estimer, le dire."}


def _event_pairs(db, ctx):
    """Paires (event, year) visees : celle de l'operateur si fournie, sinon les
    evenements actifs (epreuves + SAISON, ouvertes de SAISON N-1 comprises)."""
    ev, yr = (ctx or {}).get("event"), (ctx or {}).get("year")
    if ev and yr:
        try:
            return [(str(ev), int(yr))]
        except (TypeError, ValueError):
            pass
    import event_courant
    return event_courant.active_pairs(db, include_previous_saison=True)


def _pcorg_filter(db, ctx):
    import event_courant
    flt = event_courant.pairs_filter(_event_pairs(db, ctx))
    cat_q = (ctx or {}).get("cat_query") or {"$regex": "^PC[OS]\\."}
    return {"$and": [flt, {"category": cat_q}]}


def _cat_autorisee(ctx, categorie):
    q = (ctx or {}).get("cat_query")
    if not q or "$in" not in q:
        return True
    return categorie in q["$in"]


def _normalise_categorie(raw):
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith(("PCO.", "PCS.")):
        return s
    k = s.lower().replace("é", "e").replace("è", "e").replace("_", " ").strip()
    return CATEGORIES_RACCOURCIS.get(k) or CATEGORIES_RACCOURCIS.get(k.replace(" ", ""))


def _fiche(doc, detail=False):
    cc = doc.get("content_category") or {}
    area = doc.get("area") or {}
    hist = [h for h in (doc.get("comment_history") or []) if isinstance(h, dict)]
    out = {
        "id": str(doc.get("_id")),
        "numero": doc.get("sql_id"),
        "ouverte_le": _paris(doc.get("ts")),
        "categorie": _cat_label(doc.get("category")),
        "nature": cc.get("sous_classification"),
        "urgence": URGENCES.get(doc.get("niveau_urgence"), doc.get("niveau_urgence")),
        "statut": "close" if doc.get("status_code") == STATUT_CLOS else "en cours",
        "zone": area.get("desc"),
        "operateur": doc.get("operator"),
        "texte": _court(doc.get("text_full") or doc.get("text"),
                        1500 if detail else 280),
        "nb_actions": len(hist),
    }
    if doc.get("status_code") == STATUT_CLOS:
        out["close_le"] = _paris(doc.get("close_ts"))
    garder = hist[-15:] if detail else hist[-1:]
    out["actions" if detail else "derniere_action"] = [
        {"heure": _paris(h.get("ts")), "par": h.get("operator"),
         "texte": _court(h.get("text"), 400 if detail else 200)}
        for h in garder if (h.get("text") or "").strip()
    ]
    return out


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def t_evenement(db, args, ctx):
    """Evenement(s) en cours et phase."""
    try:
        import alfred_evenements
        import event_courant
        p = event_courant.payload(db)
        out = {"disponible": True,
               "evenement_courant": p.get("current"),
               "evenements_actifs": p.get("active"),
               "selection_operateur": {"evenement": (ctx or {}).get("event"),
                                       "annee": (ctx or {}).get("year")}}
        # Description saisie en Configuration : pour comprendre, pas pour citer
        vus, contextes = set(), []
        paires = [((ctx or {}).get("event"), (ctx or {}).get("year"))] + [
            (a.get("event"), a.get("year")) for a in p.get("active") or []]
        for ev, yr in paires:
            if ev and ev not in vus:
                vus.add(ev)
                c = alfred_evenements.contexte(db, ev, yr)
                if c:
                    contextes.append(c)
        if contextes:
            out["contexte_evenements"] = contextes
        return out
    except Exception as exc:
        return _indisponible("evenement", exc)


def t_main_courante_compteurs(db, args, ctx):
    """Fiches en cours / closes par categorie, et creees aujourd'hui."""
    try:
        debut_jour = datetime.now(TZ_PARIS).replace(hour=0, minute=0, second=0,
                                                    microsecond=0)
        debut_utc = debut_jour.astimezone(timezone.utc).replace(tzinfo=None)
        pipe = [
            {"$match": _pcorg_filter(db, ctx)},
            {"$group": {"_id": {"cat": "$category",
                                "clos": {"$eq": ["$status_code", STATUT_CLOS]}},
                        "n": {"$sum": 1},
                        "jour": {"$sum": {"$cond": [{"$gte": ["$ts", debut_utc]}, 1, 0]}}}},
        ]
        par_cat = {}
        for row in db["pcorg"].aggregate(pipe):
            cat = _cat_label((row["_id"] or {}).get("cat"))
            d = par_cat.setdefault(cat, {"en_cours": 0, "closes": 0, "creees_aujourdhui": 0})
            d["closes" if (row["_id"] or {}).get("clos") else "en_cours"] += int(row["n"])
            d["creees_aujourdhui"] += int(row.get("jour") or 0)
        total = {k: sum(v[k] for v in par_cat.values())
                 for k in ("en_cours", "closes", "creees_aujourdhui")}
        return {"disponible": True, "perimetre": _perimetre(db, ctx),
                "total": total, "par_categorie": par_cat}
    except Exception as exc:
        return _indisponible("main_courante", exc)


def _perimetre(db, ctx):
    return [{"evenement": e, "annee": y} for e, y in _event_pairs(db, ctx)]


def t_main_courante_fiches(db, args, ctx):
    """Liste filtree des fiches."""
    args = args or {}
    try:
        flt = _pcorg_filter(db, ctx)
        statut = str(args.get("statut") or "en_cours").lower()
        if statut in ("en_cours", "ouvertes", "ouverte", "open"):
            flt["$and"].append({"status_code": {"$ne": STATUT_CLOS}})
        elif statut in ("closes", "close", "fermees", "closed"):
            flt["$and"].append({"status_code": STATUT_CLOS})
        cat = _normalise_categorie(args.get("categorie"))
        if args.get("categorie") and not cat:
            return {"disponible": True, "fiches": [],
                    "note": "Categorie inconnue. Valeurs : " + ", ".join(sorted(set(CATEGORIES_RACCOURCIS)))}
        if cat:
            if not _cat_autorisee(ctx, cat):
                return {"disponible": True, "fiches": [],
                        "note": "Categorie hors du perimetre de cet operateur."}
            flt["$and"].append({"category": cat})
        urg = str(args.get("urgence") or "").upper()
        if urg in URGENCES:
            flt["$and"].append({"niveau_urgence": urg})
        heures = args.get("depuis_heures")
        if heures not in (None, ""):
            try:
                h = max(0.25, min(float(heures), 24 * 31))
                depuis = datetime.now(timezone.utc) - timedelta(hours=h)
                flt["$and"].append({"ts": {"$gte": depuis.replace(tzinfo=None)}})
            except (TypeError, ValueError):
                pass
        texte = str(args.get("texte") or "").strip()
        if texte:
            rx = {"$regex": re.escape(texte[:80]), "$options": "i"}
            flt["$and"].append({"$or": [{"text": rx}, {"text_full": rx},
                                        {"area.desc": rx}, {"comment": rx},
                                        {"content_category.sous_classification": rx}]})
        try:
            limite = max(1, min(int(args.get("limite") or 10), LIMITE_FICHES_MAX))
        except (TypeError, ValueError):
            limite = 10
        col = db["pcorg"]
        total = col.count_documents(flt)
        docs = list(col.find(flt, {"comment": 0, "comment_sql": 0})
                    .sort("ts", -1).limit(limite))
        return {"disponible": True, "perimetre": _perimetre(db, ctx),
                "total_correspondant": total, "affichees": len(docs),
                "fiches": [_fiche(d) for d in docs]}
    except Exception as exc:
        return _indisponible("main_courante", exc)


def t_main_courante_fiche(db, args, ctx):
    """Detail d'une fiche (par id ou numero Prysm)."""
    args = args or {}
    ident = str(args.get("id") or args.get("numero") or "").strip()
    if not ident:
        return {"disponible": True, "note": "Preciser id ou numero de fiche."}
    try:
        ors = [{"_id": ident}]
        try:
            from bson.objectid import ObjectId
            ors.append({"_id": ObjectId(ident)})
        except Exception:
            pass
        if ident.isdigit():
            ors += [{"sql_id": int(ident)}, {"sql_id": ident}]
        doc = db["pcorg"].find_one({"$or": ors}, {"comment": 0, "comment_sql": 0})
        if not doc:
            return {"disponible": True, "trouvee": False}
        if not _cat_autorisee(ctx, doc.get("category")):
            return {"disponible": True, "trouvee": False,
                    "note": "Fiche hors du perimetre de cet operateur."}
        return {"disponible": True, "trouvee": True, "fiche": _fiche(doc, detail=True)}
    except Exception as exc:
        return _indisponible("main_courante", exc)


def t_trafic(db, args, ctx):
    """Verdict global et axes, meme calcul que le mur circulation et la montre."""
    try:
        import watch_pages
        b = watch_pages.build_trafic(db)
    except Exception as exc:
        return _indisponible("trafic", exc)
    if not b:
        return _indisponible("trafic")
    age = None
    if b.get("t"):
        age = int(datetime.now(timezone.utc).timestamp() - b["t"])
    axes = []
    for nom, sens, cur, sev, fl, delta in b.get("r") or []:
        alertes = None
        if fl is not None:
            alertes = [lib for bit, lib in ((1, "accident"), (2, "bouchon"), (4, "danger"))
                       if fl & bit]
        axes.append({"axe": nom, "sens": SENS_AXE.get(sens),
                     "temps": _duree(cur), "retard": "+" + _duree(delta),
                     "etat": SEVERITES_AXE[sev] if 0 <= sev < len(SEVERITES_AXE) else sev,
                     "alertes_waze": alertes})
    vd = b.get("vd")
    return {
        "disponible": True,
        "verdict": VERDICTS_TRAFIC[vd] if vd is not None else None,
        "note_verdict": None if vd is not None else
            "Alertes Waze perimees : verdict non affirmable.",
        "accidents_en_zone": b.get("ac"), "bouchons_waze": b.get("jm"),
        "dangers_waze": b.get("hz"),
        "fraicheur_s": age,
        "axes": axes,
    }


def t_meteo(db, args, ctx):
    """Etat du mur meteo : actuel, pluie, consignes et contraintes non normales."""
    try:
        import meteo_etat
        now = datetime.now(TZ_PARIS).replace(tzinfo=None)
        e = meteo_etat.etat_mur(db, now) or {}
    except Exception as exc:
        return _indisponible("meteo", exc)
    if not e:
        return _indisponible("meteo")
    act = e.get("actuel") or {}
    pluie = e.get("prochaine_pluie") or {}
    vig = e.get("vigilance") or {}
    contraintes = []
    for c in e.get("contraintes") or []:
        if (c.get("niveau") or "normal") == "normal":
            continue
        contraintes.append({k: c.get(k) for k in
                            ("libelle", "niveau", "pic", "unite", "pic_heure", "consigne")})
    return {
        "disponible": True,
        "actuel": {"temperature_c": act.get("temperature_c"),
                   "vent_moyen_kmh": act.get("vent_moyen_kmh"),
                   "rafale_kmh": act.get("vent_rafale_kmh")},
        "pluie": ({"attendue": True, "dans_min": pluie.get("dans_min"),
                   "pic_mmh": pluie.get("pic_mmh")}
                  if pluie.get("attendue") else {"attendue": False}),
        "vigilance_meteo_france": vig.get("couleur_jour"),
        "consignes": [{"niveau": c.get("niveau"), "texte": c.get("texte")}
                      for c in e.get("consignes") or []],
        "contraintes": contraintes,
    }


def t_alertes(db, args, ctx):
    """Alertes actives de la centrale (filtrees sur les slugs de l'operateur)."""
    try:
        now = datetime.now(timezone.utc)
        q = {"expiresAt": {"$gt": now}}
        slugs = (ctx or {}).get("alert_slugs")
        if slugs is not None:
            q["definition_slug"] = {"$in": list(slugs)}
        docs = list(db["cockpit_active_alerts"].find(q).sort("triggeredAt", -1).limit(20))
        return {"disponible": True, "nombre": len(docs), "alertes": [
            {"titre": d.get("title"), "message": _court(d.get("message"), 300),
             "declenchee": _paris(d.get("triggeredAt")),
             "expire": _paris(d.get("expiresAt"), avec_date=False),
             "type": d.get("definition_slug")}
            for d in docs]}
    except Exception as exc:
        return _indisponible("alertes", exc)


def t_timeline(db, args, ctx):
    """Prochaines vignettes de la timeline (ouvertures factorisees)."""
    args = args or {}
    try:
        heures = max(1, min(int(args.get("heures") or 12), 48))
    except (TypeError, ValueError):
        heures = 12
    try:
        import pcorg_summary
        pairs = _event_pairs(db, ctx)
        out = []
        for ev, yr in pairs:
            for v in pcorg_summary.get_upcoming_timetable(db, ev, yr, hours=heures) or []:
                dt = None
                try:
                    dt = datetime.fromisoformat(str(v.get("datetime")))
                except (TypeError, ValueError):
                    pass
                out.append({"quand": dt.strftime("%d/%m %H:%M") if dt else v.get("datetime"),
                            "activite": v.get("activity"), "lieu": v.get("place"),
                            "evenement": ev, "_k": dt.timestamp() if dt else 0})
        out.sort(key=lambda x: x.pop("_k") if "_k" in x else 0)
        return {"disponible": True, "fenetre_heures": heures, "vignettes": out[:30]}
    except Exception as exc:
        return _indisponible("timeline", exc)


def t_presents(db, args, ctx):
    """Presents sur site (compteur principal, meme calcul que l'accueil)."""
    try:
        import presents_etat
        import watch_state
        now_utc = datetime.now(timezone.utc)
        if not watch_state.read_live_active(db):
            return {"disponible": False, "motif": "controle d'acces live inactif",
                    "note": "Pas de comptage en direct hors exploitation."}
        snap = watch_state.read_counter(db, watch_state.read_principal_id(db),
                                        max_age=watch_state.COUNTER_MAX_AGE,
                                        now_utc=now_utc)
        if not snap:
            return {"disponible": False, "motif": "aucun releve recent",
                    "note": "Collecteur probablement arrete : le signaler."}
        n = presents_etat.presents_principal(db, snap, now_utc=now_utc)
        return {"disponible": n is not None, "presents": n,
                "entrees_cumulees": snap.get("entries"),
                "releve": _paris(snap.get("timestamp"))}
    except Exception as exc:
        return _indisponible("presents", exc)


def t_wiki(db, args, ctx):
    """Procedures publiees du wiki (fiches reflexes)."""
    args = args or {}
    q = str(args.get("recherche") or "").strip()
    try:
        flt = {"status": "published"}
        if q:
            rx = {"$regex": re.escape(q[:80]), "$options": "i"}
            flt["$or"] = [{"code": rx}, {"titre": rx}, {"situation": rx},
                          {"conduite": rx}, {"souscas": rx}]
        docs = list(db["cockpit_wiki_procedures"].find(flt).sort("code", 1).limit(5))
        return {"disponible": True, "procedures": [
            {"code": d.get("code"), "titre": d.get("titre"),
             "situation": _court(d.get("situation"), 500),
             "acteurs": _court(d.get("acteurs"), 300),
             "conduite_a_tenir": [_court(x, 300) for x in (d.get("conduite") or [])][:12],
             "a_consigner": _court(d.get("consigner"), 300),
             "pieges": _court(d.get("pieges"), 300)}
            for d in docs]}
    except Exception as exc:
        return _indisponible("wiki", exc)


def t_situation(db, args, ctx):
    """Synthese en un appel : le premier outil a appeler pour << ou en est-on >>.

    Un petit modele local enchaine mal six outils ; un seul appel qui rend
    l'essentiel de chaque source lui evite des sauts et des oublis."""
    tr = t_trafic(db, {}, ctx)
    me = t_meteo(db, {}, ctx)
    al = t_alertes(db, {}, ctx)
    if (ctx or {}).get("sans_main_courante"):
        mc = {"disponible": False}
    else:
        try:
            mc = t_main_courante(db, {}, ctx)
        except Exception as exc:
            mc = _indisponible("main_courante", exc)
    tl = t_timeline(db, {"heures": 6}, ctx)
    ev = t_evenement(db, {}, ctx)
    pr = t_presents(db, {}, ctx)
    return {
        "disponible": True,
        "heure": datetime.now(TZ_PARIS).strftime("%d/%m %H:%M"),
        "evenement": ev.get("evenement_courant"),
        "contexte_evenements": ev.get("contexte_evenements"),
        "presents": pr.get("presents") if pr.get("disponible") else pr.get("motif", "indisponible"),
        "trafic": ({"verdict": tr.get("verdict"), "accidents": tr.get("accidents_en_zone"),
                    "axes_charges": [a for a in tr.get("axes", []) if a["etat"] not in ("fluide",)][:4]}
                   if tr.get("disponible") else "indisponible"),
        "meteo": ({"actuel": me.get("actuel"), "pluie": me.get("pluie"),
                   "consignes": me.get("consignes"), "contraintes": me.get("contraintes")}
                  if me.get("disponible") else "indisponible"),
        "alertes": ({"nombre": al.get("nombre"),
                     "dernieres": [a["titre"] for a in al.get("alertes", [])[:5]]}
                    if al.get("disponible") else "indisponible"),
        # Meme lecture que cockpit_main_courante sans parametre
        "main_courante": ("non consultable sur ce canal" if (ctx or {}).get("sans_main_courante")
                          else mc.get("compteurs") if mc.get("disponible") else "indisponible"),
        "fiches_en_cours_les_plus_urgentes": [
            {k: f.get(k) for k in ("numero", "ouverte", "categorie", "urgence", "zone", "texte")
             if f.get(k)} for f in (mc.get("fiches") or [])[:5]],
        "a_venir_6h": tl.get("vignettes", [])[:6] if tl.get("disponible") else "indisponible",
    }


# ---------------------------------------------------------------------------
# Registre (publie a la VM par GET /api/alfred-tools/manifest)
# ---------------------------------------------------------------------------

def t_lieux(db, args, ctx):
    import alfred_lieux
    return alfred_lieux.t_lieux(db, args, ctx)


def t_frequentation(db, args, ctx):
    import alfred_frequentation
    return alfred_frequentation.t_frequentation(db, args, ctx)


def t_main_courante(db, args, ctx):
    import alfred_main_courante
    return alfred_main_courante.t_main_courante(db, args, ctx)


TOOLS = {
    "cockpit_frequentation": {
        "fn": t_frequentation,
        "capacite": "la fréquentation : personnes présentes en direct, pics par jour et comparaison entre éditions, alignée sur le jour de course",
        "description": "Frequentation du site : personnes presentes EN DIRECT (meme calcul que "
                       "l'accueil et la TV, avec l'ecart a la meme heure l'an dernier), et "
                       "HISTORIQUE de toutes les editions d'un evenement : pic de presents par "
                       "jour et son heure, entrees, comparaison entre annees alignee sur le jour "
                       "de course. A appeler pour TOUTE question de presents, affluence, monde, "
                       "pic, frequentation ou comparaison d'annees. Recopier le champ resume sans "
                       "le reformuler ni le completer (reserves comprises).",
        "parameters": {"type": "object", "properties": {
            "evenement": {"type": "string", "description": "evenement tel que dit par l'operateur "
                                                           "(les camions, le Mans Classic, SBK...). "
                                                           "Omettre s'il n'en cite pas"},
            "annee": {"type": "string", "description": "annee ou expression de l'operateur TELLE "
                                                       "QUELLE : 2025, l'an dernier, il y a deux ans"},
            "comparer_avec": {"type": "string", "description": "si l'operateur veut comparer : "
                                                               "annees (2023, 2024), l'an dernier, "
                                                               "les 3 dernieres editions, toutes"},
            "jour": {"type": "string", "description": "jour vise : samedi, jour de course, "
                                                      "veille, J-1, YYYY-MM-DD, aujourd'hui"},
        }},
    },
    "cockpit_lieux": {
        "fn": t_lieux,
        "capacite": "les horaires et infos des lieux : portes, parkings, campings, tribunes, boutiques, hospitalités, services (centre médical, PC...), par public, et ce qui est ouvert maintenant",
        "description": "Horaires d'ouverture et informations de TOUS les lieux de l'evenement : "
                       "portes, parkings, campings, tribunes, boutiques, hospitalites, passerelles, "
                       "sanitaires, et services (ouverture du site au public, centre medical, help "
                       "desk, PC Orga, PC autorites, salle de presse). Plages continues deja "
                       "calculees par public (organisation = accredites, public, VIP), controle "
                       "d'acces (controle ou libre, par jour), capacites. Sans nom : etat de tous "
                       "les lieux a l'instant (ce qui est ouvert maintenant). A appeler pour TOUTE "
                       "question d'horaire, d'ouverture, de fermeture ou de controle d'un lieu, y "
                       "compris les questions de suite. Recopier le champ resume sans le "
                       "reformuler ni le completer.",
        "parameters": {"type": "object", "properties": {
            "nom": {"type": "string", "description": "nom du lieu tel que dit par l'operateur "
                                                     "(ex. porte nord, parking Chinetti)"},
            "type": {"type": "string", "enum": ["porte", "parking", "camping", "tribune",
                                               "boutique", "hospitalite", "passerelle",
                                               "sanitaire", "service"]},
            "public": {"type": "string",
                       "description": "le mot de l'operateur TEL QUEL (accredites, orga, staff, "
                                      "public, spectateurs, VIP...) : l'outil le traduit. Omettre "
                                      "si non precise (les trois publics sont alors donnes)"},
            "jour": {"type": "string", "description": "YYYY-MM-DD, aujourd'hui, demain ou un "
                                                      "jour de la semaine (samedi)"},
            "maintenant": {"type": "boolean", "description": "ouvert ou ferme a l'instant"},
            "evenement": {"type": "string", "description": "seulement si l'operateur le cite"},
            "annee": {"type": "string"},
        }},
    },
    "cockpit_situation": {
        "fn": t_situation,
        "capacite": "un point de situation général en une fois (fréquentation, trafic, météo, alertes, main courante, échéances)",
        "description": "Synthese de la situation en cours en UN appel : evenement, presents, "
                       "trafic, meteo, alertes, compteurs et dernieres fiches main courante, "
                       "echeances des 6 prochaines heures. A appeler en premier pour toute "
                       "question generale (ou en est-on, point de situation, briefing). Photo de "
                       "l'instant seulement : pour une periode passee (cette nuit, depuis 2 h) ou "
                       "toute question de fiches, appeler cockpit_main_courante avec `periode`.",
        "parameters": {"type": "object", "properties": {}},
    },
    "cockpit_main_courante": {
        "fn": t_main_courante,
        "capacite": "la main courante PC Organisation : point de situation des fiches en cours, "
                    "détail et chronologie d'une fiche, recherche par catégorie, urgence, mot ou "
                    "période (cette nuit, depuis 2 h, hier)",
        "description": "Main courante PC Organisation (fiches). SANS parametre : point de situation "
                       "(fiches en cours, les plus urgentes d'abord, compteurs du jour et de la "
                       "derniere heure). Avec `fiche` : detail et chronologie d'une fiche. Avec "
                       "`periode`, `categorie`, `urgence`, `texte` : fiches correspondantes. A "
                       "appeler pour TOUTE question sur les fiches, interventions, incidents, "
                       "urgences en cours, ce qui s'est passe (cette nuit, depuis 2 h, hier : "
                       "TOUJOURS passer `periode`), ce qui est en cours, << y a-t-il quelque chose "
                       "sur X >> (`texte`). Passer `evenement` des que l'operateur en cite un (les "
                       "24h camions). Un numero de fiche cite va dans `fiche`. Recopier le champ "
                       "resume sans le reformuler ni le completer ; ne jamais inventer de fiche. "
                       "Ne cree ni ne modifie aucune fiche.",
        "parameters": {"type": "object", "properties": {
            "fiche": {"type": "string", "description": "numero de fiche cite par l'operateur"},
            "periode": {"type": "string", "description": "expression TELLE QUELLE : cette nuit, "
                                                          "depuis 2h, derniere heure, hier, ce matin"},
            "categorie": {"type": "string", "description": "mot de l'operateur TEL QUEL : secours, "
                                                            "secu, surete, technique, flux, info..."},
            "urgence": {"type": "string", "description": "urgentes, detresse vitale, EU, UA, UR, "
                                                          "implique"},
            "texte": {"type": "string", "description": "mot cherche (lieu, objet, nom de porte...)"},
            "statut": {"type": "string", "description": "en cours (defaut), closes, toutes"},
            "evenement": {"type": "string", "description": "seulement si l'operateur le cite"},
            "annee": {"type": "string"},
        }},
    },
    # Fusionnes dans cockpit_main_courante : retires du manifeste, toujours
    # executables pour un wrapper qui les appellerait encore.
    "cockpit_main_courante_fiches": {
        "masque": True,
        "fn": t_main_courante_fiches,
        "description": "Liste des fiches de la main courante PC Organisation, les plus recentes "
                       "d'abord, filtrables.",
        "parameters": {"type": "object", "properties": {
            "statut": {"type": "string", "enum": ["en_cours", "closes", "toutes"],
                       "description": "defaut en_cours"},
            "categorie": {"type": "string",
                          "description": "secours, securite, technique, flux, information, "
                                         "fourriere, maincourante, surete"},
            "urgence": {"type": "string", "enum": ["EU", "UA", "UR", "IMP"]},
            "texte": {"type": "string", "description": "mot cherche dans le texte, la zone, "
                                                       "la nature ou la chronologie"},
            "depuis_heures": {"type": "number", "description": "fiches ouvertes depuis N heures"},
            "limite": {"type": "integer", "description": "1 a 25, defaut 10"},
        }},
    },
    "cockpit_main_courante_fiche": {
        "masque": True,
        "fn": t_main_courante_fiche,
        "description": "Detail d'une fiche main courante avec sa chronologie d'actions.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "id de la fiche"},
            "numero": {"type": "string", "description": "numero Prysm (sql_id)"},
        }},
    },
    "cockpit_main_courante_compteurs": {
        "masque": True,
        "fn": t_main_courante_compteurs,
        "description": "Compteurs main courante : fiches en cours, closes et creees aujourd'hui, "
                       "par categorie.",
        "parameters": {"type": "object", "properties": {}},
    },
    "cockpit_trafic": {
        "fn": t_trafic,
        "capacite": "le trafic routier autour du site (Waze) : verdict, accidents, temps par axe",
        "description": "Etat du trafic routier (Waze) : verdict global identique au mur "
                       "circulation, accidents en zone, temps et retard par axe.",
        "parameters": {"type": "object", "properties": {}},
    },
    "cockpit_meteo": {
        "fn": t_meteo,
        "capacite": "la météo du site : conditions actuelles, prochaine pluie, vigilance, consignes",
        "description": "Meteo du site identique au mur meteo : conditions actuelles, prochaine "
                       "pluie, vigilance, consignes et contraintes (vent, chaleur, orage, sol).",
        "parameters": {"type": "object", "properties": {}},
    },
    "cockpit_alertes": {
        "fn": t_alertes,
        "capacite": "les alertes actives de la centrale d'alerte Cockpit",
        "description": "Alertes actives de la centrale d'alerte Cockpit.",
        "parameters": {"type": "object", "properties": {}},
    },
    "cockpit_timeline": {
        "fn": t_timeline,
        "capacite": "les prochaines échéances de la timeline opérationnelle (ouvertures, fermetures, départs)",
        "description": "Prochaines echeances de la timeline operationnelle (ouvertures, "
                       "fermetures, departs...), factorisees.",
        "parameters": {"type": "object", "properties": {
            "heures": {"type": "integer", "description": "fenetre en heures, 1 a 48, defaut 12"},
        }},
    },
    # Fusionne dans cockpit_frequentation : retire du manifeste, toujours
    # executable pour un wrapper qui l'appellerait encore.
    "cockpit_presents": {
        "masque": True,
        "fn": t_presents,
        "description": "Nombre de personnes presentes sur site (controle d'acces live), "
                       "meme calcul que l'accueil Cockpit.",
        "parameters": {"type": "object", "properties": {}},
    },
    "cockpit_wiki_procedures": {
        "fn": t_wiki,
        "capacite": "les procédures et fiches réflexes publiées du wiki PC Organisation",
        "description": "Procedures et fiches reflexes publiees du wiki PC Organisation "
                       "(conduite a tenir, acteurs, pieges).",
        "parameters": {"type": "object", "properties": {
            "recherche": {"type": "string", "description": "mot cle ou code de procedure"},
        }},
    },
    "cockpit_evenement": {
        "fn": t_evenement,
        "capacite": "l'événement en cours, sa phase et sa description",
        "description": "Evenement(s) en cours et leur phase (montage, course, demontage, SAISON).",
        "parameters": {"type": "object", "properties": {}},
    },
}


# La main courante ne sort jamais par la vue complete sans jeton (chemin
# WhatsApp du wrapper) : un groupe WhatsApp n'a pas les droits d'un
# operateur. Bloque ICI aussi, pour tenir meme si le wrapper changeait.
OUTILS_MAIN_COURANTE = {"cockpit_main_courante", "cockpit_main_courante_fiches",
                        "cockpit_main_courante_fiche", "cockpit_main_courante_compteurs"}


def manifest(sans_main_courante=False):
    """Definitions au format tool-calling OpenAI/Ollama."""
    return [{"type": "function",
             "function": {"name": name, "description": t["description"],
                          "parameters": t["parameters"]}}
            for name, t in TOOLS.items() if not t.get("masque")
            and not (sans_main_courante and name in OUTILS_MAIN_COURANTE)]


def presentation():
    """Qui est Alfred et ce qu'il sait faire, pour les questions << qui es-tu,
    que sais-tu faire >>. Tiree du registre : un outil ajoute ou retire
    met la presentation a jour sans toucher au prompt de la VM."""
    sait = [t["capacite"] for t in TOOLS.values() if t.get("capacite") and not t.get("masque")]
    texte = ("Je suis Alfred, l'assistant du PC Organisation, intégré à Cockpit. Je lis les mêmes "
             "données que les écrans de Cockpit, en temps réel, et je peux vous donner :\n"
             + "\n".join("- " + s for s in sait)
             + "\nJe suis en lecture seule : je ne crée ni ne modifie aucune fiche et je n'envoie "
               "rien. Je ne donne ni immatriculations ni coordonnées personnelles. Je ne vois que "
               "les catégories de main courante de votre groupe. Si une donnée manque, je le dis "
               "au lieu de l'estimer. Pour signaler une réponse fausse : le pouce bas sous la "
               "réponse.")
    return {
        "nom": "Alfred",
        "role": "assistant du PC Organisation, intégré à Cockpit (circuits du Mans)",
        "sait_faire": sait,
        "ne_fait_pas": [
            "créer, modifier ou clore une fiche, envoyer un message ou agir sur un équipement",
            "donner des immatriculations (LAPI) ou des coordonnées personnelles",
            "voir les catégories de main courante hors du périmètre du groupe de l'opérateur",
            "estimer une donnée absente",
        ],
        "exemples": [
            "Fais-moi un point de situation.",
            "Qu'est-ce qui s'est passé cette nuit en main courante ?",
            "Combien de monde sur site ? Et l'an dernier à la même heure ?",
            "La porte Nord ouvre à quelle heure pour les accrédités ?",
            "Quelle météo pour les prochaines heures ?",
        ],
        "texte": texte,
        "consigne": "Pour << qui es-tu >>, << que sais-tu faire >>, << aide >> : repondre a "
                    "partir de `texte` sans appeler d'outil, sans promettre autre chose.",
    }


# Plafond d'un resultat (~1 500 tokens). Le wrapper tronquait a 8 000
# caracteres EN PLEIN JSON : le modele recevait un document invalide. On
# reduit ici, proprement, en gardant `resume` et les champs courts.
RESULT_MAX_CHARS = 5000


def _borner(result):
    import json as _json

    def taille(o):
        return len(_json.dumps(o, ensure_ascii=False, default=str))
    if not isinstance(result, dict) or taille(result) <= RESULT_MAX_CHARS:
        return result
    out = dict(result)
    # Retire d'abord les plus gros champs autres que resume, jusqu'a tenir
    for k in sorted((k for k in out if k != "resume"), key=lambda k: -taille(out[k])):
        if taille(out) <= RESULT_MAX_CHARS:
            break
        v = out[k]
        if isinstance(v, list) and len(v) > 1:
            while len(v) > 1 and taille(out) > RESULT_MAX_CHARS:
                v = v[: max(1, len(v) // 2)]
                out[k] = v
            out["tronque"] = True
        if taille(out) > RESULT_MAX_CHARS and k not in ("disponible",):
            out.pop(k, None)
            out["tronque"] = True
    if taille(out) > RESULT_MAX_CHARS and isinstance(out.get("resume"), str):
        out["resume"] = out["resume"][: RESULT_MAX_CHARS - 300] + " [...]"
        out["tronque"] = True
    return out


def call(db, name, args, ctx):
    """Execute un outil. (ok, resultat). Ne leve jamais."""
    t = TOOLS.get(name)
    if not t:
        return False, {"error": "outil_inconnu", "outils": sorted(TOOLS)}
    try:
        return True, _borner(t["fn"](db, args if isinstance(args, dict) else {}, ctx or {}))
    except Exception as exc:
        logger.exception("alfred_tools %s", name)
        return False, {"error": "erreur_outil", "detail": str(exc)[:200]}
