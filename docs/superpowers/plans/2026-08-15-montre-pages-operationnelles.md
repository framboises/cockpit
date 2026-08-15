# Quatre pages opérationnelles sur la montre — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter quatre pages — main courante, trafic, météo, fréquentation — à l'app Connect IQ `garmin/cockpit-watch/`, alimentées par une seule requête `/api/v1/watch/state` enrichie.

**Architecture:** Deux extractions préalables sortent le calcul du trafic et de la météo des modules Flask, pour que le mur du PC et la montre partagent **une seule implémentation** et ne puissent pas diverger. Un nouveau module `watch_pages.py` condense les quatre sources en blocs compacts, injecté dans `watch_state.build_state` comme l'est déjà `watch_peaks`. Côté montre, quatre vues et un menu de saut, avec un cache `Storage` coupé en deux pour ne pas alourdir la glance.

**Tech Stack:** Python 3.10+, Flask, pymongo, pytest · Monkey C, Connect IQ SDK 9.2.0, Run No Evil

**Spec:** `docs/superpowers/specs/2026-08-15-montre-pages-operationnelles-design.md`

## Global Constraints

- **Aucun guillemet typographique** dans le code — JS, CSS, Python et Monkey C. Uniquement `'` et `"` droits. Les curly quotes provoquent des SyntaxError silencieuses.
- **`db` toujours passé en argument** dans les modules de calcul. Jamais de nom de base codé en dur (`titan_dev` / `titan` vient de `from app import db`).
- **Aucun import de `app`, `meteo` ou `traffic` depuis `watch_pages.py`** : ces trois modules importent Flask et `app`, ce qui créerait un cycle. Vérifié : `import meteo` déclenche l'auth de `app`.
- **Datetimes naïfs contenant de l'UTC** côté `data_access` : `pymongo` n'est pas `tz_aware`. Tout epoch publié se calcule par `dt.replace(tzinfo=timezone.utc).timestamp()`, jamais `.timestamp()` nu.
- **`null` ne se dessine jamais comme zéro.** Trois états distincts : valeur, bloc absent (tirets + nom du bloc), mode `past` (« hors événement »).
- **Chaque bloc du payload dans son propre `try`** : une source en panne rend son bloc `null`, jamais un 500.
- **Chaque garde doit avoir un test qui tombe quand on la retire.** Vérification par sabotage obligatoire avant de déclarer une tâche finie.
- **Mise en page mesurée, jamais estimée** : sonde `Graphics.createBufferedBitmap` + `getTextWidthInPixels` + `getFontHeight`. Sur un cadran rond, le **sommet** contraint un bloc de la moitié haute, la **base** un bloc de la moitié basse.
- Budget payload : **2 Ko**, mesuré par un test.
- Budgets mémoire montre : app 786 432 o, glance 65 536 o, service de fond 65 536 o. Les quatre pages sont **exclusivement** dans l'espace app.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `trafic_etat.py` *(créé)* | agrégation par terrain, géofence, verdict global — sans Flask |
| `meteo_etat.py` *(créé)* | état complet du mur météo — sans Flask |
| `traffic.py` *(modifié)* | ses routes consomment `trafic_etat` |
| `meteo.py` *(modifié)* | `mur()` consomme `meteo_etat` |
| `watch_pages.py` *(créé)* | les quatre constructeurs de blocs |
| `watch_state.py` *(modifié)* | champs `p` et `mr`, injection de `pages` |
| `watch_api.py` *(modifié)* | passe `pages=watch_pages` |
| `tests/test_trafic_etat.py`, `test_meteo_etat.py`, `test_watch_pages.py` *(créés)* | |
| `source/Pages.mc` *(créé)* | accès typés aux quatre blocs, comme `State.mc` |
| `source/MainCouranteView.mc`, `TraficView.mc`, `MeteoView.mc`, `FrequentationView.mc` *(créés)* | |
| `source/SautMenu.mc` *(créé)* | menu de saut ouvert par MENU |
| `resources/drawables/mc_*.svg` *(créés)* | quatre icônes de catégorie |

---

## Task 1 : `trafic_etat.py` — extraction sans changement de comportement

**Files:**
- Create: `trafic_etat.py`
- Create: `tests/test_trafic_etat.py`
- Modify: `traffic.py` (routes `/trafic/waiting_data_structured` et `/alerts`)

**Interfaces:**
- Produces : `parse_route_name(name) -> (direction, terrain, tag, variant)`, `classify_congestion(current, historic) -> (status, severity)`, `agreger_terrains(routes) -> list[dict]`, `haversine_km(lat1, lon1, lat2, lon2) -> float`, `alerte_en_zone(alerte) -> bool`, `compter_alertes(alertes) -> dict`, `verdict_global(counts, pire_severite) -> int`, `lire_routes(db) -> list`, `lire_alertes(db) -> list`

- [ ] **Étape 1 : capturer la référence avant de toucher à quoi que ce soit**

Le contrat de `/trafic/waiting_data_structured` ne doit pas bouger d'un octet. On le fige d'abord.

```bash
cd /Users/ludovic/Dropbox/ACO/TITAN/cockpit
python3 - <<'EOF' > /tmp/trafic_avant.json
import json
from flask import Flask
import traffic
app = Flask(__name__)
app.register_blueprint(traffic.traffic_bp)
c = app.test_client()
print(json.dumps(c.get("/trafic/waiting_data_structured").get_json(), sort_keys=True))
EOF
wc -c /tmp/trafic_avant.json
```

Si la route est protégée par une auth et renvoie 302, capturer à la place la sortie de la fonction d'agrégation en la copiant temporairement dans un script — l'important est d'avoir une référence, pas la forme qu'elle prend.

- [ ] **Étape 2 : écrire le test de la géofence, qui n'existe nulle part aujourd'hui**

```python
# tests/test_trafic_etat.py
import trafic_etat


class TestGeofence:
    def test_alerte_au_centre_est_en_zone(self):
        alerte = {"location": {"y": trafic_etat.ZONE_CENTER_LAT,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        assert trafic_etat.alerte_en_zone(alerte) is True

    def test_alerte_a_dix_km_est_hors_zone(self):
        # 0.09 degre de latitude vaut environ 10 km.
        alerte = {"location": {"y": trafic_etat.ZONE_CENTER_LAT + 0.09,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        assert trafic_etat.alerte_en_zone(alerte) is False

    def test_alerte_sans_position_est_hors_zone(self):
        # Ne jamais compter ce qu'on ne sait pas placer : une alerte sans
        # coordonnees gonflerait le verdict sans qu'on puisse la situer.
        assert trafic_etat.alerte_en_zone({}) is False
        assert trafic_etat.alerte_en_zone({"location": {}}) is False
```

- [ ] **Étape 3 : lancer ces tests, vérifier qu'ils échouent**

Run: `python3 -m pytest tests/test_trafic_etat.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'trafic_etat'`

- [ ] **Étape 4 : écrire `trafic_etat.py`**

`parse_route_name`, `classify_congestion` et le corps de l'agrégation sont **déplacés depuis `traffic.py` sans modification de logique** (`traffic.py` lignes ~276 à ~400). Le reste est nouveau.

