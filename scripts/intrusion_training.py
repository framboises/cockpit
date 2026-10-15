"""Entrainement de nuit de la verification IA des intrusions, a partir des
reponses des operateurs (pilules de la fiche et de la galerie, photo par
photo ou par intrusion, cadres poses).

Lance chaque nuit par la tache planifiee "Cockpit - Entrainement IA
intrusions" (scripts/intrusion_training.bat). Ne fait rien tant qu'il n'y a
pas assez de reponses NOUVELLES ; sinon :

  Niveau 2 - l'avis (SigLIP) : apprend a quoi ressemblent sur NOS cameras une
    personne, un vehicule, un animal, le grillage, les reflets... Une petite
    couche est entrainee sur les empreintes SigLIP des zooms (service
    IntrusionVerifier, POST /embed), en partant des phrases d'origine.
    Validation croisee : installee seulement si elle fait mieux que les
    phrases d'origine ET ne rate pas plus de vraies intrusions. Fichier
    head.json relu a chaud par le service (pas de redemarrage), l'ancien est
    garde en head.prev.json.
  Niveau 1 - le seuil d'alerte (cockpit_settings detection_ai_filter.min_conf,
    lu par le recepteur Hik) : choisi sur les reponses, une intrusion ratee
    coutant 5 fausses alertes ; borne 0,15-0,60, 0,10 de variation max par nuit.

Un rapport part par mail (SMTP du serveur, comme les autres mails de Cockpit)
a chaque analyse, et un point d'avancement le lundi quand rien n'a tourne.

  python scripts/intrusion_training.py              (tache de nuit)
  python scripts/intrusion_training.py --dry-run    (analyse, rien installe, pas de mail)
  python scripts/intrusion_training.py --force      (ignore le minimum de reponses nouvelles)
  python scripts/intrusion_training.py --rollback   (remet l'avis precedent)
"""
import argparse
import html
import json
import logging
import os
import random
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import torch
from pymongo import MongoClient

COCKPIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(COCKPIT_DIR, "logs")
LOCK_PATH = os.path.join(LOG_DIR, "intrusion_training.lock")
EMBED_URL = os.getenv("INTRUSION_VERIFIER_EMBED_URL", "http://127.0.0.1:4011/embed")
HEAD_PATH = os.getenv("INTRUSION_VERIFIER_HEAD",
                      r"E:\TITAN\production\tagger\models\analytics\intrusion_head\head.json")
MAIL_TO = [m.strip() for m in os.getenv("INTRUSION_TRAINING_MAIL_TO", "l.arnault@lemans.org").split(",") if m.strip()]

# Quand se lancer (reponses au total / nouvelles depuis la derniere analyse)
L2_MIN, L2_MIN_NEW, L2_MIN_CLASSES = 150, 30, 3   # 3 categories d'au moins 10 exemples
L1_MIN, L1_MIN_NEW, L1_MIN_EACH = 80, 20, 15      # dont 15 vraies et 15 fausses
# Garde-fous
L2_GAIN = 0.03                  # +3 points de bonnes reponses au minimum
L1_BOUNDS, L1_STEP = (0.15, 0.60), 0.10
MISS_COST = 5                   # une intrusion ratee = 5 fausses alertes
# Memes regles que scripts/intrusion_verifier.py (avis retenu comme confiance)
SIG_REAL, SIG_YOLO_MIN = 0.6, 0.1
REAL = {"personne", "vehicule", "velo"}
# Reponse de l'operateur -> categorie de l'avis
TO_CLASS = {"velo": "personne", "rien": "sol"}

log = logging.getLogger("intrusion_training")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "intrusion_training-%s.log" % datetime.now().strftime("%Y%m%d"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)])


def _acquire_lock():
    os.makedirs(LOG_DIR, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
    try:
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return None
    return fd


def _get_db():
    env = os.getenv("TITAN_ENV", "dev").strip().lower()
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=10000)
    return client["titan" if env in ("prod", "production") else "titan_dev"]


# ---------------------------------------------------------------------------
# Reponses des operateurs
# ---------------------------------------------------------------------------
def _yolo_real(ai):
    """Confiance personne / vehicule du detecteur sur la photo (None si inconnue)."""
    if not ai or "detections" not in ai:
        return None
    return max([d.get("conf") or 0 for d in ai.get("detections") or [] if d.get("category") in REAL] or [0.0])


