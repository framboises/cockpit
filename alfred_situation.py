"""Outil Alfred `cockpit_situation` : point de situation en un appel, et les
vues `alertes` et `evenement` (remplacent cockpit_alertes et
cockpit_evenement, masques mais encore executables).

Avant (09/10/2026) l'outil rendait les donnees brutes et le modele redigeait
le point lui-meme (long, a sa facon, parfois une ligne de trop). Comme les
autres outils, Cockpit redige ici un `resume` court a recopier.

- sans `sujet` : evenement, presents, trafic, meteo, alertes, main courante
  (compteurs + 3 fiches les plus urgentes), 6 prochaines heures ;
- sujet << alertes >> : alertes actives de la centrale, une par ligne ;
- sujet << evenement >> : evenement(s) actif(s), phase, dates, description.

Les alertes LAPI (definitions << anpr-* >>) ne sortent JAMAIS dans le chat,
et toute plaque d'un message est masquee. Lecture seule.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo("Europe/Paris")
FICHES_MAX = 3
ALERTES_MAX = 10

PHASES = {"public": "ouverte au public", "montage": "montage", "demontage": "démontage",
          "course": "course", "pre": "avant-ouverture"}


def sujet_demande(raw):
    from alfred_main_courante import _norm
    n = _norm(raw)
    if not n:
        return None
    if "alert" in n or "centrale" in n:
        return "alertes"
    if any(w in n for w in ("evenement", "epreuve", "phase", "edition", "event")):
        return "evenement"
    return None


def _hm(dt):
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    p, n = dt.astimezone(TZ_PARIS), datetime.now(TZ_PARIS)
    return p.strftime("%H:%M") if p.date() == n.date() else p.strftime("%d/%m %H:%M")


def _nombre(x):
    return ("%.1f" % x).replace(".", ",").replace(",0", "") if isinstance(x, float) else str(x)


# ---------------------------------------------------------------------------
# Alertes
# ---------------------------------------------------------------------------

def alertes_actives(db, ctx):
    """Alertes actives visibles dans le chat : jamais LAPI, plaques masquees."""
    import alfred_main_courante as AM
    now = datetime.now(timezone.utc)
    q = {"expiresAt": {"$gt": now}}
    slugs = (ctx or {}).get("alert_slugs")
    if slugs is not None:
        q["definition_slug"] = {"$in": list(slugs)}
    out = []
    for d in list(db["cockpit_active_alerts"].find(q).sort("triggeredAt", -1))[:50]:
        slug = str(d.get("definition_slug") or "")
        if slug.startswith("anpr"):
            continue                      # LAPI : exclue du chat
        out.append({"titre": AM.masquer(d.get("title") or slug or "Alerte"),
                    "message": AM._court(d.get("message"), 240),
                    "declenchee": _hm(d.get("triggeredAt")), "expire": _hm(d.get("expiresAt")),
                    "type": slug})
    return out


def vue_alertes(db, ctx):
    al = alertes_actives(db, ctx)
    heure = datetime.now(TZ_PARIS).strftime("%H:%M")
    if not al:
        return {"disponible": True, "vue": "alertes", "nombre": 0,
                "resume": "Aucune alerte active dans la centrale d'alerte (%s)." % heure}
    l = ["Alertes actives de la centrale (%s) : %d." % (heure, len(al))]
    for a in al[:ALERTES_MAX]:
        l.append("- %s%s (depuis %s%s)" % (a["titre"], " : " + a["message"] if a["message"] else "",
                                           a["declenchee"] or "?",
                                           ", jusqu'à %s" % a["expire"] if a["expire"] else ""))
    if len(al) > ALERTES_MAX:
        l.append("… et %d autres." % (len(al) - ALERTES_MAX))
    return {"disponible": True, "vue": "alertes", "nombre": len(al), "alertes": al[:ALERTES_MAX],
            "resume": "\n".join(l)}


# ---------------------------------------------------------------------------
# Evenement
# ---------------------------------------------------------------------------

def _fenetre(db, event, year):
    import event_courant
    for w in event_courant.windows(db):
        if w.get("event") == event and str(w.get("year")) == str(year):
            return w
    return None


def _jour(dt, fin=False):
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(dt))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    p = dt.astimezone(TZ_PARIS)
    if fin and p.hour == 0 and p.minute == 0:
        p -= timedelta(minutes=1)        # fin a minuit = la veille au soir
    return p.strftime("%d/%m/%Y")


def ligne_evenement(db, a):
    import event_courant
    nom = "%s %s" % (a["event"], a["year"])
    if a.get("kind") == "saison" or event_courant.is_saison(a["event"]):
        return "%s (main courante permanente du site, hors épreuve)" % nom
    bits = ["épreuve"]
    if a.get("phase"):
        bits.append("phase %s" % PHASES.get(a["phase"], a["phase"]))
    w = _fenetre(db, a["event"], a["year"])
    if w:
        d, f = _jour(w.get("start")), _jour(w.get("end"), fin=True)
        if d and f:
            bits.append("du %s au %s" % (d[:5], f))
        pub = sorted(w.get("public_days") or [])
        if pub:
            p1 = "/".join(pub[0].split("-")[::-1][:2])
            p2 = "/".join(pub[-1].split("-")[::-1][:2])
            if not (d and f and p1 == d[:5] and p2 == f[:5]):     # inutile si identique
                bits.append("public du %s au %s" % (p1, p2) if p1 != p2 else "public le %s" % p1)
        if w.get("race"):
            bits.append("course le %s" % _jour(w["race"]))
    return "%s (%s)" % (nom, ", ".join(bits))


def vue_evenement(db, ctx):
    import alfred_evenements
    import event_courant
    p = event_courant.payload(db)
    actifs = p.get("active") or []
    if not actifs:
        return {"disponible": True, "vue": "evenement", "resume": "Aucun événement actif."}
    l = ["Événement en cours : %s." % ligne_evenement(db, actifs[0])]
    for a in actifs[1:]:
        l.append("Aussi actif : %s." % ligne_evenement(db, a))
    if p.get("conflict"):
        l.append("Plusieurs épreuves sont actives en même temps%s." % (
            " (priorité choisie par un administrateur)" if p.get("priority_chosen") else ""))
    sel = ((ctx or {}).get("event"), (ctx or {}).get("year"))
    if sel[0] and sel[0] != actifs[0]["event"]:
        l.append("Événement sélectionné par l'opérateur : %s %s." % sel)
    contextes = []
    for a in actifs:
        c = alfred_evenements.contexte(db, a["event"], a["year"])
        if c:
            contextes.append(c)
            if c.get("description"):
                l.append("%s : %s" % (a["event"], c["description"].rstrip(".") + "."))
    return {"disponible": True, "vue": "evenement", "evenement_courant": p.get("current"),
            "evenements_actifs": actifs, "contexte_evenements": contextes or None,
            "resume": "\n".join(l)}


# ---------------------------------------------------------------------------
# Point de situation
# ---------------------------------------------------------------------------

def _ligne_trafic(tr):
    if not tr.get("disponible"):
        return "Trafic : indisponible."
    v = (tr.get("verdict") or "").lower() or "verdict non affirmable (alertes Waze périmées)"
    acc = tr.get("accidents_en_zone") or 0
    s = "Trafic : %s, %s." % (v, "aucun accident" if not acc else "%d accident%s" % (acc, "s" if acc > 1 else ""))
    charges = [a for a in tr.get("axes") or [] if a.get("etat") not in ("fluide", None)][:4]
    if charges:
        s += " Axes chargés : " + " ; ".join(
            "%s%s %s (%s)" % (a["axe"], " " + a["sens"] if a.get("sens") else "", a["etat"], a["retard"])
            for a in charges) + "."
    return s


def _ligne_meteo(me):
    if not me.get("disponible"):
        return "Météo : indisponible."
    a = me.get("actuel") or {}
    bits = []
    if a.get("temperature_c") is not None:
        bits.append("%s °C" % _nombre(round(a["temperature_c"])))
    if a.get("vent_moyen_kmh") is not None:
        bits.append("vent %s km/h%s" % (round(a["vent_moyen_kmh"]), " (rafales %s)" % round(a["rafale_kmh"])
                                         if a.get("rafale_kmh") is not None else ""))
    s = "Météo : " + (", ".join(bits) if bits else "conditions actuelles indisponibles") + "."
    pl = me.get("pluie") or {}
    if pl.get("attendue"):
        s += " Pluie attendue dans %s min%s." % (pl.get("dans_min"), " (jusqu'à %s mm/h)" % _nombre(round(
            pl["pic_mmh"], 1)) if pl.get("pic_mmh") is not None else "")
    vig = []
    for c in me.get("contraintes") or []:
        v = "%s (%s" % (c.get("libelle"), c.get("niveau"))
        if c.get("pic") is not None and c.get("unite") and c.get("unite") != "SWI":
            v += ", %s %s%s" % (_nombre(c["pic"]), c["unite"], " vers %s" % c["pic_heure"] if c.get("pic_heure") else "")
        v += ")"
        if c.get("consigne"):
            v += " : " + c["consigne"]
        vig.append(v)
    if vig:
        s += " Vigilance : " + " ; ".join(vig) + "."
    for c in me.get("consignes") or []:
        if c.get("texte"):
            s += " Consigne : %s." % c["texte"].rstrip(".")
    return s


MOTIFS_PRESENTS = {"controle d'acces live inactif": "comptage en direct inactif (hors exploitation)",
                   "aucun releve recent": "aucun relevé récent (collecteur probablement arrêté)"}


def _ligne_presents(pr):
    if pr.get("disponible") and pr.get("presents") is not None:
        return "Présents sur site : %s." % "{:,}".format(pr["presents"]).replace(",", " ")
    m = pr.get("motif") or "indisponibles"
    return "Présents sur site : %s." % MOTIFS_PRESENTS.get(m, m)


def _lignes_main_courante(mc):
    if not mc.get("disponible"):
        return ["Main courante : indisponible."]
    c = mc.get("compteurs") or {}
    cats = ", ".join("%s %d" % kv for kv in (c.get("par_categorie") or {}).items())
    urg = ", ".join("%s %d" % kv for kv in (c.get("par_urgence") or {}).items())
    s = "Main courante : %d fiche%s en cours" % (c.get("en_cours", 0), "s" if c.get("en_cours", 0) > 1 else "")
    if cats:
        s += " (%s)" % cats
    if urg:
        s += ", dont %s" % urg
    s += " ; aujourd'hui %d ouvertes, %d closes." % (c.get("ouvertes_aujourdhui", 0), c.get("closes_aujourdhui", 0))
    l = [s]
    fiches = (mc.get("fiches") or [])[:FICHES_MAX]
    if fiches:
        def court(t, n=90):
            t = " ".join(str(t or "").split())
            return t if len(t) <= n else t[: n - 1].rstrip() + "…"
        l.append("En tête de liste : " + " ; ".join(
            "n°%s %s, %s%s « %s »" % (f.get("numero") or "?", f.get("ouverte") or "", f.get("categorie") or "",
                                      ", %s" % f["urgence"] if f.get("urgence") else "", court(f.get("texte")))
            for f in fiches) + ".")
    return l


def _ligne_agenda(tl):
    if not tl.get("disponible"):
        return "À venir (6 h) : indisponible."
    vs = [v for v in tl.get("vignettes") or [] if not v.get("journee")][:5]
    if not vs:
        return "À venir (6 h) : rien de prévu à la timeline."
    return "À venir (6 h) : " + " ; ".join(
        "%s%s %s%s%s" % (v.get("debut") or "", "-" + v["fin"] if v.get("fin") else "", v.get("activity") or "",
                         ", " + v["place"] if v.get("place") else "", " (en cours)" if v.get("en_cours") else "")
        for v in vs) + "."


def t_situation(db, args, ctx):
    import alfred_tools as AT
    args, ctx = args or {}, ctx or {}
    sujet = sujet_demande(args.get("sujet"))
    if sujet == "alertes":
        try:
            return vue_alertes(db, ctx)
        except Exception as exc:
            return AT._indisponible("alertes", exc)
    if sujet == "evenement":
        try:
            return vue_evenement(db, ctx)
        except Exception as exc:
            return AT._indisponible("evenement", exc)

    tr = AT.t_trafic(db, {}, ctx)
    me = AT.t_meteo(db, {}, ctx)
    try:
        al = alertes_actives(db, ctx)
    except Exception:
        al = None
    if ctx.get("sans_main_courante"):
        mc = None
    else:
        try:
            mc = AT.t_main_courante(db, {}, ctx)
        except Exception as exc:
            mc = AT._indisponible("main_courante", exc)
    try:
        tl = AT.t_agenda(db, {"quand": "prochaines 6 heures"}, ctx)
    except Exception as exc:
        tl = AT._indisponible("agenda", exc)
    try:
        ev = vue_evenement(db, ctx)
        tete = ligne_evenement(db, (ev.get("evenements_actifs") or [{}])[0]) if ev.get("evenements_actifs") else None
    except Exception:
        ev, tete = {}, None
    pr = AT.t_presents(db, {}, ctx)

    heure = datetime.now(TZ_PARIS).strftime("%d/%m %H:%M")
    l = ["Point de situation, %s%s." % (heure, ", " + tete if tete else "")]
    l.append(_ligne_presents(pr))
    l.append(_ligne_trafic(tr))
    l.append(_ligne_meteo(me))
    if al is None:
        l.append("Alertes : indisponibles.")
    elif not al:
        l.append("Alertes : aucune active.")
    else:
        l.append("Alertes : %d active%s : %s." % (len(al), "s" if len(al) > 1 else "",
                                                 " ; ".join(a["titre"] for a in al[:5])))
    if mc is None:
        l.append("Main courante : non consultable sur ce canal.")
    else:
        l += _lignes_main_courante(mc)
    l.append(_ligne_agenda(tl))
    return {
        "disponible": True, "vue": "situation", "heure": heure,
        "evenement": ev.get("evenement_courant"), "contexte_evenements": ev.get("contexte_evenements"),
        "alertes": len(al) if al is not None else None,
        "resume": "\n".join(l),
    }