```python
"""Etat du trafic : agregation par terrain, geofence, verdict global.

Ce module ne connait pas Flask. traffic.py porte un blueprint et importe app :
ni watch_pages ni aucun module de calcul ne peut l'importer sans cycle -- ce
qui obligerait a reecrire les seuils ailleurs, et donc a les faire diverger.
Le calcul vit donc ici, et traffic.py comme watch_pages le consomment.
"""

import logging
import math
import re

logger = logging.getLogger(__name__)

# Geofence du mur trafic (circulation.html:388). Sans lui, on compterait des
# alertes du flux Waze situees hors du perimetre du circuit.
ZONE_CENTER_LAT = 47.93827259819777
ZONE_CENTER_LON = 0.2229518934089374
ZONE_RADIUS_KM = 3.5

# Seuls ces types pilotent l'affichage du mur ; les autres sont ignores.
TYPES_COMPTES = ("ACCIDENT", "JAM", "HAZARD")

TAG_RE = re.compile(r'^\s*(#([IOP])(\d+)?#|##)\s*(.+?)\s*$')
SECURITY_RE = re.compile(r'^\s*\*\*\s*(.+?)\s*$')


def parse_route_name(name):
    """(direction, terrain, tag, variant). Deplace depuis traffic.py."""
    m = TAG_RE.match(name or "")
    if m:
        io = m.group(2)
        num = m.group(3)
        terrain = m.group(4).strip()
        if io == 'I':
            return "in", terrain, "I", None
        if io == 'O':
            return "out", terrain, "O", None
        if io == 'P':
            return None, terrain, "P", num
        return None, terrain, "neutral", None
    ms = SECURITY_RE.match(name or "")
    if ms:
        return None, ms.group(1).strip(), "security", None
    return None, (name or "").strip(), "free", None


def classify_congestion(current_time, historic_time):
    """(status, severite 0-4). Deplace depuis traffic.py, logique inchangee.

    ATTENTION, defaut connu et NON corrige ici : la branche de repli (pas de
    temps historique) compare des SECONDES a des seuils qui se lisent comme
    des minutes, donc tout trajet de plus d'une minute y sort << bouchon >>.
    Le chemin nominal passe par le ratio, ou les unites s'annulent, et il est
    juste. Corriger ce repli changerait l'affichage du mur : hors perimetre.
    """
    if not historic_time or historic_time <= 0:
        t = current_time or 0
        if t < 15:
            return ("normal", 1)
        if t < 30:
            return ("charge", 2)
        if t < 60:
            return ("sature", 3)
        return ("bouchon", 4)

    ratio = (current_time or 0) / float(historic_time)
    if ratio < 0.9:
        return ("plus fluide", 0)
    if ratio < 1.2:
        return ("normal", 1)
    if ratio < 1.6:
        return ("charge", 2)
    if ratio < 2.5:
        return ("sature", 3)
    return ("bouchon", 4)


def agreger_terrains(routes):
    """Agrege les itineraires surveilles par (terrain, direction).

    Deplace depuis /trafic/waiting_data_structured. `currentTime` est en
    SECONDES (verifie sur waze_trafic : time=208 pour un trajet Heronniere).
    """
    agg = {}
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        nom = route.get("name", "")
        if not (nom.startswith("##") or nom.startswith("#I")
                or nom.startswith("#O")):
            continue
        direction, terrain, _tag, _variant = parse_route_name(nom)
        cur = int(route.get("time", 0) or 0)
        hist = int(route.get("historicTime", 0) or 0)
        cle = (terrain, direction)
        if cle not in agg:
            agg[cle] = {"terrain": terrain, "direction": direction,
                        "sumCurrent": 0, "sumHistoric": 0, "routesCount": 0}
        agg[cle]["sumCurrent"] += max(0, cur)
        agg[cle]["sumHistoric"] += max(0, hist)
        agg[cle]["routesCount"] += 1

    terrains = []
    for rec in agg.values():
        sum_cur = rec["sumCurrent"]
        sum_hist = rec["sumHistoric"]
        ratio = (sum_cur / sum_hist) if sum_hist > 0 else None
        status, severite = classify_congestion(sum_cur, sum_hist)
        terrains.append({
            "terrain": rec["terrain"],
            "direction": rec["direction"],
            "currentTime": sum_cur,
            "historicTime": sum_hist,
            "ratio": round(ratio, 2) if ratio is not None else None,
            "deltaSeconds": max(0, sum_cur - sum_hist) if sum_hist > 0 else None,
            "deltaPercent": round((ratio - 1) * 100) if ratio is not None else None,
            "status": status,
            "severity": severite,
            "routesCount": rec["routesCount"],
        })
    terrains.sort(key=lambda t: (-1 if t["ratio"] is None else t["ratio"]),
                  reverse=True)
    return terrains


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def alerte_en_zone(alerte):
    """L'alerte tombe-t-elle dans le cercle de 3,5 km autour du circuit ?

    Une alerte sans coordonnees est HORS zone : ne jamais compter ce qu'on ne
    sait pas placer, sinon le verdict monte sans qu'on puisse dire pourquoi.
    """
    loc = (alerte or {}).get("location") or {}
    lat = loc.get("y")
    lon = loc.get("x")
    if lat is None or lon is None:
        return False
    try:
        return haversine_km(ZONE_CENTER_LAT, ZONE_CENTER_LON,
                            float(lat), float(lon)) <= ZONE_RADIUS_KM
    except (TypeError, ValueError):
        return False


def compter_alertes(alertes):
    """Comptes par type, geofences. Rend aussi le total des types comptes."""
    comptes = {t: 0 for t in TYPES_COMPTES}
    for alerte in alertes or []:
        if not alerte_en_zone(alerte):
            continue
        type_ = str((alerte or {}).get("type") or "").upper()
        if type_ in comptes:
            comptes[type_] += 1
    comptes["total"] = sum(comptes[t] for t in TYPES_COMPTES)
    return comptes


def verdict_global(comptes, pire_severite):
    """Verdict 0-3 du mur trafic. Regle de circulation.html:728, a l'identique.

    LES BOUCHONS ET DANGERS WAZE NE PILOTENT PAS LE VERDICT. Seuls comptent
    les axes surveilles et les accidents en zone : un bouchon Waze peut se
    trouver n'importe ou dans le cercle, hors des itineraires suivis, et
    faisait diverger le verdict du panneau << Axes >>. Toute tentative de les
    reintroduire ici ferait diverger le poignet du mur.
    """
    accidents = (comptes or {}).get("ACCIDENT", 0)
    pire = pire_severite or 0
    if accidents > 0 or pire >= 4:
        return 3
    if pire == 3:
        return 2
    if pire == 2:
        return 1
    return 0


def lire_routes(db):
    """Itineraires du dernier releve Waze en base."""
    doc = db["waze_trafic"].find_one(sort=[("fetched_at", -1)]) or {}
    return ((doc.get("data") or {}).get("routes")) or []


def lire_alertes(db):
    """Alertes Waze du dernier releve, non filtrees."""
    doc = db["waze_alerts"].find_one(sort=[("fetched_at", -1)]) or {}
    return doc.get("data") or []


def fraicheur(db):
    """Horodatage du dernier releve Waze, naif UTC, ou None."""
    doc = db["waze_trafic"].find_one(sort=[("fetched_at", -1)]) or {}
    return doc.get("fetched_at")
```

- [ ] **Étape 5 : relancer les tests de géofence**

Run: `python3 -m pytest tests/test_trafic_etat.py -q`
Expected: PASS (3 tests)

- [ ] **Étape 6 : ajouter les tests d'agrégation et de verdict**

```python
class TestVerdictGlobal:
    def test_accident_en_zone_passe_en_critique(self):
        assert trafic_etat.verdict_global({"ACCIDENT": 1}, 0) == 3

    def test_axe_en_bouchon_passe_en_critique(self):
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 4) == 3

    def test_paliers_intermediaires(self):
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 3) == 2
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 2) == 1
        assert trafic_etat.verdict_global({"ACCIDENT": 0}, 1) == 0

    def test_bouchons_waze_ne_pilotent_pas_le_verdict(self):
        # LA regle a tenir. Trente bouchons et dangers Waze dans le cercle ne
        # font PAS monter le verdict : ils peuvent tous etre hors des
        # itineraires surveilles. Seuls les axes et les accidents comptent.
        comptes = {"ACCIDENT": 0, "JAM": 30, "HAZARD": 12}
        assert trafic_etat.verdict_global(comptes, 1) == 0


class TestAgregerTerrains:
    def test_fusionne_les_troncons_du_meme_terrain(self):
        routes = [
            {"name": "#I# Ouest", "time": 120, "historicTime": 100},
            {"name": "#I2# Ouest", "time": 60, "historicTime": 50},
        ]
        out = trafic_etat.agreger_terrains(routes)
        assert len(out) == 1
        assert out[0]["currentTime"] == 180
        assert out[0]["historicTime"] == 150

    def test_ignore_les_itineraires_non_balises(self):
        routes = [{"name": "Fresne -> Leroy Merlin", "time": 173,
                   "historicTime": 162}]
        assert trafic_etat.agreger_terrains(routes) == []

    def test_tri_par_ratio_decroissant(self):
        routes = [
            {"name": "#I# Calme", "time": 100, "historicTime": 100},
            {"name": "#I# Bouche", "time": 300, "historicTime": 100},
        ]
        out = trafic_etat.agreger_terrains(routes)
        assert out[0]["terrain"] == "Bouche"


class TestCompterAlertes:
    def test_ne_compte_que_ce_qui_est_en_zone(self):
        alertes = [
            {"type": "ACCIDENT", "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                              "x": trafic_etat.ZONE_CENTER_LON}},
            {"type": "ACCIDENT", "location": {"y": trafic_etat.ZONE_CENTER_LAT + 0.5,
                                              "x": trafic_etat.ZONE_CENTER_LON}},
        ]
        assert trafic_etat.compter_alertes(alertes)["ACCIDENT"] == 1

    def test_type_inconnu_ignore(self):
        alertes = [{"type": "ROAD_CLOSED",
                    "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                                 "x": trafic_etat.ZONE_CENTER_LON}}]
        comptes = trafic_etat.compter_alertes(alertes)
        assert comptes["total"] == 0
```

- [ ] **Étape 7 : lancer, vérifier que tout passe**

Run: `python3 -m pytest tests/test_trafic_etat.py -q`
Expected: PASS (11 tests)

- [ ] **Étape 8 : brancher `traffic.py` sur le module extrait**

Dans `traffic.py`, remplacer les définitions locales de `parse_route_name` et `classify_congestion` par un import, et remplacer le corps de l'agrégation de `/trafic/waiting_data_structured` par un appel à `agreger_terrains`. La route devient :