def collect(db):
    """-> liste d'exemples {photo, rect, truth (ou None), real (bool), yolo,
    real_conf, camera, src}. Une photo = un exemple ; la reponse photo par
    photo l'emporte sur les cadres, puis sur l'avis d'intrusion."""
    out, seen = [], set()
    q = {"$or": [{"photos.truth": {"$exists": True}}, {"photos.label": {"$exists": True}},
                 {"ai_vote.vote": {"$exists": True}}]}
    for e in db["hik_intrusions"].find(q, {"photos": 1, "ai": 1, "ai_vote": 1, "camera_name": 1}):
        cam = e.get("camera_name", "")
        for p in e.get("photos") or []:
            truth = (p.get("truth") or {}).get("truth")
            src = "photo"
            if not truth and p.get("label"):
                boxes = p["label"].get("boxes") or []
                truth, src = (boxes[0].get("category") if boxes else "rien"), "cadres"
            if truth and p.get("photo") not in seen:
                seen.add(p["photo"])
                pa = p.get("ai") or {}
                out.append({"photo": p["photo"], "rect": p.get("rect"), "truth": truth, "real": truth in REAL,
                            "yolo": _yolo_real(pa), "real_conf": pa.get("real_conf"), "camera": cam, "src": src})
        ai, v = e.get("ai") or {}, e.get("ai_vote") or {}
        if not v.get("vote") or ai.get("status") not in ("confirmed", "filtered") or not ai.get("photo") \
                or ai["photo"] in seen:
            continue
        p = next((x for x in e.get("photos") or [] if x.get("photo") == ai["photo"]), None)
        if not p:
            continue
        seen.add(p["photo"])
        truth = v.get("truth")
        real = (truth in REAL) if truth else ((v["vote"] == "up") == (ai["status"] == "confirmed"))
        pa = p.get("ai") or {}
        out.append({"photo": p["photo"], "rect": p.get("rect"), "truth": truth, "real": real,
                    "yolo": _yolo_real(pa), "real_conf": pa.get("real_conf", ai.get("real_conf")),
                    "camera": cam, "src": "intrusion"})
    return out


