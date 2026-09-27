const fs = require("fs");
const d = db.getSiblingDB("titan");
const OUT = "E:/TITAN/production/cockpit/exports/waze/";

const JOURS = [
  ["2026-09-13", new Date("2026-09-12T22:00:00Z"), new Date("2026-09-13T22:00:00Z")],
  ["2026-09-19", new Date("2026-09-18T22:00:00Z"), new Date("2026-09-19T22:00:00Z")],
];
const BORNE_MIN = JOURS[0][1], BORNE_MAX = JOURS[1][2];

// ---------- helpers ----------
const norm = s => String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
function paris(dt) { return dt.toLocaleString("sv-SE", { timeZone: "Europe/Paris" }); } // "YYYY-MM-DD HH:mm:ss"
function jourDe(dt) { return paris(dt).slice(0, 10); }
function heureDe(dt) { return paris(dt).slice(11, 19); }
const SEMAINE = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];
function semaineDe(iso) { return SEMAINE[new Date(iso + "T12:00:00Z").getUTCDay()]; }
function csv(v) {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[;"\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function ecrire(nom, entetes, lignes) {
  const txt = "\uFEFF" + entetes.join(";") + "\r\n" +
    lignes.map(l => l.map(csv).join(";")).join("\r\n") + "\r\n";
  fs.writeFileSync(OUT + nom, txt);
  print("  " + nom + "  -> " + lignes.length + " lignes");
}

// ---------- axes cibles demandes ----------
const CIBLES = [
  ["D323", /(^|[^a-z0-9])d323([^0-9]|$)/],
  ["D338", /(^|[^a-z0-9])d338([^0-9]|$)/],
  ["Avenue du Panorama", /panorama/],
  ["Hunaudieres", /hunaudi/],
  ["Avenue Antares", /antar/],
  ["Chemin aux Boeufs", /(boeufs|bœufs)/],
];
function cibles(...textes) {
  const n = textes.map(norm).join(" || ");
  return CIBLES.filter(([, re]) => re.test(n)).map(([lab]) => lab).join(" + ");
}

print("### lecture des snapshots ###");
const snaps = d.waze_trafic_history.find(
  { fetched_at: { $gte: BORNE_MIN, $lt: BORNE_MAX } }
).sort({ fetched_at: 1 }).toArray()
  .filter(s => JOURS.some(([, f, t]) => s.fetched_at >= f && s.fetched_at < t));
print("  trafic : " + snaps.length + " snapshots");

// ---------- mapping itineraire -> rues (union sur les 2 jours) ----------
const ruesPar = {};   // id -> Set
const metaPar = {};   // id -> {name, length, historicTime}
for (const s of snaps) for (const r of s.data.routes || []) {
  const id = String(r.id);
  ruesPar[id] = ruesPar[id] || new Set();
  metaPar[id] = { name: r.name, length: r.length, ref: r.historicTime };
  for (const sr of r.subRoutes || []) {
    if (sr.fromName) ruesPar[id].add(sr.fromName);
    if (sr.toName) ruesPar[id].add(sr.toName);
  }
}
const cibleParId = {};
for (const id of Object.keys(metaPar)) {
  const rues = [...ruesPar[id]];
  cibleParId[id] = cibles(metaPar[id].name, ...rues);
}

// ---------- 00 : liste des axes pour selection ----------
ecrire("00_axes_disponibles.csv",
  ["itineraire_id", "itineraire", "longueur_m", "temps_reference_s", "nb_rues_connues", "rues_traversees", "axes_cibles"],
  Object.keys(metaPar)
    .sort((a, b) => metaPar[a].name.localeCompare(metaPar[b].name))
    .map(id => {
      const rues = [...ruesPar[id]].sort();
      return [id, metaPar[id].name, metaPar[id].length, metaPar[id].ref, rues.length, rues.join(" | "), cibleParId[id]];
    }));

// ---------- 01 / 02 : series temporelles par itineraire ----------
const lignesRoutes = [];
for (const s of snaps) {
  const j = jourDe(s.fetched_at), h = heureDe(s.fetched_at);
  for (const r of s.data.routes || []) {
    const id = String(r.id);
    const t = Number(r.time), ref = Number(r.historicTime);
    lignesRoutes.push([
      j, h, s.fetched_at.toISOString(), semaineDe(j),
      id, r.name, r.length, t, ref, t - ref,
      ref > 0 ? Math.round((t / ref - 1) * 100) : "",
      r.jamLevel, cibleParId[id],
    ]);
  }
}
const ENT_ROUTES = ["date", "heure_paris", "horodatage_utc", "jour_semaine", "itineraire_id", "itineraire",
  "longueur_m", "temps_s", "temps_reference_s", "retard_s", "retard_pct", "jam_level", "axes_cibles"];
ecrire("01_itineraires_temps.csv", ENT_ROUTES, lignesRoutes);
ecrire("02_itineraires_axes_cibles.csv", ENT_ROUTES, lignesRoutes.filter(l => l[12]));

// ---------- 05 : bouchons par releve ----------
const lignesBouchons = snaps.map(s => {
  const lj = s.data.lengthOfJams || [], uj = s.data.usersOnJams || [];
  const parNiv = {}; for (const x of lj) parNiv[x.jamLevel] = Number(x.jamLength || 0);
  const j = jourDe(s.fetched_at);
  return [
    j, heureDe(s.fetched_at), s.fetched_at.toISOString(), semaineDe(j),
    lj.reduce((a, x) => a + Number(x.jamLength || 0), 0),
    parNiv[1] || 0, parNiv[2] || 0, parNiv[3] || 0, parNiv[4] || 0, parNiv[5] || 0,
    Math.round(uj.reduce((a, x) => a + Number(x.wazersCount || 0), 0)),
    (s.data.irregularities || []).length,
    (s.data.routes || []).length,
  ];
});
ecrire("05_bouchons_par_releve.csv",
  ["date", "heure_paris", "horodatage_utc", "jour_semaine", "bouchons_total_m",
   "niveau1_m", "niveau2_m", "niveau3_m", "niveau4_m", "niveau5_m",
   "wazers_sur_bouchons", "nb_irregularites", "nb_itineraires"],
  lignesBouchons);

// ---------- 03 / 04 : alertes uniques ----------
print("### lecture des alertes ###");
const vus = {};   // date|uuid -> agregat
let nSnapAl = 0;
d.waze_alerts_history.find({ fetched_at: { $gte: BORNE_MIN, $lt: BORNE_MAX } })
  .sort({ fetched_at: 1 }).forEach(s => {
    if (!JOURS.some(([, f, t]) => s.fetched_at >= f && s.fetched_at < t)) return;
    nSnapAl++;
    const j = jourDe(s.fetched_at);
    for (const a of s.data || []) {
      const k = j + "|" + a.uuid;
      if (!vus[k]) vus[k] = { j, a, premier: s.fetched_at, dernier: s.fetched_at, n: 0 };
      vus[k].dernier = s.fetched_at; vus[k].n++; vus[k].a = a;
    }
  });
print("  alertes : " + nSnapAl + " snapshots, " + Object.keys(vus).length + " alertes uniques");

const lignesAl = Object.values(vus).sort((x, y) => x.premier - y.premier).map(v => {
  const a = v.a, loc = a.location || {};
  const pub = a.pubMillis ? new Date(Number(a.pubMillis)) : null;
  return [
    v.j, semaineDe(v.j), a.uuid,
    heureDe(v.premier), heureDe(v.dernier),
    Math.round((v.dernier - v.premier) / 60000), v.n,
    pub ? paris(pub) : "",
    a.type || "", a.subtype || "", a.street || "", a.city || "",
    loc.y !== undefined ? Number(loc.y).toFixed(6) : "",
    loc.x !== undefined ? Number(loc.x).toFixed(6) : "",
    a.roadType === undefined ? "" : a.roadType,
    a.reliability === undefined ? "" : a.reliability,
    a.confidence === undefined ? "" : a.confidence,
    a.reportRating === undefined ? "" : a.reportRating,
    a.reportByMunicipalityUser === undefined ? "" : a.reportByMunicipalityUser,
    (a.reportDescription || "").replace(/[\r\n]+/g, " "),
    cibles(a.street, a.city),
  ];
});
const ENT_AL = ["date", "jour_semaine", "uuid", "premier_vu_paris", "dernier_vu_paris", "duree_min", "nb_releves",
  "publiee_le_paris", "type", "sous_type", "rue", "ville", "latitude", "longitude",
  "road_type", "fiabilite", "confiance", "note", "signale_par_municipalite", "description", "axes_cibles"];
ecrire("03_alertes.csv", ENT_AL, lignesAl);
ecrire("04_alertes_axes_cibles.csv", ENT_AL, lignesAl.filter(l => l[20]));

print("\n### recapitulatif axes cibles ###");
for (const [lab] of CIBLES) {
  const nr = new Set(lignesRoutes.filter(l => String(l[12]).includes(lab)).map(l => l[5])).size;
  const na = lignesAl.filter(l => String(l[20]).includes(lab)).length;
  print("  " + lab.padEnd(20) + " itineraires=" + nr + "  alertes=" + na);
}