```python
from trafic_etat import (agreger_terrains, classify_congestion,   # noqa: F401
                         parse_route_name)

@traffic_bp.route('/trafic/waiting_data_structured')
def get_trafic_data_parking_structured():
    try:
        trafic_data, cache_status = _get_waze_trafic_payload()
        return _jsonify_with_cache({
            "terrains": agreger_terrains(trafic_data.get("routes")),
            "updateTime": trafic_data.get("updateTime")
        }, cache_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

`classify_congestion` et `parse_route_name` restent importés même si la route ne les appelle plus directement : d'autres routes du fichier les utilisent.

- [ ] **Étape 9 : vérifier que le contrat n'a pas bougé**

Rejouer la capture de l'étape 1 dans `/tmp/trafic_apres.json` puis :

```bash
diff <(python3 -m json.tool /tmp/trafic_avant.json) \
     <(python3 -m json.tool /tmp/trafic_apres.json) && echo "IDENTIQUE"
```

Expected: `IDENTIQUE`. Toute différence est un défaut d'extraction, pas une amélioration — corriger avant de continuer.

- [ ] **Étape 10 : sabotage**

Retirer le filtre `alerte_en_zone` de `compter_alertes` : `test_ne_compte_que_ce_qui_est_en_zone` doit tomber. Remplacer la condition `accidents > 0` de `verdict_global` par une condition sur `JAM` : `test_bouchons_waze_ne_pilotent_pas_le_verdict` doit tomber. Restaurer.

- [ ] **Étape 11 : commit**

```bash
git add trafic_etat.py tests/test_trafic_etat.py traffic.py
git commit -m "refactor(trafic): extraire le calcul dans un module sans Flask"
```

---

## Task 2 : `meteo_etat.py` — extraction sans changement de comportement

**Files:**
- Create: `meteo_etat.py`
- Create: `tests/test_meteo_etat.py`
- Modify: `meteo.py` (route `mur()` et les helpers privés qu'elle utilise)

**Interfaces:**
- Consumes : rien des tâches précédentes
- Produces : `etat_mur(db, maintenant, config) -> dict` — le payload complet du mur, à l'identique de ce que rend `/api/meteo/mur` aujourd'hui

- [ ] **Étape 1 : capturer la référence**

```bash
cd /Users/ludovic/Dropbox/ACO/TITAN/cockpit
python3 - <<'EOF' > /tmp/meteo_avant.json
import json
from datetime import datetime
from pymongo import MongoClient
import meteo
db = MongoClient("mongodb://localhost:27017/")["titan_dev"]
# Appel direct du corps de la route, hors contexte Flask : la route est
# protegee par role_required et renverrait 302.
with __import__("flask").Flask(__name__).test_request_context():
    rep = meteo.mur()
print(json.dumps(rep.get_json() if hasattr(rep, "get_json") else rep,
                 sort_keys=True, default=str))
EOF
wc -c /tmp/meteo_avant.json
```

Si `mur()` reste inatteignable hors contexte applicatif, capturer à la place le résultat de chaque helper (`_contraintes`, `_verdict`, `_fraicheur_mur`) sur la base réelle — la référence doit exister avant l'extraction, sous une forme ou une autre.

- [ ] **Étape 2 : créer `meteo_etat.py` par déplacement**

Déplacer **sans modifier la logique** : le corps de `mur()` (`meteo.py:556` à ~744) et les helpers qu'il appelle — `_pire`, `_premiere_heure`, `_contrainte_vent`, `_contrainte_chaleur`, `_contrainte_orage`, `_contrainte_sol`, `_contraintes`, `_fraicheur_mur`, `_verdict`, `_instant`.

Deux seules substitutions :
- `_db()` devient le paramètre `db`
- `_config_meteo()` devient le paramètre `config`

```python
"""Etat complet du mur meteo, sans Flask.

meteo.py porte un blueprint et importe app : watch_pages ne peut pas
l'importer sans cycle. Or la montre doit repeter EXACTEMENT ce que le mur
decide -- les seuils de rafale, de WBGT et d'orage n'ont pas a exister en
deux endroits. Le calcul vit donc ici, et meteo.mur() comme
watch_pages.build_meteo le consomment.
"""

from datetime import datetime, timedelta, timezone

# ... helpers deplaces depuis meteo.py, inchanges ...


def etat_mur(db, maintenant, config):
    """Tout ce qu'affiche le mur du PC Organisation, en un appel.

    Corps deplace verbatim depuis meteo.mur(). Rend le meme dictionnaire :
    maintenant, actuel, prochaines, prochaine_pluie, consignes, contraintes,
    vigilance, radar, sol, fraicheur, verdict.
    """
    # ... corps deplace ...
```

- [ ] **Étape 3 : réduire `mur()` à un enrobage**

```python
@meteo_bp.route("/mur", methods=["GET"])
@role_required("user")
def mur():
    """Tout ce qu'affiche le mur du PC Organisation, en une requete.

    Le calcul vit dans meteo_etat, partage avec la montre : les seuils ne
    doivent exister qu'a un seul endroit.
    """
    import meteo_etat
    return jsonify(meteo_etat.etat_mur(_db(), datetime.now(), _config_meteo()))
```

- [ ] **Étape 4 : vérifier que le contrat n'a pas bougé**

Rejouer la capture dans `/tmp/meteo_apres.json` puis `diff`. Expected: identique. Toute différence est un défaut d'extraction.

- [ ] **Étape 5 : tests sur les parties qui comptent pour la montre**

```python
# tests/test_meteo_etat.py
from conftest import FakeDb

import meteo_etat


class TestEtatMur:
    def test_base_vide_ne_leve_pas(self):
        # La montre doit pouvoir demander la meteo meme quand aucune source
        # n'a encore ecrit : un bloc a None est acceptable, une exception non.
        etat = meteo_etat.etat_mur(FakeDb(), datetime(2026, 8, 15, 12, 0), {})
        assert isinstance(etat, dict)
        assert "actuel" in etat
```

Compléter avec un test par consigne (`vent`, `chaleur`, `orage`) sur des données forgées, en reprenant les seuils tels qu'ils sont dans le code déplacé — le but est de **verrouiller le comportement existant**, pas de le juger.

- [ ] **Étape 6 : lancer la suite complète**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, aucun test existant cassé.

- [ ] **Étape 7 : commit**

```bash
git add meteo_etat.py tests/test_meteo_etat.py meteo.py
git commit -m "refactor(meteo): extraire l'etat du mur dans un module sans Flask"
```

---

## Task 3 : `watch_pages.build_main_courante`

**Files:**
- Create: `watch_pages.py`
- Create: `tests/test_watch_pages.py`

**Interfaces:**
- Produces : `build_main_courante(db, event, year, now_utc) -> dict | None`

- [ ] **Étape 1 : écrire le test d'abord**

```python
# tests/test_watch_pages.py
from datetime import datetime, timedelta, timezone

from conftest import FakeDb

import watch_pages

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _fiche(categorie, close):
    return {"event": "24H MOTOS", "year": 2026, "category": categorie,
            "status_code": 10 if close else 2}


class TestMainCourante:
    def _db(self):
        return FakeDb(pcorg=[
            _fiche("PCO.Secours", False), _fiche("PCO.Secours", False),
            _fiche("PCO.Secours", True),
            _fiche("PCO.Technique", False),
            _fiche("PCO.Flux", True),
            _fiche("PCO.Information", False),
            _fiche("PCO.Fourriere", True),
        ])

    def test_paires_en_cours_terminees(self):
        bloc = watch_pages.build_main_courante(self._db(), "24H MOTOS", 2026, NOW)
        assert bloc["s"] == [2, 1]     # secours
        assert bloc["sc"] == [0, 0]    # securite
        assert bloc["tq"] == [1, 0]    # technique
        assert bloc["f"] == [0, 1]     # flux

    def test_les_trois_autres_categories_sont_repliees(self):
        # Information, MainCourante et Fourriere ne sont pas jetees : les
        # taire ferait disparaitre des fiches reelles de l'ecran.
        bloc = watch_pages.build_main_courante(self._db(), "24H MOTOS", 2026, NOW)
        assert bloc["o"] == [1, 1]

    def test_autre_evenement_ignore(self):
        db = FakeDb(pcorg=[
            {"event": "GPF", "year": 2026, "category": "PCO.Secours",
             "status_code": 2},
        ])
        bloc = watch_pages.build_main_courante(db, "24H MOTOS", 2026, NOW)
        assert bloc["s"] == [0, 0]

    def test_sans_evenement_rend_none(self):
        assert watch_pages.build_main_courante(FakeDb(), None, None, NOW) is None
