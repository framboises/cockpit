"""Calcul de l'etat servi a la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import Flask : ce module se teste sans application.
"""

# Seuils WBGT en degres, replies sur l'echelle 0-3 de la montre. Ils viennent
# de SEUILS_WBGT (ISO 7243) dans meteo_thermique.py, dont le palier
# danger_extreme (33) est absorbe par le niveau 3 : au-dela de "suspendre le
# travail lourd", un cran de plus ne change aucune decision au poignet.
WBGT_DEFAULT_LEVELS = (25.0, 28.0, 30.0)

# Ce qui tient la reponse sous 2 Ko.
MAX_ALERTS = 5
LABEL_MAX = 24


def wbgt_level(wbgt_c, thresholds=None):
    """Replie un WBGT en degres sur l'echelle 0-3 de la montre."""
    if wbgt_c is None:
        return 0
    seuils = thresholds or WBGT_DEFAULT_LEVELS
    niveau = 0
    for seuil in seuils:
        if wbgt_c >= seuil:
            niveau += 1
    return min(niveau, 3)


def entry_rate(entries_now, ts_now, entries_before, ts_before):
    """Debit d'entrees en personnes/heure entre deux releves du compteur."""
    if entries_now is None or ts_now is None:
        return None
    if entries_before is None or ts_before is None:
        return None
    delta_s = (ts_now - ts_before).total_seconds()
    if delta_s <= 0:
        return None
    delta_e = entries_now - entries_before
    if delta_e < 0:
        # Remise a zero du compteur : mieux vaut rien qu'un debit absurde.
        return None
    return int(round(delta_e * 3600.0 / delta_s))


def select_alerts(active, config_alerts):
    """Filtre, note et tronque les alertes actives pour la montre.

    `cockpit_active_alerts` ne porte aucun champ de severite et le `priority`
    de `cockpit_alert_definitions` est un ordre d'affichage, pas une gravite
    (opening = 1, field_sos = 99). Le niveau vient donc exclusivement de la
    configuration, qui sert du meme coup de filtre : un slug absent ne part
    pas a la montre.
    """
    par_slug = {}
    for regle in config_alerts or []:
        slug = regle.get("slug")
        if slug:
            par_slug[slug] = regle

    sortie = []
    for doc in active or []:
        regle = par_slug.get(doc.get("definition_slug"))
        if regle is None:
            continue
        libelle = regle.get("label") or doc.get("title") or ""
        sortie.append({
            "l": int(regle.get("level", 1)),
            "m": libelle[:LABEL_MAX],
        })

    sortie.sort(key=lambda a: -a["l"])
    return sortie[:MAX_ALERTS]


def event_label(short, year):
    """Libelle court de l'evenement rapporte, du genre '24HM 26'."""
    if not short or year is None:
        return None
    return "%s %02d" % (short, int(year) % 100)


def _safe_int(value):
    """Convertit en entier, ou None si la valeur n'est pas exploitable.

    Les annees arrivent tantot en int, tantot en chaine selon l'emetteur, et
    parfois en valeur libre saisie a la main. Un seul point de conversion evite
    que les deux chemins de resolve_event ne divergent.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_event(config, counter_doc):
    """Retourne (event, year) : epingle si demande, sinon derive du compteur.

    En mode auto, on lit le `requested_event` / `year` du dernier releve : les
    chiffres et les alertes viennent alors forcement du meme evenement. Le doc
    global du live-controle n'est pas une source : il ne porte aucune annee et
    derive en pratique.
    """
    config = config or {}
    if config.get("event_mode") == "pinned":
        return config.get("event"), _safe_int(config.get("year"))

    if not counter_doc:
        return None, None
    return counter_doc.get("requested_event"), _safe_int(counter_doc.get("year"))