def embed(samples):
    """Empreintes SigLIP (service IntrusionVerifier). Retire les exemples sans
    photo lisible. -> (samples, F, keys, T, scale)"""
    feats, meta = [], None
    for i in range(0, len(samples), 16):
        body = {"items": [{"photo": s["photo"], "rect": s["rect"]} for s in samples[i:i + 16]]}
        req = urllib.request.Request(EMBED_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.load(r)
        feats += res["features"]
        meta = meta or res
    keep = [(s, f) for s, f in zip(samples, feats) if f]
    if not keep:
        return [], None, meta["keys"], None, meta["scale"]
    return ([s for s, _ in keep], torch.tensor([f for _, f in keep]), meta["keys"],
            torch.tensor(meta["text"]), float(meta["scale"]))


# ---------------------------------------------------------------------------
# Niveau 2 : l'avis appris
# ---------------------------------------------------------------------------
def probs(F, W, b, scale):
    return torch.softmax(scale * (F @ W.T) + (b if b is not None else 0), -1)


def train_head(F, y, W0, scale, K, epochs=300, lr=1e-3, lam=0.5):
    """Couche lineaire partant des phrases d'origine (W0), rappelee vers
    elles (lam) : peu d'exemples ne la font pas deriver. Classes equilibrees."""
    W = W0.clone().requires_grad_(True)
    b = torch.zeros(K, requires_grad=True)
    cnt = torch.bincount(y, minlength=K).float()
    cw = torch.where(cnt > 0, cnt.sum() / (cnt.clamp(min=1) * (cnt > 0).sum()), torch.zeros(K))
    opt = torch.optim.Adam([W, b], lr=lr)
    for _ in range(epochs):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(scale * (F @ W.T) + b, y, weight=cw) + lam * ((W - W0) ** 2).sum()
        loss.backward()
        opt.step()
    return W.detach(), b.detach()


def real_conf(yolo, p, keys):
    """Confiance personne / vehicule retenue par le verificateur."""
    sig = float(p[keys.index("personne")] + p[keys.index("vehicule")])
    if yolo >= SIG_YOLO_MIN and sig >= SIG_REAL and sig > yolo:
        return sig
    return yolo


def level2(samples, F, keys, T, scale, min_conf, current, force):
    """-> rapport (dict) ; installe head.json si c'est mieux."""
    idx = [i for i, s in enumerate(samples) if s["truth"]]
    K = len(keys)
    y_all = [keys.index(TO_CLASS.get(samples[i]["truth"], samples[i]["truth"])) for i in idx]
    counts = {keys[k]: y_all.count(k) for k in range(K) if y_all.count(k)}
    rep = {"n": len(idx), "counts": counts, "installed": False}
    big = [k for k, n in counts.items() if n >= 10]
    new = current["new_truth"]
    if len(idx) < L2_MIN or len(big) < L2_MIN_CLASSES or (new < L2_MIN_NEW and not force):
        rep["skip"] = ("%d reponses precises sur %d, %d categorie(s) d'au moins 10 exemples sur %d, %d nouvelle(s)"
                       % (len(idx), L2_MIN, len(big), L2_MIN_CLASSES, new))
        return rep, None
    Fi, y = F[idx], torch.tensor(y_all)
    # Validation croisee : 5 tirages 80 / 20 stratifies
    rnd = random.Random(42)
    ok_zero = ok_head = tot = 0
    miss_zero = miss_head = fa_zero = fa_head = 0
    per = {k: [0, 0, 0] for k in counts}  # bonnes phrases, bonnes apprises, total
    for rep_i in range(5):
        test = set()
        for k in counts:
            members = [j for j in range(len(idx)) if y_all[j] == keys.index(k)]
            rnd.shuffle(members)
            if len(members) >= 2:
                test.update(members[:max(1, len(members) // 5)])
        tr = [j for j in range(len(idx)) if j not in test]
        W, b = train_head(Fi[tr], y[tr], T, scale, K)
        te = sorted(test)
        pz, ph = probs(Fi[te], T, None, scale), probs(Fi[te], W, b, scale)
        for n, j in enumerate(te):
            s, lab = samples[idx[j]], keys[y_all[j]]
            z_ok, h_ok = int(pz[n].argmax()) == y_all[j], int(ph[n].argmax()) == y_all[j]
            ok_zero += z_ok
            ok_head += h_ok
            tot += 1
            per[lab][0] += z_ok
            per[lab][1] += h_ok
            per[lab][2] += 1
            if s["yolo"] is not None:
                rz, rh = real_conf(s["yolo"], pz[n], keys), real_conf(s["yolo"], ph[n], keys)
                if s["real"]:
                    miss_zero += rz < min_conf
                    miss_head += rh < min_conf
                else:
                    fa_zero += rz >= min_conf
                    fa_head += rh >= min_conf
    acc_z, acc_h = ok_zero / tot, ok_head / tot
    rep.update({"acc_zero": acc_z, "acc_head": acc_h, "tested": tot,
                "miss_zero": miss_zero, "miss_head": miss_head, "fa_zero": fa_zero, "fa_head": fa_head,
                "per_class": {k: (v[0] / v[2], v[1] / v[2], v[2]) for k, v in per.items() if v[2]}})
    prev_acc = (current or {}).get("l2_acc")
    # Personne / vehicule : jamais plus de 5 points de moins (c'est ce qui donne l'alerte)
    drop = [k for k in ("personne", "vehicule") if k in rep["per_class"] and rep["per_class"][k][2] >= 10
            and rep["per_class"][k][1] < rep["per_class"][k][0] - 0.05]
    good = acc_h >= acc_z + L2_GAIN and miss_head <= miss_zero and not drop \
        and (prev_acc is None or acc_h >= prev_acc - 0.02)
    if not good:
        rep["refused"] = ("pas assez mieux que les phrases d'origine" if acc_h < acc_z + L2_GAIN
                          else "raterait plus de vraies intrusions" if miss_head > miss_zero
                          else "reconnaitrait moins bien : " + ", ".join(drop) if drop
                          else "moins bon que l'avis appris actuel (%d %%)" % round(prev_acc * 100))
        return rep, None
    W, b = train_head(Fi, y, T, scale, K)  # version finale : toutes les reponses
    rep["version"] = datetime.now().strftime("%Y%m%d-%H%M")
    return rep, {"keys": keys, "W": [[round(v, 6) for v in row] for row in W.tolist()],
                 "b": [round(v, 6) for v in b.tolist()], "version": rep["version"], "n": len(idx),
                 "acc": acc_h, "acc_zero": acc_z, "counts": counts}


def install_head(head):
    os.makedirs(os.path.dirname(HEAD_PATH), exist_ok=True)
    if os.path.exists(HEAD_PATH):
        os.replace(HEAD_PATH, HEAD_PATH.replace(".json", ".prev.json"))
    tmp = HEAD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(head, f)
    os.replace(tmp, HEAD_PATH)


def load_head():
    try:
        with open(HEAD_PATH, encoding="utf-8") as f:
            h = json.load(f)
        return torch.tensor(h["W"]), torch.tensor(h["b"]), h
    except (OSError, ValueError, KeyError):
        return None, None, None


# ---------------------------------------------------------------------------
# Niveau 1 : le seuil d'alerte
# ---------------------------------------------------------------------------
def level1(samples, F, keys, T, scale, W, b, min_conf, current, force):
    pr = probs(F, W if W is not None else T, b, scale) if F is not None else None
    rows = []
    for i, s in enumerate(samples):
        if s["yolo"] is not None and pr is not None:
            rc = real_conf(s["yolo"], pr[i], keys)
        elif s["real_conf"] is not None:
            rc = float(s["real_conf"])
        else:
            continue
        rows.append((rc, s["real"]))
    n_real = sum(1 for _, r in rows if r)
    n_false = len(rows) - n_real
    rep = {"n": len(rows), "real": n_real, "false": n_false, "current": min_conf, "changed": False}
    new = current["new_total"]
    if len(rows) < L1_MIN or n_real < L1_MIN_EACH or n_false < L1_MIN_EACH or (new < L1_MIN_NEW and not force):
        rep["skip"] = ("%d reponses sur %d (%d vraies, %d fausses, %d minimum de chaque), %d nouvelle(s)"
                       % (len(rows), L1_MIN, n_real, n_false, L1_MIN_EACH, new))
        return rep

    def score(t):
        miss = sum(1 for rc, r in rows if r and rc < t)
        fa = sum(1 for rc, r in rows if not r and rc >= t)
        return miss, fa, miss * MISS_COST + fa
    grid = [round(0.10 + 0.05 * i, 2) for i in range(11)]  # 0,10 a 0,60
    rep["table"] = [(t,) + score(t) for t in grid]
    lo = max(L1_BOUNDS[0], min_conf - L1_STEP)
    hi = min(L1_BOUNDS[1], min_conf + L1_STEP)
    allowed = [t for t in grid if lo - 1e-9 <= t <= hi + 1e-9]
    cur_cost = score(min_conf)[2]
    best = min(allowed, key=lambda t: (score(t)[2], abs(t - min_conf)))
    rep["best"], rep["cur_cost"], rep["best_cost"] = best, cur_cost, score(best)[2]
    rep["changed"] = score(best)[2] < cur_cost and abs(best - min_conf) > 1e-9
    return rep


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------
def pct(x):
    return "%d %%" % round(x * 100)


def report_lines(l2, l1, installed_head, new_conf, total, dry):
    L = []
    L.append("%d reponses d'operateurs utilisables (photo par photo, cadres, avis d'intrusion)." % total)
    L.append("")
    L.append("NIVEAU 2 - l'avis de l'IA (ce que c'est : personne, animal, grillage...)")
    if l2.get("skip"):
        L.append("  Pas encore : " + l2["skip"] + ".")
    else:
        L.append("  %d reponses precises : %s" % (l2["n"], ", ".join("%s %d" % kv for kv in sorted(l2["counts"].items(), key=lambda x: -x[1]))))
        L.append("  Bonnes reponses sur des photos mises de cote : %s avec les phrases d'origine, %s avec l'avis appris"
                 % (pct(l2["acc_zero"]), pct(l2["acc_head"])))
        L.append("  Vraies intrusions qui auraient ete filtrees : %d -> %d ; fausses alertes : %d -> %d"
                 % (l2["miss_zero"], l2["miss_head"], l2["fa_zero"], l2["fa_head"]))
        for k, (z, h, n) in sorted(l2["per_class"].items(), key=lambda x: -x[1][2]):
            L.append("    %-9s %s -> %s (%d essais)" % (k, pct(z), pct(h), n))
        if installed_head:
            L.append("  => " + ("SERAIT INSTALLE (essai a blanc)" if dry else "INSTALLE (version %s), pris en compte sous 30 s" % installed_head))
        else:
            L.append("  => Non installe : " + l2.get("refused", "") + ".")
    L.append("")
    L.append("NIVEAU 1 - le seuil d'alerte (personne / vehicule au-dessus duquel l'alerte part)")
    if l1.get("skip"):
        L.append("  Pas encore : " + l1["skip"] + ". Seuil inchange (%s)." % pct(l1["current"]))
    else:
        L.append("  %d reponses : %d vraies intrusions, %d fausses." % (l1["n"], l1["real"], l1["false"]))
        L.append("  Seuil   intrusions ratees   fausses alertes")
        for t, miss, fa, _ in l1["table"]:
            mark = " <= actuel" if abs(t - l1["current"]) < 1e-9 else ""
            mark += " <= choisi" if new_conf is not None and abs(t - new_conf) < 1e-9 else ""
            L.append("  %5s   %17d   %15d%s" % (pct(t), miss, fa, mark))
        if new_conf is not None:
            L.append("  => " + ("SERAIT CHANGE" if dry else "CHANGE") + " : %s -> %s (une intrusion ratee compte comme %d fausses alertes)"
                     % (pct(l1["current"]), pct(new_conf), MISS_COST))
        else:
            L.append("  => Seuil garde a %s : c'est deja le meilleur dans la plage autorisee." % pct(l1["current"]))
    L.append("")
    L.append("Revenir en arriere : python scripts\\intrusion_training.py --rollback (avis precedent) ;")
    L.append("le seuil se remet dans cockpit_settings detection_ai_filter.min_conf.")
    return L


def send_mail(subject, lines):
    host = (os.getenv("SMTP_HOST", "192.168.254.2") or "").strip()
    port = int(os.getenv("SMTP_PORT", "25"))
    sender = (os.getenv("SMTP_FROM", "safe@lemans.org") or "").strip()
    name = (os.getenv("SMTP_FROM_NAME", "TITAN ACO") or "").strip()
    msg = MIMEMultipart("alternative")
    msg["From"] = "%s <%s>" % (name, sender)
    msg["To"] = ", ".join(MAIL_TO)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg["X-Mailer"] = "TITAN Cockpit Entrainement IA"
    text = "\n".join(lines)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText("<pre style=\"font:13px Consolas,monospace\">%s</pre>" % html.escape(text), "html", "utf-8"))
    with smtplib.SMTP(host, port, timeout=int(os.getenv("SMTP_TIMEOUT", "10"))) as smtp:
        smtp.ehlo()
        if os.getenv("SMTP_USE_TLS", "false").strip().lower() in {"1", "true", "yes", "on"}:
            smtp.starttls()
            smtp.ehlo()
        user, pw = os.getenv("SMTP_USER", "").strip(), os.getenv("SMTP_PASSWORD", "").strip()
        if user and pw:
            smtp.login(user, pw)
        smtp.send_message(msg, from_addr=sender, to_addrs=MAIL_TO)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    _setup_logging()
    if a.rollback:
        prev = HEAD_PATH.replace(".json", ".prev.json")
        if os.path.exists(prev):
            os.replace(prev, HEAD_PATH)
            log.info("Avis precedent remis en place")
        elif os.path.exists(HEAD_PATH):
            os.replace(HEAD_PATH, HEAD_PATH.replace(".json", ".off.json"))
            log.info("Avis appris retire : phrases d'origine")
        return 0
    if _acquire_lock() is None:
        log.info("Deja en cours")
        return 0
    db = _get_db()
    settings = db["cockpit_settings"]
    state = settings.find_one({"_id": "detection_ai_training"}) or {}
    min_conf = float((settings.find_one({"_id": "detection_ai_filter"}) or {}).get("min_conf") or 0.3)

    samples = collect(db)
    log.info("%d reponses utilisables", len(samples))
    n_truth, n_total = sum(1 for s in samples if s["truth"]), len(samples)
    # Reponses nouvelles depuis la derniere analyse
    state["new_truth"] = n_truth - state.get("seen_truth", 0)
    state["new_total"] = n_total - state.get("seen_total", 0)
    ready = a.force or (n_truth >= L2_MIN and state["new_truth"] >= L2_MIN_NEW) \
        or (n_total >= L1_MIN and state["new_total"] >= L1_MIN_NEW)
    now = datetime.now(timezone.utc)
    if not ready:
        log.info("Pas assez de reponses nouvelles : rien a faire")
        last = state.get("last_progress_mail")
        if datetime.now().weekday() == 0 and not a.dry_run and not a.no_mail \
                and (not last or (now - last.replace(tzinfo=timezone.utc)).days >= 6):
            lines = ["Pas encore d'entrainement : il faut plus de reponses.", "",
                     "Reponses precises (pilules) : %d / %d pour l'avis de l'IA (niveau 2)" % (n_truth, L2_MIN),
                     "Reponses au total : %d / %d pour le seuil d'alerte (niveau 1)" % (len(samples), L1_MIN), "",
                     "Le plus utile : repondre photo par photo dans les fiches, en commencant",
                     "par les intrusions \"A verifier\" et le mode Revoir de la galerie."]
            try:
                send_mail("Cockpit IA intrusions : point d'avancement", lines)
                settings.update_one({"_id": "detection_ai_training"}, {"$set": {"last_progress_mail": now}}, upsert=True)
            except Exception as e:
                log.warning("Mail d'avancement non envoye : %s", e)
        return 0

    samples, F, keys, T, scale = embed(samples)
    log.info("%d empreintes calculees", len(samples))
    l2, head = level2(samples, F, keys, T, scale, min_conf, state, a.force)
    W, b, cur = load_head()
    if head:
        W, b = torch.tensor(head["W"]), torch.tensor(head["b"])
    elif cur and cur.get("keys") != keys:
        W = b = None
    l1 = level1(samples, F, keys, T, scale, W, b, min_conf, state, a.force)
    new_conf = l1["best"] if l1.get("changed") else None

    if not a.dry_run:
        if head:
            install_head(head)
            log.info("Avis appris installe : %s", head["version"])
        if new_conf is not None:
            settings.update_one({"_id": "detection_ai_filter"}, {"$set": {
                "min_conf": new_conf, "min_conf_by": "entrainement de nuit", "min_conf_at": now,
                "min_conf_prev": min_conf}}, upsert=True)
            log.info("Seuil change : %.2f -> %.2f", min_conf, new_conf)
        # Chaque niveau ne repart de zero que s'il a vraiment ete analyse
        upd = {"last_run": now}
        if not l2.get("skip"):
            upd["seen_truth"] = n_truth
        if not l1.get("skip"):
            upd["seen_total"] = n_total
        if head:
            upd["l2_acc"] = l2["acc_head"]
        settings.update_one({"_id": "detection_ai_training"}, {
            "$set": upd, "$push": {"history": {"$each": [{"at": now, "l2": {k: v for k, v in l2.items() if k != "per_class"},
                                                           "l1": {k: v for k, v in l1.items() if k != "table"},
                                                           "head": head and head["version"], "min_conf": new_conf}],
                                               "$slice": -60}}}, upsert=True)
    lines = report_lines(l2, l1, head and head["version"], new_conf, len(samples), a.dry_run)
    for ln in lines:
        log.info(ln)
    if not a.dry_run and not a.no_mail:
        changed = []
        if head:
            changed.append("avis appris installe")
        if new_conf is not None:
            changed.append("seuil %s" % pct(new_conf))
        subject = "Cockpit IA intrusions : rapport d'entrainement du %s%s" % (
            datetime.now().strftime("%d/%m"), (" - " + ", ".join(changed)) if changed else " - rien de change")
        try:
            send_mail(subject, lines)
        except Exception as e:
            log.warning("Rapport non envoye : %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