```

- [ ] **Étape 2 : lancer, vérifier l'échec**

Run: `python3 -m pytest tests/test_watch_pages.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'watch_pages'`

- [ ] **Étape 3 : implémenter**

```python
"""Blocs des quatre pages operationnelles de la montre.

Fonctions pures et lectures Mongo, `db` toujours passe en argument. Aucun
import de Flask, ni de app, meteo ou traffic : ces trois modules importent
app, et un import en retour ferait un cycle. Le calcul du trafic et de la
meteo vit dans trafic_etat et meteo_etat, sans Flask, precisement pour cela.

Chaque constructeur rend un dictionnaire compact ou None. Il ne leve jamais :
l'appelant met le bloc a null et les autres pages restent servies.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Les quatre categories nommees sur la page, et leur cle dans le payload.
CATEGORIES_NOMMEES = (
    ("s", "PCO.Secours"),
    ("sc", "PCO.Securite"),
    ("tq", "PCO.Technique"),
    ("f", "PCO.Flux"),
)

# Repliees sur la ligne << + N (M) >>. Les compter a zero serait faux.
CATEGORIES_AUTRES = ("PCO.Information", "PCO.MainCourante", "PCO.Fourriere")

# Une fiche est close quand status_code vaut 10 (app.py:4808).
STATUT_CLOS = 10


def _epoch(moment):
    """Epoch UTC d'un datetime naif-UTC, convention pymongo."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def build_main_courante(db, event, year, now_utc=None):
    """Compteurs [en cours, terminees] par categorie. None si pas d'evenement."""
    if not event or year is None:
        return None
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    pipeline = [
        {"$match": {"event": event, "year": int(year),
                    "category": {"$regex": "^PCO"}}},
        {"$group": {"_id": {"cat": "$category",
                            "clos": {"$eq": ["$status_code", STATUT_CLOS]}},
                    "n": {"$sum": 1}}},
    ]
    comptes = {}
    for ligne in db["pcorg"].aggregate(pipeline):
        cat = (ligne["_id"] or {}).get("cat") or ""
        clos = bool((ligne["_id"] or {}).get("clos"))
        seau = comptes.setdefault(cat, [0, 0])
        seau[1 if clos else 0] = int(ligne.get("n", 0))

    bloc = {"t": _epoch(now_utc)}
    for cle, categorie in CATEGORIES_NOMMEES:
        bloc[cle] = list(comptes.get(categorie, [0, 0]))
    autres = [0, 0]
    for categorie in CATEGORIES_AUTRES:
        paire = comptes.get(categorie, [0, 0])
        autres[0] += paire[0]
        autres[1] += paire[1]
    bloc["o"] = autres
    return bloc
```

- [ ] **Étape 4 : ajouter `aggregate` au double Mongo**

`tests/conftest.py` n'implémente pas `aggregate`. Ajouter à `FakeCollection` le strict nécessaire pour ce pipeline — `$match` puis `$group` sur une clé composite avec `$sum: 1` — et **lever `NotImplementedError` sur tout étage non reconnu**, comme le fait déjà `_match` pour les opérateurs inconnus. Un double qui ignore un étage rendrait le test tautologique.

- [ ] **Étape 5 : lancer**

Run: `python3 -m pytest tests/test_watch_pages.py -q`
Expected: PASS (4 tests)

- [ ] **Étape 6 : sabotage**

Supprimer la boucle sur `CATEGORIES_AUTRES` : `test_les_trois_autres_categories_sont_repliees` doit tomber. Restaurer.

- [ ] **Étape 7 : commit**

```bash
git add watch_pages.py tests/test_watch_pages.py tests/conftest.py
git commit -m "feat(montre): bloc main courante"
```

---

## Task 4 : `watch_pages.build_trafic`

**Files:**
- Modify: `watch_pages.py`
- Modify: `tests/test_watch_pages.py`

**Interfaces:**
- Consumes : `trafic_etat.lire_routes`, `lire_alertes`, `agreger_terrains`, `compter_alertes`, `verdict_global`, `fraicheur` (Task 1)
- Produces : `build_trafic(db, now_utc) -> dict | None`

- [ ] **Étape 1 : écrire le test**

```python
class TestTrafic:
    def _db(self, routes, alertes):
        return FakeDb(
            waze_trafic=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": {"routes": routes}}],
            waze_alerts=[{"fetched_at": datetime(2026, 8, 15, 11, 59),
                          "data": alertes}],
        )

    def test_quatre_terrains_au_plus_les_plus_charges(self):
        routes = [{"name": "#I# T%d" % i, "time": 100 * (i + 1),
                   "historicTime": 100} for i in range(6)]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert len(bloc["r"]) == 4
        # Le plus charge en premier : la montre montre d'abord ce qui coince.
        assert bloc["r"][0][0] == "T5"

    def test_temps_converti_en_minutes(self):
        # currentTime est en SECONDES cote Waze.
        routes = [{"name": "#I# Ouest", "time": 1080, "historicTime": 600}]
        bloc = watch_pages.build_trafic(self._db(routes, []), NOW)
        assert bloc["r"][0][2] == 18

    def test_verdict_et_comptes_geofences(self):
        import trafic_etat
        dedans = {"type": "ACCIDENT",
                  "location": {"y": trafic_etat.ZONE_CENTER_LAT,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        dehors = {"type": "ACCIDENT",
                  "location": {"y": trafic_etat.ZONE_CENTER_LAT + 0.5,
                               "x": trafic_etat.ZONE_CENTER_LON}}
        bloc = watch_pages.build_trafic(self._db([], [dedans, dehors]), NOW)
        assert bloc["ac"] == 1
        assert bloc["vd"] == 3

    def test_sans_donnee_waze_rend_none(self):
        assert watch_pages.build_trafic(FakeDb(), NOW) is None
```

- [ ] **Étape 2 : lancer, vérifier l'échec**

Run: `python3 -m pytest tests/test_watch_pages.py::TestTrafic -q`
Expected: FAIL, `AttributeError: module 'watch_pages' has no attribute 'build_trafic'`

- [ ] **Étape 3 : implémenter**

```python
import trafic_etat

# Au-dela, la liste ne tient plus sur un cadran rond et le detail se lit sur
# le cockpit.
MAX_TERRAINS = 4


def build_trafic(db, now_utc=None):
    """Verdict global, comptes geofences et terrains les plus charges."""
    routes = trafic_etat.lire_routes(db)
    alertes = trafic_etat.lire_alertes(db)
    if not routes and not alertes:
        return None

    terrains = trafic_etat.agreger_terrains(routes)
    comptes = trafic_etat.compter_alertes(alertes)
    pire = max([t["severity"] for t in terrains], default=0)

    # Tri par gravite decroissante, pas par ratio : la montre montre d'abord
    # ce qui coince, et deux terrains de ratios voisins peuvent tomber dans
    # des paliers differents.
    ordonnes = sorted(terrains, key=lambda t: t["severity"], reverse=True)

    resume = []
    for terrain in ordonnes[:MAX_TERRAINS]:
        sens = {"in": "i", "out": "o"}.get(terrain.get("direction"), "-")
        resume.append([
            terrain["terrain"],
            sens,
            int(round((terrain["currentTime"] or 0) / 60.0)),
            terrain["severity"],
        ])

    return {
        "t": _epoch(trafic_etat.fraicheur(db)),
        "vd": trafic_etat.verdict_global(comptes, pire),
        "ac": comptes.get("ACCIDENT", 0),
        "z": comptes.get("total", 0),
        "r": resume,
    }
```

- [ ] **Étape 4 : lancer**

Run: `python3 -m pytest tests/test_watch_pages.py -q`
Expected: PASS (8 tests)

- [ ] **Étape 5 : vérifier sur la base réelle**

```bash
python3 -c "
from datetime import datetime, timezone
from pymongo import MongoClient
import watch_pages
db = MongoClient('mongodb://localhost:27017/')['titan_dev']
print(watch_pages.build_trafic(db, datetime.now(timezone.utc)))
"
```

Expected: un bloc cohérent avec ce qu'affiche `/circulation` au même instant. Si le verdict diverge, c'est la règle qui a été mal portée — reprendre l'étape 3 de la Task 1.

- [ ] **Étape 6 : sabotage**

Remplacer le tri par gravité par un tri alphabétique : `test_quatre_terrains_au_plus_les_plus_charges` doit tomber. Retirer la division par 60 : `test_temps_converti_en_minutes` doit tomber. Restaurer.

- [ ] **Étape 7 : commit**

```bash
git add watch_pages.py tests/test_watch_pages.py
git commit -m "feat(montre): bloc trafic avec verdict global geofence"
```

---

## Task 5 : `watch_pages.build_meteo`

**Files:**
- Modify: `watch_pages.py`
- Modify: `tests/test_watch_pages.py`

**Interfaces:**
- Consumes : `meteo_etat.etat_mur` (Task 2)
- Produces : `build_meteo(db, now, config) -> dict | None`

- [ ] **Étape 1 : écrire le test**

```python
class TestMeteo:
    def test_condense_l_etat_du_mur(self, monkeypatch):
        etat = {
            "actuel": {"temperature_c": 21.3, "vent_moyen_kmh": 18,
                       "vent_rafale_kmh": 34},
            "prochaine_pluie": {"attendue": True, "dans_min": 25,
                                "pic_mmh": 2.4},
            "consignes": [
                {"niveau": "vigilance", "texte": "Pluie 2 mm - parkings en herbe"},
                {"niveau": "danger", "texte": "Rafales 62 km/h - securiser les structures"},
            ],
            "vigilance": {"niveau": 1},
        }
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur",
                            lambda d, m, c: etat)
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0), {})
        assert bloc["tc"] == 21.3
        assert bloc["rf"] == 34
        assert bloc["pl"] == 25
        assert bloc["pm"] == 2.4
        # La consigne retenue est LA PLUS GRAVE, pas la premiere de la liste.
        assert bloc["cn"].startswith("Rafales 62")
        assert bloc["cl"] == 2

    def test_sans_pluie_attendue_les_champs_sont_nuls(self, monkeypatch):
        etat = {"actuel": {"temperature_c": 18.0},
                "prochaine_pluie": {"attendue": False},
                "consignes": [], "vigilance": None}
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur",
                            lambda d, m, c: etat)
        bloc = watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0), {})
        assert bloc["pl"] is None
        assert bloc["pm"] is None
        assert bloc["cn"] is None

    def test_source_qui_leve_rend_none(self, monkeypatch):
        def casse(d, m, c):
            raise RuntimeError("piaf injoignable")
        monkeypatch.setattr(watch_pages.meteo_etat, "etat_mur", casse)
        assert watch_pages.build_meteo(FakeDb(), datetime(2026, 8, 15, 12, 0), {}) is None
