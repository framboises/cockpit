"""Fidelite du moteur client des patrouilles (static/js/formation/patrouilles.js)
au rejeu serveur (formation/patrouilles.py) : memes journaux, memes resultats.

Le moteur pur du module JS s'exporte sous node (`module.exports`) ; le test
l'execute par `node` et compare traites / manques / erreurs / ignores et le
statut de chaque incident. Saut propre si node est absent.
"""
import json
import os
import random
import shutil
import subprocess

import pytest

from formation import banque, patrouilles

NODE = shutil.which("node")
JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "static", "js", "formation", "patrouilles.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node absent")

# Rejoue chaque cas comme le client : actions "engager" a t_ms fini, triees par le
# temps (tri stable), passees une a une au moteur.
_HARNAIS = r"""
const M = require(process.argv[1]);
const data = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = data.cas.map(function (c) {
  const e = M.creer(data.plan, c.incidents || data.plan.INCIDENTS);
  const acts = c.actions.filter(function (a) {
    return a && typeof a === "object" && a.type === "engager" && typeof a.t_ms === "number" && isFinite(a.t_ms);
  }).map(function (a, k) { return [a, k]; });
  acts.sort(function (x, y) { return x[0].t_ms - y[0].t_ms || x[1] - y[1]; });
  let ignores = 0;
  acts.forEach(function (x) {
    const code = M.engager(e, x[0].t_ms, c.coef, x[0].moyen, x[0].incident);
    if (code !== "ok" && code !== "mauvais_type") ignores++;
  });
  const b = M.bilan(e);
  b.ignores = ignores;
  return b;
});
process.stdout.write(JSON.stringify(out));
"""