```

- [ ] **Étape 2 : lancer, vérifier l'échec**

Run: `python3 -m pytest tests/test_watch_pages.py::TestMeteo -q`
Expected: FAIL

- [ ] **Étape 3 : implémenter**

```python
import meteo_etat

# Niveaux de consigne du mur, replies sur l'echelle 0-3 de la montre.
NIVEAUX_CONSIGNE = {"vigilance": 1, "danger": 2, "critique": 3}

# Une consigne plus longue deborde la largeur utile du cadran rond.
CONSIGNE_MAX = 44


def build_meteo(db, now, config=None):
    """Condense l'etat du mur. None si la source est injoignable.

    AUCUN SEUIL N'EST REINVENTE ICI. Le mur decide -- rafales, WBGT, orage --
    et redige la consigne en langage d'action ; la montre la repete. C'est ce
    qui garantit que le poignet et le mur ne se contrediront jamais.
    """
    try:
        etat = meteo_etat.etat_mur(db, now, config or {})
    except Exception as exc:
        logger.warning("watch_pages : etat meteo indisponible (%s)", exc)
        return None
    if not etat:
        return None

    actuel = etat.get("actuel") or {}
    pluie = etat.get("prochaine_pluie") or {}
    attendue = bool(pluie.get("attendue"))

    # La consigne retenue est la plus grave, pas la premiere : la liste du mur
    # est chronologique, or c'est la gravite qui commande l'action.
    consigne = None
    niveau = 0
    for entree in etat.get("consignes") or []:
        rang = NIVEAUX_CONSIGNE.get(entree.get("niveau"), 0)
        if rang > niveau:
            niveau = rang
            consigne = entree.get("texte")

    vigilance = etat.get("vigilance") or {}

    return {
        "t": _epoch(etat.get("fraicheur") or now),
        "tc": actuel.get("temperature_c"),
        "v": actuel.get("vent_moyen_kmh"),
        "rf": actuel.get("vent_rafale_kmh"),
        "pl": pluie.get("dans_min") if attendue else None,
        "pm": pluie.get("pic_mmh") if attendue else None,
        "cn": consigne[:CONSIGNE_MAX] if consigne else None,
        "cl": niveau,
        "vg": vigilance.get("niveau") or 0,
    }
```

- [ ] **Étape 4 : lancer**

Run: `python3 -m pytest tests/test_watch_pages.py -q`
Expected: PASS (11 tests)

- [ ] **Étape 5 : sabotage**

Remplacer la sélection de la consigne la plus grave par `consignes[0]` : `test_condense_l_etat_du_mur` doit tomber. Restaurer.

- [ ] **Étape 6 : commit**

```bash
git add watch_pages.py tests/test_watch_pages.py
git commit -m "feat(montre): bloc meteo, consigne la plus grave"
```

---

## Task 6 : `watch_pages.build_frequentation`

**Files:**
- Modify: `watch_pages.py`
- Modify: `tests/test_watch_pages.py`

**Interfaces:**
- Consumes : `pcorg_summary._max_current_in_snapshots`, `watch_peaks.resolve_race_dt`
- Produces : `build_frequentation(db, event, year, now, location_id) -> dict | None`

- [ ] **Étape 1 : écrire le test**

```python
class TestFrequentation:
    def test_pic_du_jour_et_n1_au_jour_equivalent(self, monkeypatch):
        # On isole le calcul de l'acces Mongo : ce qui est teste ici, c'est
        # l'ALIGNEMENT au jour de course, pas la lecture des snapshots.
        appels = []

        def faux_max(db, coll, jour, loc, event=None):
            appels.append(jour)
            return (52100, "14h15") if len(appels) == 1 else (49800, "15h02")

        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots", faux_max)
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: datetime(
                                an, 4, 18, 13, 0, tzinfo=timezone.utc))

        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026,
            datetime(2026, 4, 17, 12, 0), location_id="628")

        assert bloc["pj"] == 52100
        assert bloc["ph"] == "14h15"
        assert bloc["n1"] == 49800
        # J-1 en 2026 doit interroger J-1 en 2025, pas la meme date calendaire.
        assert appels[1] == datetime(2025, 4, 18).date() - timedelta(days=1)

    def test_sans_date_de_course_pas_de_n1(self, monkeypatch):
        monkeypatch.setattr(watch_pages.watch_peaks, "resolve_race_dt",
                            lambda db, ev, an: None)
        monkeypatch.setattr(watch_pages.pcorg_summary,
                            "_max_current_in_snapshots",
                            lambda *a, **k: (52100, "14h15"))
        bloc = watch_pages.build_frequentation(
            FakeDb(), "24H MOTOS", 2026, datetime(2026, 4, 17, 12, 0))
        assert bloc["pj"] == 52100
        assert bloc["n1"] is None

    def test_sans_evenement_rend_none(self):
        assert watch_pages.build_frequentation(
            FakeDb(), None, None, datetime(2026, 4, 17, 12, 0)) is None
```

- [ ] **Étape 2 : lancer, vérifier l'échec**

Run: `python3 -m pytest tests/test_watch_pages.py::TestFrequentation -q`
Expected: FAIL

- [ ] **Étape 3 : implémenter**

```python
from datetime import timedelta

import pcorg_summary
import watch_peaks

COMPTEUR_PRINCIPAL = "628"


def build_frequentation(db, event, year, now, location_id=COMPTEUR_PRINCIPAL):
    """Pic du jour, son heure, et le pic N-1 au jour EQUIVALENT.

    L'alignement N-1 se fait au decalage au jour de course, jamais a la date
    calendaire : c'est la convention de tout le cockpit, et une edition ne
    tombe pas le meme jour du mois d'une annee sur l'autre.
    """
    if not event or year is None:
        return None

    jour = now.date()
    pic, heure = pcorg_summary._max_current_in_snapshots(
        db, "data_access", jour, location_id)

    pic_n1 = None
    course = watch_peaks.resolve_race_dt(db, event, int(year))
    course_n1 = watch_peaks.resolve_race_dt(db, event, int(year) - 1)
    if course is not None and course_n1 is not None:
        decalage = jour - course.date()
        jour_n1 = course_n1.date() + decalage
        for collection in watch_peaks.snapshot_collections(db):
            valeur, _ = pcorg_summary._max_current_in_snapshots(
                db, collection, jour_n1, location_id)
            if valeur is not None and (pic_n1 is None or valeur > pic_n1):
                pic_n1 = valeur

    return {
        "t": _epoch(now if now.tzinfo else now.replace(tzinfo=timezone.utc)),
        "pj": pic,
        "ph": heure,
        "n1": pic_n1,
    }
```

- [ ] **Étape 4 : lancer**

Run: `python3 -m pytest tests/test_watch_pages.py -q`
Expected: PASS (14 tests)

- [ ] **Étape 5 : vérifier sur la base réelle**

```bash
python3 -c "
from datetime import datetime
from pymongo import MongoClient
import watch_pages
db = MongoClient('mongodb://localhost:27017/')['titan_dev']
# Jour de course 2026 des 24H MOTOS.
print(watch_pages.build_frequentation(db, '24H MOTOS', 2026,
                                      datetime(2026, 4, 18, 20, 0)))
" 2>&1 | grep -v "^watch_peaks :"
```

Expected: `pj` proche de 50 690 (le pic de l'édition, atteint ce jour-là) et `n1` proche de 40 077 (24H MOTOS 2025). Un écart important signale un mauvais alignement.

- [ ] **Étape 6 : sabotage**

Remplacer `jour_n1` par la même date calendaire de l'année précédente : `test_pic_du_jour_et_n1_au_jour_equivalent` doit tomber. Restaurer.

- [ ] **Étape 7 : commit**

```bash
git add watch_pages.py tests/test_watch_pages.py
git commit -m "feat(montre): bloc frequentation aligne au jour de course"
```

---

## Task 7 : intégration serveur — `p`, `mr`, injection des pages

**Files:**
- Modify: `watch_state.py`
- Modify: `watch_api.py`
- Modify: `tests/test_watch_state.py`

**Interfaces:**
- Consumes : les quatre `build_*` de `watch_pages`
- Produces : payload complet, `build_state(db, now, now_utc, peaks, pages)`

- [ ] **Étape 1 : écrire les tests**

```python
class TestPresentsEtMotif:
    def test_presents_en_direct(self):
        db = FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": True},
                dict(_counter(48213, 0), current="47320"),
            ],
            evenement=[{"nom": "24H MOTOS", "short": "24HM"}],
        )
        st = watch_state.build_state(db, NOW, now_utc=NOW)
        assert st["p"] == 47320
        assert st["mr"] is None

    def test_presents_jamais_affiches_hors_direct(self):
        # Un chiffre de presents perime est indiscernable d'un chiffre juste,
        # et c'est precisement lui qu'on regarde pour decider.
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "compteur_principal_id": "628",
             "live_controle_actif": False},
            dict(_counter(48213, 1), current="47320"),
        ])
        st = watch_state.build_state(db, NOW, now_utc=NOW)
        assert st["p"] is None

    def test_motif_inactif(self):
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "compteur_principal_id": "628",
             "live_controle_actif": False},
            _counter(48213, 1),
        ])
        st = watch_state.build_state(db, NOW, now_utc=NOW)
        assert st["mr"] == "inactif"

    def test_motif_sans_releve_est_un_incident(self):
        # Drapeau leve mais plus aucun releve : le collecteur est plante alors
        # qu'on le croit en marche. Ce n'est PAS le meme evenement qu'un arret
        # volontaire, et la montre doit pouvoir les distinguer.
        db = FakeDb(data_access=[
            {"_id": "___GLOBAL___", "compteur_principal_id": "628",
             "live_controle_actif": True},
            _counter(8, minutes_ago=113 * 24 * 60),
        ])
        st = watch_state.build_state(db, NOW,
                                     now_utc=NOW.replace(tzinfo=timezone.utc))
        assert st["mr"] == "sans_releve"

    def test_repli_sur_entrees_moins_sorties(self):
        db = FakeDb(
            data_access=[
                {"_id": "___GLOBAL___", "compteur_principal_id": "628",
                 "live_controle_actif": True},
                dict(_counter(48213, 0), exits="1000"),
            ],
        )
        st = watch_state.build_state(db, NOW, now_utc=NOW)
        assert st["p"] == 47213
```

Plus, dans `TestBuildStatePics`, un test que les quatre blocs valent `None` quand `pages` n'est pas injecté, et un test de taille du payload complet sous 2 Ko.

- [ ] **Étape 2 : lancer, vérifier l'échec**

Run: `python3 -m pytest tests/test_watch_state.py -k "Presents or Motif" -q`
Expected: FAIL

- [ ] **Étape 3 : implémenter dans `watch_state.py`**

Dans `build_state`, après la résolution du compteur :

```python
    # `mr` distingue deux situations que `m: past` confondait : un
    # live-controle arrete (normal, 350 jours par an) et un collecteur plante
    # alors que le drapeau le dit en marche (incident a traiter).
    actif = read_live_active(db)
    courant = None
    if actif:
        courant = read_counter(db, location_id,
                               max_age=COUNTER_MAX_AGE, now_utc=now_utc)
    mode = "live" if courant else "past"
    motif = None
    if mode == "past":
        motif = "inactif" if not actif else "sans_releve"

    presents = None
    if courant:
        presents = _safe_int(courant.get("current"))
        if presents is None:
            sorties = _safe_int(courant.get("exits"))
            if entrees is not None and sorties is not None:
                presents = entrees - sorties
```

Et dans le dictionnaire rendu : `"p": presents, "mr": motif`, plus les quatre blocs :

```python
    blocs = {"mc": None, "tr": None, "me": None, "st": None}
    if pages is not None:
        # Chaque bloc dans son propre try : une source qui tombe ne doit pas
        # priver la montre des trois autres pages ni du WBGT.
        constructeurs = (
            # En past, main courante et frequentation n'ont pas d'objet :
            # inutile de payer quatre lectures Mongo 350 jours par an.
            ("mc", lambda: pages.build_main_courante(db, event, year, now_utc)
                   if mode == "live" else None),
            ("tr", lambda: pages.build_trafic(db, now_utc)),
            ("me", lambda: pages.build_meteo(db, now)),
            ("st", lambda: pages.build_frequentation(db, event, year, now)
                   if mode == "live" else None),
        )
        for cle, construire in constructeurs:
            try:
                blocs[cle] = construire()
            except Exception as exc:
                logger.warning("watch_state : bloc %s indisponible (%s)", cle, exc)
```

`build_state` se termine aujourd'hui par un `return {...}` litteral. Le
remplacer par une affectation puis une mise a jour, sinon les quatre cles ne
peuvent pas etre ajoutees :

```python
    payload = {
        "t": ...,          # inchange
        "n": ...,
        "m": mode,
        "mr": motif,
        "p": presents,
        "e": entrees,
        "er": debit,
        "pk": pic,
        "pkt": int(pic_ts.timestamp()) if pic_ts else None,
        "w": round(wbgt, 1) if wbgt is not None else None,
        "wl": wbgt_level(wbgt, tuple(config["wbgt_levels"])),
        "al": select_alerts(actives, config["alerts"]),
    }
    payload.update(blocs)
    return payload
```

Ajouter `import logging` et `logger = logging.getLogger(__name__)` en tête de `watch_state.py` s'ils n'y sont pas.

- [ ] **Étape 4 : brancher `watch_api.py`**

```python
import watch_pages

    payload = watch_state.build_state(
        _db(), datetime.now(), datetime.now(timezone.utc),
        peaks=watch_peaks, pages=watch_pages)
```

- [ ] **Étape 5 : lancer la suite complète**

Run: `python3 -m pytest tests/ -q`
Expected: PASS. Les doublures `lambda d, n, n_utc, **kw:` de `test_watch_api.py` acceptent déjà le nouveau mot-clé.

- [ ] **Étape 6 : mesurer la taille réelle du payload**

```bash
python3 -c "
import json
from datetime import datetime, timezone
from pymongo import MongoClient
import watch_state, watch_peaks, watch_pages
db = MongoClient('mongodb://localhost:27017/')['titan_dev']
st = watch_state.build_state(db, datetime.now(), datetime.now(timezone.utc),
                             peaks=watch_peaks, pages=watch_pages)
brut = json.dumps(st, ensure_ascii=False)
print(brut)
print(len(brut.encode()), 'octets')
" 2>&1 | grep -v "^watch_peaks :"
```

Expected: sous 2 048 octets. Au-delà, tronquer d'abord `me.cn` puis réduire `MAX_TERRAINS` — jamais supprimer un `t` de bloc.

- [ ] **Étape 7 : sabotage**

Faire construire `mc` en mode `past` : le test de non-construction doit tomber. Retirer un `try` : le test de source qui lève doit propager. Restaurer.

- [ ] **Étape 8 : commit**

```bash
git add watch_state.py watch_api.py tests/
git commit -m "feat(montre): payload complet, presents et motif du mode past"
```

---

## Task 8 : transport côté montre — cache à deux clés

**Files:**
- Modify: `garmin/cockpit-watch/source/Api.mc`
- Modify: `garmin/cockpit-watch/source/Cache.mc`
- Create: `garmin/cockpit-watch/source/Pages.mc`
- Modify: `garmin/cockpit-watch/source/ApiTest.mc`
- Create: `garmin/cockpit-watch/source/PagesTest.mc`

**Interfaces:**
- Produces : `Cache.savePages(blocs)`, `Cache.loadPages()`, `Pages.mainCourante(pg)`, `Pages.trafic(pg)`, `Pages.meteo(pg)`, `Pages.frequentation(pg)`, `Pages.verdictMot(vd)`

- [ ] **Étape 1 : écrire les tests d'abord**

```monkeyc
// source/ApiTest.mc
(:test)
function testToCacheReporteLesQuatreBlocs(logger) {
    var data = {"t" => 100, "m" => "live", "mr" => null, "p" => 47320,
                "mc" => {"s" => [2, 14]}, "tr" => {"vd" => 2},
                "me" => {"tc" => 21.3}, "st" => {"pj" => 52100},
                "al" => []};
    var st = Api.toCacheDict(data, 200);
    Test.assertEqual(st["p"], 47320);
    var pg = Api.toPagesDict(data);
    Test.assertEqual(pg["tr"]["vd"], 2);
    Test.assertEqual(pg["st"]["pj"], 52100);
    return true;
}

(:test)
function testLeNoyauNePortePasLesBlocs(logger) {
    // Le cache est coupe en deux : la glance et le service de fond lisent le
    // noyau, sur un budget de 64 Ko, et n'ont aucune raison de deserialiser
    // du trafic qu'ils n'affichent jamais.
    var data = {"t" => 100, "tr" => {"vd" => 2}, "al" => []};
    var st = Api.toCacheDict(data, 200);
    Test.assert(!st.hasKey("tr"));
    return true;
}
```

- [ ] **Étape 2 : compiler les tests, vérifier l'échec**

```bash
cd garmin/cockpit-watch
export SDK="$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2"
"$SDK/bin/monkeyc" -f monkey.jungle -o bin/test.prg -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -t
```

Expected: ERROR, `Cannot find symbol ':toPagesDict'`

- [ ] **Étape 3 : implémenter**

Dans `Api.mc`, ajouter `"p"` et `"mr"` à `toCacheDict` (**sans** les quatre blocs), et une fonction séparée :

```monkeyc
    // Les quatre blocs vont dans une SECONDE cle Storage, lue par la seule
    // app. Les mettre dans le noyau ferait deserialiser du trafic a la glance
    // a chaque affichage, sur un budget de 64 Ko dont elle utilise deja 11 %.
    function toPagesDict(data) {
        if (data == null) {
            return null;
        }
        return {
            "mc" => data["mc"],
            "tr" => data["tr"],
            "me" => data["me"],
            "st" => data["st"]
        };
    }