def _node(plan, cas):
    r = subprocess.run([NODE, "-e", _HARNAIS, JS], input=json.dumps({"plan": plan, "cas": cas}),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _plan():
    return banque.charger()["patrouilles"]


def _journal_aleatoire(rng, incidents, coef, n):
    units = [u["id"] for u in _plan()["UNITS"]]
    ids = [i["id"] for i in incidents]
    fin = max((i["t"] + i["dl"]) * 1000 for i in incidents) * coef
    return [{"type": "engager", "moyen": rng.choice(units), "incident": rng.choice(ids),
             "t_ms": rng.uniform(0, fin + 3000)} for _ in range(n)]


def _journal_glouton_flottant(rng, coef):
    """A l'ouverture de chaque incident (+ un delai de reaction aleatoire), le
    premier moyen du bon type libre ; parfois d'abord un moyen d'un autre type."""
    units = _plan()["UNITS"]
    out = []
    for inc in _plan()["INCIDENTS"]:
        t = (inc["t"] * 1000 + rng.uniform(0, 9000)) * coef
        if rng.random() < 0.4:
            autre = rng.choice([u for u in units if u["type"] != inc["type"]])
            out.append({"type": "engager", "moyen": autre["id"], "incident": inc["id"], "t_ms": t})
            t += rng.uniform(100, 1500)
        bon = [u for u in units if u["type"] == inc["type"]]
        out.append({"type": "engager", "moyen": rng.choice(bon)["id"], "incident": inc["id"], "t_ms": t})
    return out


def _cas():
    rng = random.Random(20261122)
    cas = []
    for coef in (1.0, 1.33, 1.5):
        for _ in range(25):
            cas.append({"coef": coef, "actions": _journal_glouton_flottant(rng, coef)})
        for _ in range(25):
            cas.append({"coef": coef, "actions": _journal_aleatoire(rng, _plan()["INCIDENTS"], coef, 40)})
        ent = banque.charger()["entrainement"]["patrouilles"]["INCIDENTS"]
        for _ in range(5):
            cas.append({"coef": coef, "incidents": ent, "actions": _journal_aleatoire(rng, ent, coef, 6)})
    # Bord : arrivee exactement a l'echeance (traite), engagement exactement a l'echeance (echu)
    d1 = _ech("I1")
    t = d1 - 8 * patrouilles.PAS_MS
    cas.append({"coef": 1.0, "actions": [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": t}]})
    cas.append({"coef": 1.0, "actions": [{"type": "engager", "moyen": "P2", "incident": "I1", "t_ms": d1}]})
    cas.append({"coef": 1.33, "actions": [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": d1 * 1.33 - 1}]})
    # Relais : patrouille sur le malaise I9, S1 hors du plan (40-115 s), S2 libre ou deja engage
    for coef in (1.0, 1.33):
        for s2 in (False, True):
            for t in (46000, 47000, 55000):
                acts = [{"type": "engager", "moyen": "S2", "incident": "I8", "t_ms": 43000 * coef}] if s2 else []
                cas.append({"coef": coef, "actions": acts + [
                    {"type": "engager", "moyen": "P2", "incident": "I9", "t_ms": t * coef},
                    {"type": "engager", "moyen": "S1", "incident": "I9", "t_ms": (t + 10) * coef}]})
    # Le journal du candidat ideal, a plusieurs vitesses
    from test_formation_patrouilles import journal_ideal
    for r_ms in (1500, 4000, 9000):
        cas.append({"coef": 1.0, "actions": journal_ideal(reaction_ms=r_ms)})
    return cas


def _ech(iid):
    i = next(x for x in _plan()["INCIDENTS"] if x["id"] == iid)
    return (i["t"] + i["dl"]) * 1000


def test_moteur_js_identique_au_rejeu():
    cas = _cas()
    resultats = _node(_plan(), cas)
    assert len(resultats) == len(cas)
    vus = {"traite": 0, "erreur": 0, "ignore": 0, "relais": 0, "pas_le_plus_proche": 0, "mauvais_en_route": 0}
    for c, js in zip(cas, resultats):
        py = patrouilles.rejouer(c["actions"], c["coef"], incidents=c.get("incidents"))
        for k in ("traites", "manques", "erreurs", "ignores", "plus_proches"):
            assert js[k] == py[k], (k, c)
        for dj, dp in zip(js["detail"], py["detail_incidents"]):
            assert dj["id"] == dp["id"] and dj["statut"] == dp["statut"] and dj["moyen"] == dp["moyen"], c
            assert dj["plus_proche"] == dp["plus_proche"] and dj["relais"] == dp["relais"], c
            assert dj["mauvais_moyens"] == dp["mauvais_moyens"], c
            vus["mauvais_en_route"] += len(dp["mauvais_moyens"])
            vus["relais"] += 1 if dp["relais"] and dp["statut"] == "traite" else 0
            vus["pas_le_plus_proche"] += 1 if dp["plus_proche"] is False else 0
            if dp["arrivee_ms"] is not None:
                assert abs(dj["arrivee"] - dp["arrivee_ms"]) <= 0.5
        vus["traite"] += py["traites"]
        vus["erreur"] += py["erreurs"]
        vus["ignore"] += py["ignores"]
    # les journaux couvrent bien les trois issues
    assert all(v > 0 for v in vus.values()), vus


def test_moteur_js_coef_allonge_echeances():
    t = _ech("I1") + 2000 - 8 * patrouilles.PAS_MS
    a = [{"type": "engager", "moyen": "P1", "incident": "I1", "t_ms": t}]
    r1, r133 = _node(_plan(), [{"coef": 1.0, "actions": a}, {"coef": 1.33, "actions": a}])
    assert r1["traites"] == 0 and r133["traites"] == 1


def test_moteur_js_chemin_et_position():
    script = r"""
const M = require(process.argv[1]);
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const pl = M.lirePlan(plan);
const e = M.creer(plan, plan.INCIDENTS);
M.engager(e, 2000, 1, "P1", "I1");
const u = e.units[0];
process.stdout.write(JSON.stringify({
  chemin: M.chemin(pl, [0, 0], [0, 11]),
  dep: M.position(u, 2000, false), mi: M.position(u, 2000 + 430 * 1.5, false),
  pas: M.position(u, 2000 + 430 * 1.5, true), arr: M.position(u, 99999, false),
  phases: [M.phase(u, 2000), M.phase(u, u.arrivee), M.phase(u, u.libre_a)],
  etats: [M.etatIncident(e.incs[0], 1999), M.etatIncident(e.incs[0], 3000), M.etatIncident(e.incs[0], u.arrivee),
          M.etatIncident(e.incs[1], 45000)]
}));
"""
    r = subprocess.run([NODE, "-e", script, JS], input=json.dumps(_plan()), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert [tuple(c) for c in d["chemin"]] == patrouilles.chemin((0, 0), (0, 11))
    p = patrouilles.chemin((1, 1), (1, 9))
    assert d["dep"] == list(p[0]) and d["arr"] == list(p[-1])
    assert d["mi"] == [(p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2]
    assert d["pas"] == list(p[1])
    assert d["phases"] == ["route", "site", "dispo"]
    assert d["etats"] == ["attente", "route", "traite", "manque"]


def test_node_check():
    r = subprocess.run([NODE, "--check", JS], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