```

Dans `onReceive`, après `toCacheDict`, appeler `Cache.savePages(toPagesDict(data))`.

Dans `Cache.mc`, ajouter `savePages` / `loadPages` sur une clé distincte, avec le même contrôle de schéma que le noyau.

Créer `Pages.mc` — **sans annotation `(:glance)` ni `(:background)`** : ce module n'existe que pour l'app.

```monkeyc
module Pages {

    // Les mots du verdict trafic sont ceux du mur (circulation.html:494) :
    // le poignet et l'ecran doivent dire la meme chose du meme etat.
    function verdictMot(vd) {
        if (vd == null) { return "--"; }
        if (vd >= 3) { return "CRITIQUE"; }
        if (vd == 2) { return "TENSION"; }
        if (vd == 1) { return "VIGILANCE"; }
        return "FLUIDE";
    }

    function bloc(pg, cle) {
        if (pg == null) { return null; }
        return pg[cle];
    }
}
```

- [ ] **Étape 4 : compiler et lancer les tests**

```bash
"$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Expected: PASS, 53 tests.

- [ ] **Étape 5 : vérifier que la glance n'a pas grossi**

```bash
"$SDK/bin/monkeyc" -f monkey.jungle -o /tmp/mem.prg -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r --build-stats 0
```

Expected: Glance et Background inchangés à quelques octets près (2 989 / 4 205 et 2 907 / 3 778 avant cette tâche). **Une hausse notable signale que `Pages.mc` a été annoté par erreur.**

- [ ] **Étape 6 : sabotage**

Ajouter `"tr" => data["tr"]` à `toCacheDict` : `testLeNoyauNePortePasLesBlocs` doit tomber. Restaurer.

- [ ] **Étape 7 : commit**

```bash
git add source/Api.mc source/Cache.mc source/Pages.mc source/ApiTest.mc
git commit -m "feat(montre): transport des quatre blocs, cache a deux cles"
```

---

## Task 9 : page 1 — héros, motif, voyants

**Files:**
- Modify: `garmin/cockpit-watch/source/CockpitView.mc`
- Modify: `garmin/cockpit-watch/source/State.mc`
- Modify: `garmin/cockpit-watch/source/Mock.mc`
- Modify: `garmin/cockpit-watch/source/StateTest.mc`, `DessinTest.mc`

**Interfaces:**
- Consumes : `Pages.bloc`, `Cache.loadPages` (Task 8)
- Produces : `State.motif(st)`, `State.presents(st)`

- [ ] **Étape 1 : écrire les tests**

```monkeyc
// source/StateTest.mc
(:test)
function testMotifDistingueArretEtPanne(logger) {
    Test.assertEqual(State.motif({"m" => "past", "mr" => "inactif"}), "inactif");
    Test.assertEqual(State.motif({"m" => "past", "mr" => "sans_releve"}),
                     "sans_releve");
    Test.assert(State.motif({"m" => "live"}) == null);
    return true;
}
```

Et dans `DessinTest.mc`, un test de dessin par motif.

- [ ] **Étape 2 : compiler, vérifier l'échec**

Expected: ERROR, `Cannot find symbol ':motif'`

- [ ] **Étape 3 : implémenter**

Dans `State.mc` :

```monkeyc
    function motif(st) {
        if (st == null || !isPast(st)) {
            return null;
        }
        return st["mr"];
    }
```

Dans `CockpitView.drawMain` :

- héros : `passe ? st["pk"] : st["p"]`
- sous-ligne en direct : `Fmt.count(st["e"]) + " entrees - " + Fmt.rate(st["er"]) + " pers/h"`
- pied selon le motif :

```monkeyc
        if (passe) {
            var mr = State.motif(mState);
            var texte = "hors evenement";
            var couleur = Graphics.COLOR_DK_GRAY;
            if (mr != null && mr.equals("inactif")) {
                texte = "live inactif";
            } else if (mr != null && mr.equals("sans_releve")) {
                // Le drapeau dit "actif" mais plus rien n'arrive : ce n'est
                // pas un arret volontaire, c'est une panne du collecteur.
                texte = "aucun releve";
                couleur = Graphics.COLOR_RED;
            }
            dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() - hX - 17, Graphics.FONT_XTINY,
                        texte, Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
```

- bande de voyants sous le WBGT, **omise quand tout est calme** :

```monkeyc
        // L'absence dit "rien a signaler" mieux qu'une ligne de zeros, et
        // n'encombre pas une page qui doit se lire en deux secondes.
        var pg = Cache.loadPages();
        var mc = Pages.bloc(pg, "mc");
        var tr = Pages.bloc(pg, "tr");
        var bouts = [];
        if (mc != null) {
            var enCours = mc["s"][0] + mc["sc"][0] + mc["tq"][0] + mc["f"][0]
                          + mc["o"][0];
            if (enCours > 0) { bouts.add("MC " + enCours.toString()); }
        }
        if (tr != null && tr["vd"] != null && tr["vd"] >= 1) {
            bouts.add(Pages.verdictMot(tr["vd"]));
        }
        if (bouts.size() > 0) { /* dessiner la ligne, coloree par le pire */ }
```

- [ ] **Étape 4 : ajouter les scénarios de mock**

`Mock.state` gagne les quatre blocs sur les scénarios `live`, et un scénario 5 « source en panne » où `mc`, `tr`, `me`, `st` valent tous `null`. Le scénario 4 (`past`) gagne `mr => "inactif"`, et un scénario 6 porte `mr => "sans_releve"`.

- [ ] **Étape 5 : compiler, lancer**

Expected: PASS.

- [ ] **Étape 6 : mesurer la mise en page**

Créer une sonde jetable comme celle de l'étape 4 du lot précédent, mesurant la ligne de voyants et les trois libellés de pied. Vérifier le **sommet** pour les blocs hauts et la **base** pour le pied. Supprimer la sonde après usage.

- [ ] **Étape 7 : sabotage**

Faire afficher `st["p"]` en mode `past` : le test de dessin doit tomber ou la valeur doit être `--`. Restaurer.

- [ ] **Étape 8 : commit**

```bash
git commit -am "feat(montre): presents en heros, motif du mode past, voyants"
```

---

## Task 10 : icônes SVG et vue Main courante

**Files:**
- Create: `garmin/cockpit-watch/resources/drawables/mc_secours.svg`, `mc_securite.svg`, `mc_technique.svg`, `mc_flux.svg`
- Modify: `garmin/cockpit-watch/resources/drawables/drawables.xml`
- Create: `garmin/cockpit-watch/source/MainCouranteView.mc`
- Modify: `garmin/cockpit-watch/source/DessinTest.mc`

- [ ] **Étape 1 : créer les quatre icônes**

Formes simples reprenant celles du cockpit (`CATEGORY_STYLES` dans `pcorg.js`), aux couleurs exactes. 32 × 32, `viewBox="0 0 32 32"`.

```xml
<!-- mc_secours.svg — croix medicale, local_hospital, #dc2626 -->
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
    <rect x="13" y="5" width="6" height="22" fill="#dc2626"/>
    <rect x="5" y="13" width="22" height="6" fill="#dc2626"/>
</svg>
```

```xml
<!-- mc_securite.svg — bouclier, shield, #ef4444 -->
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
    <path d="M16 3 L27 8 V16 C27 22 22 27 16 29 C10 27 5 22 5 16 V8 Z"
          fill="#ef4444"/>
</svg>
```

```xml
<!-- mc_technique.svg — cle, build, #f59e0b -->
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
    <path d="M21 4 A8 8 0 1 0 27 15 L27 15 L14 28 L8 22 L21 9 Z"
          fill="none" stroke="#f59e0b" stroke-width="4"/>
</svg>
```

```xml
<!-- mc_flux.svg — deux fleches croisees, swap_calls, #0d9488 -->
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
    <path d="M9 26 V10 L4 15 M9 10 L14 15" fill="none" stroke="#0d9488"
          stroke-width="4"/>
    <path d="M23 6 V22 L28 17 M23 22 L18 17" fill="none" stroke="#0d9488"
          stroke-width="4"/>
</svg>
```

Déclarer dans `drawables.xml` :

```xml
    <bitmap id="IconeSecours" filename="mc_secours.svg" dithering="none" />
    <bitmap id="IconeSecurite" filename="mc_securite.svg" dithering="none" />
    <bitmap id="IconeTechnique" filename="mc_technique.svg" dithering="none" />
    <bitmap id="IconeFlux" filename="mc_flux.svg" dithering="none" />
```

- [ ] **Étape 2 : compiler, vérifier que les SVG sont acceptés**

Expected: BUILD SUCCESSFUL. Un SVG refusé se voit ici, pas à l'exécution.

- [ ] **Étape 3 : écrire la vue**

`MainCouranteView.mc` sur le modèle d'`EditionsView` : quatre lignes icône + `en cours (terminées)`, puis la ligne `+ N (M)`, puis l'âge du bloc. En mode `past` ou bloc `null`, afficher respectivement « hors evenement » et des tirets — **jamais des zéros**.

⚠️ Secours `#dc2626` et Sécurité `#ef4444` sont deux rouges quasi identiques : c'est la **forme** de l'icône qui porte l'identité, jamais la teinte seule.

- [ ] **Étape 4 : tests de dessin**

Ajouter à `DessinTest.mc` : bloc présent, bloc `null`, mode `past`, et tous les compteurs à zéro.

- [ ] **Étape 5 : mesurer la mise en page**

Sonde jetable : largeur de `2 (14)` en `FONT_SMALL`, hauteur de l'icône, corde disponible à chaque `y`. Cinq lignes plus un pied doivent tenir.

- [ ] **Étape 6 : commit**

```bash
git add resources/drawables source/MainCouranteView.mc source/DessinTest.mc
git commit -m "feat(montre): vue main courante avec les icones du cockpit"
```

---

## Task 11 : vue Trafic

**Files:**
- Create: `garmin/cockpit-watch/source/TraficView.mc`
- Modify: `garmin/cockpit-watch/source/DessinTest.mc`

- [ ] **Étape 1 : écrire la vue**

Bandeau du verdict en haut, **coloré ET nommé** — la couleur seule ne porte jamais une identité :

| `vd` | Mot | Couleur |
|---|---|---|
| 0 | FLUIDE | `COLOR_GREEN` |
| 1 | VIGILANCE | `COLOR_YELLOW` |
| 2 | TENSION | `COLOR_ORANGE` |
| 3 | CRITIQUE | `COLOR_RED` |

Puis les quatre terrains — nom, flèche de sens, minutes, statut — puis `N alertes · M accident(s)`, puis l'âge du bloc.

Ce squelette vaut pour les quatre vues de pages ; `EditionsView.mc` en est le modèle complet (chargement, erreur, défilement, `largeurUtile`).

```monkeyc
using Toybox.WatchUi;
using Toybox.Graphics;

class TraficView extends WatchUi.View {

    hidden function couleurVerdict(vd) {
        if (vd == null) { return Graphics.COLOR_DK_GRAY; }
        if (vd >= 3) { return Graphics.COLOR_RED; }
        if (vd == 2) { return Graphics.COLOR_ORANGE; }
        if (vd == 1) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        var tr = Pages.bloc(Cache.loadPages(), "tr");
        if (tr == null) {
            // Tirets, et on NOMME le bloc absent : un ecran vide se lit comme
            // un trafic fluide, ce qu'il ne dit pas.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hM / 2, Graphics.FONT_MEDIUM,
                        "trafic --", Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        var y = 34;
        dc.setColor(couleurVerdict(tr["vd"]), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_MEDIUM, Pages.verdictMot(tr["vd"]),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // ... terrains, comptes d'alertes, age -- positions issues de la sonde
    }
}
```

- [ ] **Étape 2 : tests de dessin** — les quatre verdicts, bloc `null`, liste de terrains vide.

- [ ] **Étape 3 : mesurer la mise en page.**

- [ ] **Étape 4 : commit**

```bash
git commit -am "feat(montre): vue trafic avec le verdict global du mur"
```

---

## Task 12 : vues Météo et Fréquentation

**Files:**
- Create: `garmin/cockpit-watch/source/MeteoView.mc`, `FrequentationView.mc`
- Modify: `garmin/cockpit-watch/source/DessinTest.mc`

- [ ] **Étape 1 : écrire `MeteoView`**

Trois strates : l'instant (température, vent, rafale), puis la pluie à venir (`Pluie dans N min` + `X mm/h au pic`, ou rien si `pl` est `null`), puis la consigne colorée par `cl`. Vigilance `vg >= 1` : liseré coloré.

- [ ] **Étape 2 : écrire `FrequentationView`**

`Pic jour` + heure, `N-1` + delta calculé sur la montre (`pj` et `n1` sont là), `Pic édition` depuis `pk`/`pkt` du noyau. En `past`, « hors evenement ».

- [ ] **Étape 3 : tests de dessin** pour les deux, dans leurs trois états.

- [ ] **Étape 4 : mesurer la mise en page des deux vues.**

- [ ] **Étape 5 : commit**

```bash
git commit -am "feat(montre): vues meteo et frequentation"
```

---

## Task 13 : navigation — six pages et menu de saut

**Files:**
- Modify: `garmin/cockpit-watch/source/CockpitView.mc`, `CockpitDelegate.mc`
- Create: `garmin/cockpit-watch/source/SautMenu.mc`

- [ ] **Étape 1 : porter le cycle de 2 à 6 pages**

`CockpitView.nextPage` / `previousPage` sur `mPage = (mPage + 1) % 6`, et `onUpdate` aiguille vers la vue correspondante. Les quatre nouvelles vues sont dessinées **dans** `CockpitView` plutôt que poussées, pour que HAUT/BAS reste un simple changement de page sans pile de vues.

- [ ] **Étape 2 : écrire le menu de saut**

`onMenu()` ouvre un `WatchUi.Menu2` de sept entrées : les six pages plus « Pics par édition ». Choisir une page pose `mPage` et referme ; choisir les éditions pousse `EditionsView`.

⚠️ MENU ouvrait directement `EditionsView`. Le raccourci disparaît au profit du menu — le vérifier explicitement, un utilisateur habitué au geste doit retrouver la vue en une entrée.

- [ ] **Étape 3 : tests** — `nextPage` boucle bien sur 6, chaque valeur de `mPage` se dessine sans lever.

- [ ] **Étape 4 : commit**

```bash
git commit -am "feat(montre): six pages en cycle et menu de saut"
```

---

## Task 14 : vérification d'ensemble et documentation

**Files:**
- Modify: `garmin/cockpit-watch/README.md`
- Modify: `CLAUDE.md`

- [ ] **Étape 1 : suite complète des deux côtés**

```bash
python3 -m pytest tests/ -q
cd garmin/cockpit-watch && "$SDK/bin/monkeydo" bin/test.prg fenix8solar51mm -t
```

Expected: tout passe.

- [ ] **Étape 2 : mémoire**

```bash
"$SDK/bin/monkeyc" -f monkey.jungle -o /tmp/mem.prg -y ~/.garmin_keys/developer_key.der -d fenix8solar51mm -r --build-stats 0
```

Expected: app largement sous son budget ; **glance et service de fond quasi inchangés** — toute hausse notable signale une annotation `(:glance)` ou `(:background)` posée par erreur sur un module de page.

- [ ] **Étape 3 : bout en bout sur la vraie base**

Reprendre le harnais Flask minimal du lot précédent (`scratchpad/e2e.py`) : 401 sans jeton, 200 avec, 401 après révocation, et le payload complet sous 2 Ko.

- [ ] **Étape 4 : vérifier la cohérence avec les murs**

Ouvrir `/circulation` et `/meteo-mur` dans un navigateur, comparer au même instant le verdict trafic et la consigne météo affichés par la montre en mock branché sur la vraie base. **Toute divergence est un défaut**, puisque les deux consomment désormais le même calcul.

- [ ] **Étape 5 : documenter**

README de la montre : les six pages, le menu de saut, le tableau des motifs (`inactif` / `sans_releve`), le nombre de tests. `CLAUDE.md` : une section sur `trafic_etat.py` et `meteo_etat.py`, en disant pourquoi ils existent — le cycle d'import et la garantie de non-divergence.

- [ ] **Étape 6 : commit**

```bash
git add -A
git commit -m "docs(montre): quatre pages operationnelles, verification d'ensemble"
```

---

## Pièges connus, à ne pas redécouvrir

- **`import meteo` déclenche l'auth de `app`.** `meteo.py` et `traffic.py` importent `app` : aucun module de calcul ne peut les importer. C'est toute la raison d'être des Tasks 1 et 2.
- **`currentTime` de Waze est en secondes**, pas en minutes.
- **`classify_congestion` compare des secondes à des seuils en minutes** sur sa branche de repli. Non corrigé : le changer modifierait l'affichage du mur.
- **Le verdict trafic existe désormais en double** — JS pour le mur (`circulation.html`), Python pour la montre (`trafic_etat`). À surveiller ; l'unification passerait par faire consommer le verdict serveur au mur, hors périmètre ici.
- **`Test.assertEqual(X, null)` plante** dans le SDK. Utiliser `Test.assert(X == null)`.
- **`hidden` est refusé à l'échelle module** en Monkey C, et `method(:x)` ne fonctionne pas dans un module — utiliser `new Lang.Method(Module, :x)`.
- **Sur un cadran rond, le sommet contraint un bloc de la moitié haute, la base un bloc de la moitié basse.** Vérifier le mauvais bord a déjà laissé passer trois chevauchements.
- **Un double de test qui ignore un filtre rend le test tautologique.** `FakeCollection` lève sur tout opérateur inconnu ; garder cette discipline en ajoutant `aggregate`.
