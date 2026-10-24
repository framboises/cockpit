# cameras_dossier.py -- Dossier de planification des travaux des cameras en projet
#
# PDF A4 paysage (reportlab), charte des dossiers de plans de la Direction des
# Operations (Barlow + Exo2, bleu marine, cartouche en bas a droite, planches
# numerotees) : couverture, plan d'ensemble, une planche par emplacement (vue
# micro et vue macro sur orthophoto IGN, angles de vue, GPS WGS84 et Lambert
# 93, support de pose, modele, cameras remplacees ou deplacees, reemploi),
# recapitulatif et quantitatif, fiches techniques des modeles en annexe
# (facultatif). Donnees : cameras_planned (page Cameras, mode Projet),
# catalogue cameras_models et positions des cameras existantes
# (detection._device_position).
# Regles : docs/claude/cameras.md, section "Dossier de planification".
import io
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw

import cameras_models
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(ROOT, "static", "fonts", "dossier")
LOGO_PATH = os.path.join(ROOT, "static", "img", "ACO-logo-monochrome-universel-blanc.png")
TILE_DIR = os.path.join(ROOT, "uploads", "tiles_cache", "ign_ortho")
TILE_URL = ("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
            "&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}")
TILE_MAX_Z = 19
TILE_TTL_DAYS = 180
TILE_TIMEOUT_S = 8
TILE_WORKERS = 8
PARIS = ZoneInfo("Europe/Paris")

# Charte (relevee sur le dossier Porte du Houx, indice A)
NAVY = (0x13 / 255, 0x1E / 255, 0x41 / 255)
LOGO_BG = (0x1C, 0x20, 0x4A)
BLUE = (0x09 / 255, 0x71 / 255, 0xCE / 255)
TEAL = (0x1F / 255, 0x9E / 255, 0x89 / 255)
RED = (0xE7 / 255, 0x4C / 255, 0x3C / 255)
ORANGE = (0xE4 / 255, 0x81 / 255, 0x2A / 255)
GREY_BG = (0xE8 / 255, 0xEB / 255, 0xEE / 255)
GREY_ROW = (0xF1 / 255, 0xF3 / 255, 0xF5 / 255)
MUTED = (0x8A / 255, 0x8F / 255, 0xA3 / 255)
LINE = (0xC3 / 255, 0xC8 / 255, 0xD0 / 255)
WHITE = (1, 1, 1)
PLANNED = (0x8B / 255, 0x5C / 255, 0xF6 / 255)   # violet des cameras en projet (page Cameras)
EXISTING = (0x94 / 255, 0xA3 / 255, 0xB8 / 255)

PAGE_W, PAGE_H = 841.89, 595.28  # A4 paysage en points
MARGIN = 28

STATUS_LABELS = {"souhaitee": "Souhaitée", "validee": "Validée", "commandee": "Commandée"}
MOUNT_LABELS = {"mat": "Mât", "angle_mur": "Angle de mur", "mur_plat": "Mur plat"}
OPTICS_LABELS = {"mobile": "Mobile (PTZ)", "fixe": "Fixe"}

_fonts_lock = threading.Lock()
_fonts_ok = False
_logo_cache = {}


# ---------------------------------------------------------------------------
# Geodesie
# ---------------------------------------------------------------------------
def lambert93(lat, lng):
    """WGS84 (assimile au RGF93) -> Lambert 93 (X, Y en metres), formules IGN
    (projection conique conforme, GRS80)."""
    e = 0.0818191910428158
    n = 0.7256077650532670
    c = 11754255.4261
    xs, ys = 700000.0, 12655612.0499
    phi = math.radians(lat)
    lat_iso = math.log(math.tan(math.pi / 4 + phi / 2) * ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2))
    r = c * math.exp(-n * lat_iso)
    gamma = n * math.radians(lng - 3.0)
    return xs + r * math.sin(gamma), ys - r * math.cos(gamma)


def dms(value, pos, neg):
    v = abs(value)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60.0) * 3600
    return "%d°%02d'%04.1f\" %s" % (d, m, s, pos if value >= 0 else neg)


def dest_point(lat, lng, az, dist):
    """Point a `dist` metres dans la direction `az` (degres depuis le nord)."""
    r = 6371000.0
    br, p1, l1 = math.radians(az), math.radians(lat), math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(dist / r) + math.cos(p1) * math.sin(dist / r) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(dist / r) * math.cos(p1), math.cos(dist / r) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def distance_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cardinal(az):
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    return names[int((az % 360) / 22.5 + 0.5) % 16]


def cone_points(lat, lng, view, steps=24):
    fov, rng, az = float(view["fov"]), float(view["range"]), float(view["az"])
    if fov >= 360:
        return [dest_point(lat, lng, i * 360.0 / 48, rng) for i in range(48)]
    pts = [(lat, lng)]
    for i in range(steps + 1):
        pts.append(dest_point(lat, lng, az - fov / 2 + fov * i / steps, rng))
    return pts


# ---------------------------------------------------------------------------
# Fond de carte : tuiles IGN (Web Mercator), cache disque
# ---------------------------------------------------------------------------
def _world_px(lat, lng, z):
    s = 256 * 2 ** z
    x = (lng + 180) / 360 * s
    siny = min(max(math.sin(math.radians(lat)), -0.9999), 0.9999)
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * s
    return x, y


def meters_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / 2 ** z


def _tile(z, x, y):
    path = os.path.join(TILE_DIR, str(z), str(x), "%d.jpg" % y)
    try:
        if os.path.isfile(path) and (datetime.now().timestamp() - os.path.getmtime(path)) < TILE_TTL_DAYS * 86400:
            return Image.open(path).convert("RGB")
    except OSError:
        pass
    try:
        r = requests.get(TILE_URL.format(z=z, x=x, y=y), timeout=TILE_TIMEOUT_S,
                         headers={"User-Agent": "Cockpit-ACO dossier cameras"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path, "JPEG", quality=88)
        return img
    except Exception:
        return None


class MapView:
    """Fenetre de carte : centre, zoom, taille en pixels ; image de fond et
    projection (lat, lng) -> pixels de l'image."""

    def __init__(self, lat, lng, z, w_px, h_px):
        self.lat, self.lng, self.z, self.w, self.h = lat, lng, z, int(w_px), int(h_px)
        cx, cy = _world_px(lat, lng, z)
        self.x0, self.y0 = cx - self.w / 2, cy - self.h / 2
        self.image = None
        self.missing = 0

    def reader(self):
        """Fond de carte en JPEG (embarque tel quel dans le PDF : 10 fois plus
        leger que l'image brute)."""
        if getattr(self, "_jpeg", None) is None:
            buf = io.BytesIO()
            self.image.save(buf, "JPEG", quality=82, optimize=True)
            buf.seek(0)
            self._jpeg = ImageReader(buf)
        return self._jpeg

    def px(self, lat, lng):
        x, y = _world_px(lat, lng, self.z)
        return x - self.x0, y - self.y0

    def tiles(self):
        n = 2 ** self.z
        return [(self.z, tx, ty)
                for tx in range(int(self.x0 // 256), int((self.x0 + self.w) // 256) + 1)
                for ty in range(int(self.y0 // 256), int((self.y0 + self.h) // 256) + 1)
                if 0 <= ty < n]

    def build(self, tiles):
        img = Image.new("RGB", (self.w, self.h), (214, 219, 224))
        for (z, tx, ty) in self.tiles():
            t = tiles.get((z, tx % 2 ** z, ty))
            if t is None:
                self.missing += 1
                continue
            img.paste(t, (int(round(tx * 256 - self.x0)), int(round(ty * 256 - self.y0))))
        if self.missing:
            d = ImageDraw.Draw(img)
            d.text((8, self.h - 18), "Fond de carte partiellement indisponible", fill=(90, 90, 90))
        self.image = img
        return self


def fit_view(points, w_px, h_px, min_extent_m=40, max_z=TILE_MAX_Z, min_z=8, pad=0.12):
    """Vue cadrant tous les points (avec marge), zoom entier le plus fort possible."""
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    lat_c, lng_c = (min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2
    for z in range(max_z, min_z - 1, -1):
        xs, ys = zip(*[_world_px(a, b, z) for a, b in points])
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        mpp = meters_per_px(lat_c, z)
        need_x = max(span_x, min_extent_m / mpp) * (1 + 2 * pad)
        need_y = max(span_y, min_extent_m / mpp) * (1 + 2 * pad)
        if need_x <= w_px and need_y <= h_px:
            return MapView(lat_c, lng_c, z, w_px, h_px)
    return MapView(lat_c, lng_c, min_z, w_px, h_px)


def fetch_tiles(views):
    """Telecharge (ou relit en cache) toutes les tuiles des vues, en parallele."""
    keys = sorted({k for v in views for k in v.tiles()})
    keys = [(z, x % 2 ** z, y) for (z, x, y) in keys]
    with ThreadPoolExecutor(max_workers=TILE_WORKERS) as pool:
        imgs = list(pool.map(lambda k: _tile(*k), keys))
    tiles = dict(zip(keys, imgs))
    for v in views:
        v.build(tiles)
    return views


# ---------------------------------------------------------------------------
# Donnees
# ---------------------------------------------------------------------------
def collect(db, statuses=None, ids=None):
    """Projets retenus, tries par priorite (sans priorite : a la fin, par nom),
    numerotes C1..Cn, avec les cameras existantes citees et leur position, le
    modele et l'objectif de chaque canal (catalogue cameras_models) et le
    reemploi : camera remplacee par un projet et reinstallee par un autre."""
    import detection
    models = cameras_models.catalogue(db)
    lidx = cameras_models.lens_index(models)
    mby = {m["_id"]: m for m in models}
    q = {}
    if statuses:
        q["status"] = {"$in": list(statuses)}
    if ids:
        q["_id"] = {"$in": list(ids)}
    planned = list(db["cameras_planned"].find(q))
    planned.sort(key=lambda d: (d.get("priority") is None, d.get("priority") or 0, (d.get("name") or "").lower()))
    existing = {}
    for d in db["cockpit_cameras"].find({}, {"name": 1, "ip": 1, "map": 1, "channels": 1, "model": 1, "enabled": 1}):
        pos, _src, _geo = detection._device_position(db, d)
        existing[d["_id"]] = {
            "_id": d["_id"], "name": d.get("name", ""), "ip": d.get("ip", ""), "model": d.get("model", ""),
            "enabled": d.get("enabled", True) is not False,
            "lat": pos["lat"] if pos else None, "lng": pos["lng"] if pos else None,
            "views": [c["view"] for c in d.get("channels") or []
                      if c.get("enabled", True) is not False and isinstance(c.get("view"), dict) and c["view"].get("az") is not None],
        }
    # Reemploi : tous les projets (y compris hors du dossier) ; repere Cn si retenu
    reps = {d["_id"]: "C%d" % i for i, d in enumerate(planned, 1)}
    removed_by, reused_by = {}, {}
    for p in db["cameras_planned"].find({}, {"name": 1, "replaces": 1, "moves_camera_id": 1}):
        ref = {"_id": p["_id"], "name": p.get("name", ""), "rep": reps.get(p["_id"])}
        for o in p.get("replaces") or []:
            removed_by[o] = ref
        if p.get("moves_camera_id"):
            reused_by[p["moves_camera_id"]] = ref
    gone = {"name": "Caméra supprimée", "ip": "", "model": "", "lat": None, "lng": None, "views": []}

    def ref_of(o):
        return dict(existing.get(o) or dict(gone, _id=o), removed_by=removed_by.get(o), reused_by=reused_by.get(o))

    items = []
    for i, d in enumerate(planned, 1):
        chans = d.get("channels") or [dict({"no": 1}, **{k: d[k] for k in ("optics", "view") if d.get(k)})]
        moves = ref_of(d["moves_camera_id"]) if d.get("moves_camera_id") else None
        replaces = [ref_of(o) for o in d.get("replaces") or []]
        kind = "deplacement" if moves else "remplacement" if replaces else "nouvelle"
        out = []
        for j, c in enumerate(chans):
            k = cameras_models.lens_key(models, c, (moves or {}).get("model", ""), j, fallback=not moves, idx=lidx)
            m, lens = lidx.get(k, (None, None))
            out.append(dict(c, lens=k, lens_model=m, lens_obj=lens))
        items.append({
            "rep": "C%d" % i, "doc": d, "name": d.get("name", ""), "lat": d.get("lat"), "lng": d.get("lng"),
            "status": d.get("status", "souhaitee"), "priority": d.get("priority"), "note": d.get("note", ""),
            "mount": d.get("mount") or "", "mount_height": d.get("mount_height"),
            "model": mby.get(d.get("model_id")) if d.get("model_id") else None,
            "channels": out, "moves": moves, "replaces": replaces, "kind": kind,
        })
    return items, existing


def _proj_ref(ref):
    """'C3' (projet du dossier) ou 'le projet <nom>' (hors dossier)"""
    return ref["rep"] or "le projet « %s » (hors dossier)" % ref["name"]


def kind_label(it):
    if it["kind"] == "deplacement":
        rb = (it["moves"] or {}).get("removed_by")
        if rb:
            return "Réemploi d'une caméra existante déposée par %s" % _proj_ref(rb)
        return "Déplacement d'une caméra existante"
    if it["kind"] == "remplacement":
        return "Nouvelle caméra – remplace %d existante%s" % (len(it["replaces"]), "s" if len(it["replaces"]) > 1 else "")
    return "Nouvelle caméra"


def _device_rows(it):
    """[(libelle, modele ou None)] des appareils a commander pour un
    emplacement (un deplacement : aucun). Multi-capteurs : un appareil par
    disposition complete (1 PTZ + 4 fixes = 1 appareil ; 10 canaux = 2)."""
    if it["kind"] == "deplacement":
        return []
    out, multi = [], {}
    for ch in it["channels"]:
        m, lens = ch.get("lens_model"), ch.get("lens_obj")
        if not m:
            pm = it.get("model")
            out.append((cameras_models.device_label(pm), pm) if pm else ("Caméra (modèle et optique à définir)", None))
        elif m.get("multi"):
            if m["_id"] not in multi:
                multi[m["_id"]] = {"m": m, "n": {}, "at": len(out)}
                out.append(None)  # place reservee : ordre d'apparition
            multi[m["_id"]]["n"][lens["k"]] = multi[m["_id"]]["n"].get(lens["k"], 0) + 1
        else:
            out.append((cameras_models.device_label(m, lens), m))
    for g in sorted(multi.values(), key=lambda g: -g["at"]):
        per = {x["k"]: max(1, int(x.get("count") or 1)) for x in g["m"].get("lenses") or []}
        n = max(-(-cnt // per.get(k, 1)) for k, cnt in g["n"].items())
        out[g["at"]:g["at"] + 1] = [(cameras_models.device_label(g["m"]), g["m"])] * n
    return out


def devices_of(it):
    """Libelles des appareils a commander pour un emplacement."""
    return [lab for lab, _m in _device_rows(it)]


def models_of(it):
    """Modeles du catalogue de l'emplacement : appareils a commander, sinon
    modele choisi pour l'emplacement."""
    out = {}
    for _lab, m in _device_rows(it):
        if m is not None:
            out.setdefault(m["_id"], m)
    if it.get("model") and it["kind"] != "deplacement":
        out.setdefault(it["model"]["_id"], it["model"])
    return list(out.values())


# ---------------------------------------------------------------------------
# Rendu : polices, logo, primitives de la charte
# ---------------------------------------------------------------------------
def _register_fonts():
    global _fonts_ok
    with _fonts_lock:
        if _fonts_ok:
            return
        for name in ("Barlow-Regular", "Barlow-Medium", "Barlow-SemiBold", "Barlow-Bold", "Barlow-Italic", "Exo2-BoldItalic"):
            pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, name + ".ttf")))
        _fonts_ok = True


def _logo(size_px=400):
    """Logo ACO blanc sur carre bleu marine arrondi (comme le dossier modele)."""
    if size_px in _logo_cache:
        return _logo_cache[size_px]
    s = size_px
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.2), fill=LOGO_BG + (255,))
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo = logo.crop(logo.getbbox())  # le PNG source a une large marge transparente
        inner = int(s * 0.8)
        logo.thumbnail((inner, inner), Image.LANCZOS)
        tile.alpha_composite(logo, ((s - logo.width) // 2, (s - logo.height) // 2))
    except OSError:
        pass
    _logo_cache[size_px] = ImageReader(tile)
    return _logo_cache[size_px]


class Doc:
    def __init__(self, opts):
        self.o = opts
        self.buf = io.BytesIO()
        self.c = rl_canvas.Canvas(self.buf, pagesize=(PAGE_W, PAGE_H))
        self.c.setTitle(opts["title"])
        self.c.setAuthor(opts["author_line"])
        self.c.setSubject("Dossier de planification des travaux – caméras en projet")
        self.c.setCreator("Cockpit – ACO")
        self.pages = []  # (titre de planche, echelle)
        self.page_no = 0
        self.total = 0

    # --- texte ---
    def text(self, x, y, s, font="Barlow-Regular", size=9, color=NAVY, align="left"):
        c = self.c
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        if align == "right":
            c.drawRightString(x, y, s)
        elif align == "center":
            c.drawCentredString(x, y, s)
        else:
            c.drawString(x, y, s)

    def para(self, x, y, s, width, font="Barlow-Regular", size=8.5, color=NAVY, leading=None, max_lines=None):
        """Paragraphe a la ligne ; renvoie le y sous le dernier trait."""
        leading = leading or size * 1.35
        lines = []
        for chunk in (s or "").split("\n"):
            lines += simpleSplit(chunk, font, size, width) or [""]
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip() + " …"
        for ln in lines:
            self.text(x, y, ln, font, size, color)
            y -= leading
        return y

    def bullet(self, x, y, n, text, width, size=8.5, color=NAVY):
        c = self.c
        c.setFillColorRGB(*NAVY)
        c.circle(x + 5, y + 3, 5.2, stroke=0, fill=1)
        self.text(x + 5, y + 0.9, str(n), "Barlow-Bold", 6.5, WHITE, "center")
        return self.para(x + 16, y, text, width - 16, size=size, color=color) - 3

    def section(self, x, y, title, color=NAVY):
        self.text(x, y, title.upper(), "Barlow-Bold", 10, color)
        return y - 16

    def label(self, x, y, s):
        self.text(x, y, s, "Barlow-Bold", 8.5, BLUE)

    # --- tableau (en-tete bleu marine, lignes alternees) ---
    def table(self, x, y, cols, rows, size=7.6, row_h=13.5, head=True, bold_last=False):
        """cols : [(titre, largeur, alignement)] ; renvoie le y sous le tableau."""
        c = self.c
        width = sum(w for _, w, _ in cols)
        if head:
            c.setFillColorRGB(*NAVY)
            c.rect(x, y - row_h, width, row_h, stroke=0, fill=1)
            cx = x
            for title, w, al in cols:
                self._cell(cx, y - row_h, w, row_h, title, "Barlow-Bold", size, WHITE, al)
                cx += w
            y -= row_h
        for i, row in enumerate(rows):
            lines = [simpleSplit(str(v), "Barlow-Regular", size, w - 8) or [""] for v, (_, w, _) in zip(row, cols)]
            n = max(len(ls) for ls in lines)
            h = row_h + (n - 1) * size * 1.25
            if i % 2 == 0:
                c.setFillColorRGB(*GREY_ROW)
                c.rect(x, y - h, width, h, stroke=0, fill=1)
            cx = x
            for j, ((_, w, al), ls) in enumerate(zip(cols, lines)):
                font = "Barlow-Bold" if (j == 0 or (bold_last and j == len(cols) - 1)) else "Barlow-Regular"
                for k, ln in enumerate(ls):
                    self._cell(cx, y - row_h - k * size * 1.25, w, row_h, ln, font, size, NAVY, al)
                cx += w
            y -= h
        return y

    def _cell(self, x, y, w, h, s, font, size, color, align):
        ty = y + (h - size) / 2 + 1.5
        if align == "right":
            self.text(x + w - 5, ty, s, font, size, color, "right")
        elif align == "center":
            self.text(x + w / 2, ty, s, font, size, color, "center")
        else:
            self.text(x + 5, ty, s, font, size, color)

    # --- cadre de page, en-tete, cartouche ---
    def frame(self, title):
        c = self.c
        c.setStrokeColorRGB(*LINE)
        c.setLineWidth(0.6)
        c.roundRect(14, 18, PAGE_W - 28, PAGE_H - 32, 4, stroke=1, fill=0)
        c.drawImage(_logo(), MARGIN, PAGE_H - 70, 40, 40, mask="auto")
        c.setStrokeColorRGB(*NAVY)
        c.setLineWidth(0.8)
        c.line(MARGIN + 50, PAGE_H - 50, MARGIN + 75, PAGE_H - 50)
        self.text(MARGIN + 82, PAGE_H - 54, title.upper(), "Barlow-Bold", 11)
        tw = pdfmetrics.stringWidth(title.upper(), "Barlow-Bold", 11)
        sub = "%s · INDICE %s · %s" % (self.o["short"].upper(), self.o["indice"], self.o["month"].upper())
        self.text(MARGIN + 82 + tw + 18, PAGE_H - 54, sub, "Barlow-Regular", 6.5, MUTED)
        sw = pdfmetrics.stringWidth(sub, "Barlow-Regular", 6.5)
        c.line(MARGIN + 82 + tw + 18 + sw + 12, PAGE_H - 50, PAGE_W - MARGIN - 10, PAGE_H - 50)
        self._swoosh()

    def _swoosh(self):
        c = self.c
        p = c.beginPath()
        p.moveTo(PAGE_W * 0.54, 0)
        p.lineTo(PAGE_W, 0)
        p.lineTo(PAGE_W, 22)
        p.close()
        c.setFillColorRGB(*GREY_BG)
        c.drawPath(p, stroke=0, fill=1)
        p = c.beginPath()
        p.moveTo(PAGE_W * 0.54, 0)
        p.lineTo(PAGE_W, 0)
        p.lineTo(PAGE_W, 12)
        p.close()
        c.setFillColorRGB(*NAVY)
        c.drawPath(p, stroke=0, fill=1)

    def cartouche(self, planche_title, scale):
        c = self.c
        x, y, w, h = PAGE_W - MARGIN - 300, 34, 300, 86
        c.setFillColorRGB(*WHITE)
        c.setStrokeColorRGB(*NAVY)
        c.setLineWidth(0.8)
        c.rect(x, y, w, h, stroke=1, fill=1)
        c.drawImage(_logo(), x + 8, y + 12, 60, 60, mask="auto")
        tx = x + 76
        self.text(tx, y + h - 16, self.o["title"].upper()[:58], "Barlow-Bold", 7.6)
        self.text(tx, y + h - 27, self.o["subtitle"][:80], "Barlow-Regular", 5.6, MUTED)
        self.text(tx, y + h - 43, planche_title.upper()[:40], "Exo2-BoldItalic", 9.5, BLUE)
        self.text(tx, y + h - 57, "Échelle : %s  ·  %s  ·  Indice %s – %s" % (scale, self.o["date"], self.o["indice"], self.o["phase"]),
                  "Barlow-Regular", 6.2)
        self.para(tx, y + h - 70, "Établi par %s · non contractuel, à valider par le service technique" % self.o["author_line"],
                  w - 76 - 52, size=5, color=MUTED, leading=6.2, max_lines=2)
        c.setFillColorRGB(*NAVY)
        c.circle(x + w - 28, y + h / 2, 22, stroke=0, fill=1)
        self.text(x + w - 28, y + h / 2 + 1, "%d/%d" % (self.page_no, self.total), "Barlow-Bold", 10.5, WHITE, "center")
        self.text(x + w - 28, y + h / 2 - 9, "PLANCHE", "Barlow-Bold", 4.2, WHITE, "center")

    def new_page(self):
        if self.page_no:
            self.c.showPage()
        self.page_no += 1

    # --- carte ---
    def map(self, view, x, y, w, h, draw, north=True, scale=True):
        """Image de fond + surcharges vectorielles. draw(proj) dessine avec
        proj(lat, lng) -> (x, y) en points PDF."""
        c = self.c
        c.saveState()
        p = c.beginPath()
        p.rect(x, y, w, h)
        c.clipPath(p, stroke=0, fill=0)
        c.drawImage(view.reader(), x, y, w, h)
        k = w / view.w

        def proj(lat, lng):
            px, py = view.px(lat, lng)
            return x + px * k, y + h - py * k
        draw(proj, k)
        c.restoreState()
        c.setStrokeColorRGB(*LINE)
        c.setLineWidth(0.6)
        c.rect(x, y, w, h, stroke=1, fill=0)
        if north:
            self._north(x + w - 26, y + h - 40)
        if scale:
            self._scalebar(view, x + 10, y + 10, w, k)
        self.text(x + w - 4, y + 3, "© IGN – Géoplateforme, orthophotographie", "Barlow-Regular", 4.6, WHITE, "right")

    def _north(self, x, y):
        c = self.c
        c.setFillColorRGB(1, 1, 1)
        c.setFillAlpha(0.88)
        c.roundRect(x - 2, y - 2, 20, 32, 2, stroke=0, fill=1)
        c.setFillAlpha(1)
        p = c.beginPath()
        p.moveTo(x + 8, y + 26)
        p.lineTo(x + 3, y + 8)
        p.lineTo(x + 8, y + 12)
        p.lineTo(x + 13, y + 8)
        p.close()
        c.setFillColorRGB(*NAVY)
        c.drawPath(p, stroke=0, fill=1)
        self.text(x + 8, y + 1, "N", "Barlow-Bold", 6, NAVY, "center")

    def _scalebar(self, view, x, y, w, k):
        mpp = meters_per_px(view.lat, view.z) / k  # metres par point PDF
        target = w * 0.2 * mpp
        nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]
        L = max(n for n in nice if n <= max(target, 1))
        bar = L / mpp
        c = self.c
        c.setFillColorRGB(1, 1, 1)
        c.setFillAlpha(0.88)
        c.rect(x - 4, y - 4, bar + 46, 16, stroke=0, fill=1)
        c.setFillAlpha(1)
        c.setFillColorRGB(*NAVY)
        c.rect(x, y + 2, bar / 2, 3, stroke=0, fill=1)
        c.setStrokeColorRGB(*NAVY)
        c.setLineWidth(0.6)
        c.rect(x, y + 2, bar, 3, stroke=1, fill=0)
        self.text(x + bar + 4, y + 1.5, "%s m" % L if L < 1000 else "%g km" % (L / 1000), "Barlow-Bold", 6.5)


def scale_label(view, w_pt):
    """Echelle approchee du plan imprime (A4) : 1/xxx arrondi."""
    m_per_pt = meters_per_px(view.lat, view.z) * view.w / w_pt
    ratio = m_per_pt / 0.000352778
    for nice in (100, 200, 250, 500, 750, 1000, 1500, 2000, 2500, 5000, 7500, 10000, 15000, 20000, 25000, 50000, 100000):
        if ratio <= nice * 1.15:
            return "≈ 1/%s" % format(nice, ",").replace(",", " ")
    return "≈ 1/%d" % round(ratio, -3)


# ---------------------------------------------------------------------------
# Surcharges cartographiques
# ---------------------------------------------------------------------------
def _poly(c, pts, stroke, fill, alpha, width=0.8, dash=None):
    p = c.beginPath()
    p.moveTo(*pts[0])
    for q in pts[1:]:
        p.lineTo(*q)
    p.close()
    c.setFillColorRGB(*fill)
    c.setFillAlpha(alpha)
    c.setStrokeColorRGB(*stroke)
    c.setStrokeAlpha(0.95)
    c.setLineWidth(width)
    c.setDash(*(dash or ([], 0)))
    c.drawPath(p, stroke=1, fill=1)
    c.setFillAlpha(1)
    c.setStrokeAlpha(1)
    c.setDash([], 0)


def draw_cone(c, proj, lat, lng, view, color, alpha=0.22, dash=None, width=0.8):
    _poly(c, [proj(a, b) for a, b in cone_points(lat, lng, view)], color, color, alpha, width, dash)


def draw_pin(d, proj, lat, lng, label, color=PLANNED, r=8, font_size=7):
    c = d.c
    x, y = proj(lat, lng)
    c.setFillColorRGB(*WHITE)
    c.circle(x, y, r + 1.4, stroke=0, fill=1)
    c.setFillColorRGB(*color)
    c.circle(x, y, r, stroke=0, fill=1)
    d.text(x, y - font_size * 0.35, label, "Barlow-Bold", font_size, WHITE, "center")


def draw_existing(d, proj, cam, with_cones=True, label=True):
    c = d.c
    if cam.get("lat") is None:
        return
    if with_cones:
        for v in cam["views"]:
            draw_cone(c, proj, cam["lat"], cam["lng"], v, EXISTING, alpha=0.12, width=0.4)
    x, y = proj(cam["lat"], cam["lng"])
    c.setFillColorRGB(*WHITE)
    c.circle(x, y, 3.6, stroke=0, fill=1)
    c.setFillColorRGB(*EXISTING)
    c.circle(x, y, 2.6, stroke=0, fill=1)
    if label:
        d.text(x + 5, y - 2, cam["name"][:28], "Barlow-Medium", 4.8, WHITE)


def draw_removed(d, proj, cam):
    """Camera existante remplacee (deposee) : croix rouge."""
    if cam.get("lat") is None:
        return
    c = d.c
    x, y = proj(cam["lat"], cam["lng"])
    c.setFillColorRGB(*WHITE)
    c.circle(x, y, 6.5, stroke=0, fill=1)
    c.setStrokeColorRGB(*RED)
    c.setLineWidth(1.6)
    c.line(x - 4, y - 4, x + 4, y + 4)
    c.line(x - 4, y + 4, x + 4, y - 4)


def draw_move(d, proj, old, new):
    """Fleche pointillee ancienne position -> nouvelle."""
    if old.get("lat") is None:
        return
    c = d.c
    x1, y1 = proj(old["lat"], old["lng"])
    x2, y2 = proj(new[0], new[1])
    ln = math.hypot(x2 - x1, y2 - y1)
    if ln < 4:
        return
    c.setStrokeColorRGB(*ORANGE)
    c.setLineWidth(1.4)
    c.setDash([3, 2], 0)
    ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
    ex, ey = x2 - ux * 10, y2 - uy * 10
    c.line(x1, y1, ex, ey)
    c.setDash([], 0)
    p = c.beginPath()
    p.moveTo(ex + ux * 5, ey + uy * 5)
    p.lineTo(ex - uy * 3.5, ey + ux * 3.5)
    p.lineTo(ex + uy * 3.5, ey - ux * 3.5)
    p.close()
    c.setFillColorRGB(*ORANGE)
    c.drawPath(p, stroke=0, fill=1)
    c.setFillColorRGB(*WHITE)
    c.circle(x1, y1, 4.5, stroke=0, fill=1)
    c.setStrokeColorRGB(*ORANGE)
    c.setLineWidth(1.2)
    c.circle(x1, y1, 3.2, stroke=1, fill=0)


def _in_view(view, lat, lng, margin=40):
    x, y = view.px(lat, lng)
    return -margin <= x <= view.w + margin and -margin <= y <= view.h + margin


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
MICRO = (468, 262)   # taille en points des cartes de la planche emplacement
MACRO = (226, 150)
OVERVIEW = (470, 418)
COVER_MAP = (360, 220)
PX = 2.2             # pixels de fond de carte par point PDF
MICRO_MAX_REF_M = 120  # camera remplacee / deplacee plus loin : hors cadrage de la vue micro


def _views_for(items, existing):
    """Prepare toutes les vues (une seule passe de telechargement des tuiles)."""
    views = {}
    pts = [(it["lat"], it["lng"]) for it in items]
    pts += [(r["lat"], r["lng"]) for it in items for r in it["replaces"] + ([it["moves"]] if it["moves"] else []) if r.get("lat") is not None]
    views["overview"] = fit_view(pts, OVERVIEW[0] * PX, OVERVIEW[1] * PX, min_extent_m=200, max_z=18)
    views["cover"] = fit_view(pts, COVER_MAP[0] * PX, COVER_MAP[1] * PX, min_extent_m=200, max_z=18)
    for it in items:
        lat, lng = it["lat"], it["lng"]
        ext = [(lat, lng)]
        for ch in it["channels"]:
            if ch.get("view"):
                ext += cone_points(lat, lng, ch["view"], steps=8)
        for r in it["replaces"] + ([it["moves"]] if it["moves"] else []):
            # Au-dela, la vue micro deviendrait illisible : la vue macro la montre
            if r.get("lat") is not None and distance_m(lat, lng, r["lat"], r["lng"]) < MICRO_MAX_REF_M:
                ext.append((r["lat"], r["lng"]))
        views[it["rep"] + ":micro"] = fit_view(ext, MICRO[0] * PX, MICRO[1] * PX, min_extent_m=60, pad=0.08)
        # Macro : environ 600 m autour de l'emplacement
        views[it["rep"] + ":macro"] = fit_view([dest_point(lat, lng, 0, 300), dest_point(lat, lng, 180, 300),
                                                dest_point(lat, lng, 90, 300), dest_point(lat, lng, 270, 300)],
                                               MACRO[0] * PX, MACRO[1] * PX, pad=0, max_z=17)
    fetch_tiles(list(views.values()))
    return views


def page_cover(d, items, views):
    c = d.c
    d.new_page()
    p = c.beginPath()
    p.moveTo(0, 0)
    p.lineTo(0, PAGE_H)
    p.lineTo(PAGE_W * 0.5, PAGE_H)
    p.lineTo(PAGE_W * 0.22, 0)
    p.close()
    c.setFillColorRGB(*GREY_BG)
    c.drawPath(p, stroke=0, fill=1)
    c.drawImage(_logo(), MARGIN, PAGE_H - 70, 40, 40, mask="auto")
    c.setStrokeColorRGB(*NAVY)
    c.setLineWidth(0.8)
    c.line(MARGIN + 50, PAGE_H - 50, MARGIN + 75, PAGE_H - 50)
    d.text(MARGIN + 82, PAGE_H - 54, "DOSSIER DE PLANIFICATION", "Barlow-Bold", 11)
    sub = "%s · INDICE %s · %s" % (d.o["phase"].upper(), d.o["indice"], d.o["month"].upper())
    d.text(MARGIN + 270, PAGE_H - 54, sub, "Barlow-Regular", 6.5, MUTED)
    c.line(MARGIN + 270 + pdfmetrics.stringWidth(sub, "Barlow-Regular", 6.5) + 12, PAGE_H - 50, PAGE_W - MARGIN - 10, PAGE_H - 50)
    c.drawImage(_logo(), 64, PAGE_H - 255, 140, 140, mask="auto")
    y = PAGE_H - 330
    for ln in simpleSplit(d.o["title"].upper(), "Barlow-Bold", 30, 380)[:2]:
        d.text(64, y, ln, "Barlow-Bold", 30)
        y -= 32
    y -= 2
    for ln in simpleSplit(d.o["subtitle"], "Exo2-BoldItalic", 13, 380)[:2]:
        d.text(64, y, ln, "Exo2-BoldItalic", 13, BLUE)
        y -= 17
    y -= 10
    n_new = sum(1 for it in items if it["kind"] != "deplacement")
    n_move = sum(1 for it in items if it["kind"] == "deplacement")
    n_reuse = sum(1 for it in items if it["kind"] == "deplacement" and it["moves"].get("removed_by"))
    n_rep = sum(1 for it in items for r in it["replaces"] if not r.get("reused_by"))
    n_dev = sum(len(devices_of(it)) for it in items)
    lines = ["%d emplacement%s, classés par ordre de priorité (C1 à C%d)" % (len(items), "s" if len(items) > 1 else "", len(items)),
             "%d nouvelle%s caméra%s à poser (%d appareil%s)" % (n_new, "s" if n_new > 1 else "", "s" if n_new > 1 else "", n_dev, "s" if n_dev > 1 else "")]
    if n_move:
        lines.append("%d caméra%s existante%s à déplacer%s" % (n_move, "s" if n_move > 1 else "", "s" if n_move > 1 else "",
                                                             " (dont %d réemploi%s)" % (n_reuse, "s" if n_reuse > 1 else "") if n_reuse else ""))
    if n_rep:
        lines.append("%d caméra%s existante%s à déposer, retour au stock" % (n_rep, "s" if n_rep > 1 else "", "s" if n_rep > 1 else ""))
    lines.append("Statuts retenus : %s" % ", ".join(STATUS_LABELS.get(s, s) for s in d.o["statuses"]))
    for ln in lines:
        d.text(64, y, ln, "Barlow-Regular", 9.5)
        y -= 15
    y -= 22
    d.text(64, y, "Établi par %s" % d.o["author_line"], "Barlow-Regular", 8)
    d.text(64, y - 12, "Document de principe pour le service technique – positions et angles de vue issus de la carte Cockpit, "
           "implantation à relever sur site", "Barlow-Regular", 5.6, MUTED)
    # Droite : plan d'ensemble reduit + sommaire
    v = views["cover"]
    mx, mw, mh = PAGE_W * 0.53, COVER_MAP[0], COVER_MAP[1]
    my = PAGE_H - 110 - mh
    d.map(v, mx, my, mw, mh, lambda proj, k: [draw_pin(d, proj, it["lat"], it["lng"], it["rep"][1:], r=5.5, font_size=5.5)
                                                       for it in items], north=False, scale=False)
    d.text(mx + 4, my + mh - 11, "PLAN D'ENSEMBLE – EMPLACEMENTS EN PROJET", "Barlow-Bold", 5.8, WHITE)
    d.text(mx, my - 12, "Emplacements numérotés par ordre de priorité, orthophoto IGN", "Barlow-Regular", 5.6, MUTED)
    y = my - 50
    d.text(mx, y, "SOMMAIRE", "Barlow-Bold", 10)
    y -= 26
    toc = [("Plan d'ensemble", 2)]
    if items:
        toc.append(("Fiches emplacements C1 à C%d" % len(items), 3))
    toc.append(("Récapitulatif & quantitatif", 3 + len(items)))
    if d.o.get("annex_page"):
        toc.append(("Annexes – fiches techniques", d.o["annex_page"]))
    for i, (t, pg) in enumerate(toc, 1):
        c.setFillColorRGB(*NAVY)
        c.circle(mx + 6, y + 3, 6, stroke=0, fill=1)
        d.text(mx + 6, y + 0.8, str(i), "Barlow-Bold", 6.5, WHITE, "center")
        c.setStrokeColorRGB(*TEAL)
        c.setLineWidth(0.8)
        c.line(mx + 13, y + 3, mx + 38, y + 3)
        d.text(mx + 46, y, t, "Barlow-Regular", 8.5)
        d.text(mx + 330, y, "planche %d" % pg, "Barlow-Regular", 7, MUTED, "right")
        y -= 20
    d._swoosh()


def _overview_draw(d, items, existing):
    def draw(proj, k):
        c = d.c
        for it in items:
            for ch in it["channels"]:
                if ch.get("view"):
                    draw_cone(c, proj, it["lat"], it["lng"], ch["view"], PLANNED, alpha=0.18, dash=[2, 1.5], width=0.5)
        for it in items:
            for r in it["replaces"]:
                draw_removed(d, proj, r)
            if it["moves"]:
                draw_move(d, proj, it["moves"], (it["lat"], it["lng"]))
        for it in items:
            draw_pin(d, proj, it["lat"], it["lng"], it["rep"][1:], r=6.5, font_size=6)
    return draw


def legend(d, x, y, with_existing=True):
    c = d.c
    y = d.section(x, y, "Légende")
    rows = [("pin", "caméra en projet (repère C1, C2… = ordre de priorité)"),
            ("cone", "angle de vue prévu (direction, ouverture, portée DORI « observer »)")]
    if with_existing:
        rows.append(("exist", "caméra existante et son angle de vue (gris)"))
    rows += [("removed", "caméra existante remplacée : à déposer"),
             ("move", "caméra existante déplacée : de l'ancienne position vers la nouvelle")]
    for kind, txt in rows:
        cx, cy = x + 8, y + 3
        if kind == "pin":
            c.setFillColorRGB(*PLANNED)
            c.circle(cx, cy, 5, stroke=0, fill=1)
        elif kind == "cone":
            _poly(c, [(cx - 7, cy - 4), (cx + 7, cy - 4), (cx, cy + 5)], PLANNED, PLANNED, 0.25, 0.6, [2, 1.5])
        elif kind == "exist":
            c.setFillColorRGB(*EXISTING)
            c.circle(cx, cy, 3, stroke=0, fill=1)
        elif kind == "removed":
            c.setStrokeColorRGB(*RED)
            c.setLineWidth(1.4)
            c.line(cx - 4, cy - 4, cx + 4, cy + 4)
            c.line(cx - 4, cy + 4, cx + 4, cy - 4)
        elif kind == "move":
            c.setStrokeColorRGB(*ORANGE)
            c.setLineWidth(1.3)
            c.setDash([3, 2], 0)
            c.line(cx - 8, cy, cx + 6, cy)
            c.setDash([], 0)
        d.text(x + 22, y, txt, "Barlow-Regular", 7.6)
        y -= 14
    return y - 4


def page_overview(d, items, existing, views):
    d.new_page()
    d.frame("Plan d'ensemble")
    v = views["overview"]
    x, y = MARGIN + 6, PAGE_H - 90 - OVERVIEW[1]
    d.label(x, PAGE_H - 82, "Plan d'ensemble – emplacements en projet — éch. %s" % scale_label(v, OVERVIEW[0]))
    d.map(v, x, y, OVERVIEW[0], OVERVIEW[1], _overview_draw(d, items, existing))
    rx = x + OVERVIEW[0] + 26
    ry = legend(d, rx, PAGE_H - 90, with_existing=False)
    ry = d.section(rx, ry - 4, "Emplacements")
    rw = PAGE_W - MARGIN - 6 - rx
    cols = [("Rep.", 26, "left"), ("Emplacement", rw - 26 - 28 - 70 - 54, "left"), ("Prio.", 28, "center"),
            ("Type", 70, "left"), ("Statut", 54, "left")]
    rows = [(it["rep"], it["name"], it["priority"] or "–", {"nouvelle": "Nouvelle", "remplacement": "Remplacement",
                                                             "deplacement": "Déplacement"}[it["kind"]],
             STATUS_LABELS.get(it["status"], it["status"])) for it in items]
    max_rows = 12
    d.table(rx, ry, cols, rows[:max_rows], size=7.2, row_h=12.5)
    if len(rows) > max_rows:
        d.text(rx, 132, "… et %d autre(s) : voir le récapitulatif." % (len(rows) - max_rows), "Barlow-Regular", 6.5, MUTED)
    d.cartouche("Plan d'ensemble", scale_label(v, OVERVIEW[0]))


def page_item(d, it, items, existing, views):
    d.new_page()
    d.frame("%s · %s" % (it["rep"], it["name"][:60]))
    vm, vM = views[it["rep"] + ":micro"], views[it["rep"] + ":macro"]
    lat, lng = it["lat"], it["lng"]
    others = [o for o in items if o is not it]
    taken = {r["_id"] for r in it["replaces"]} | ({it["moves"]["_id"]} if it["moves"] else set())

    def draw_micro(proj, k):
        c = d.c
        for cam in existing.values():
            if cam["_id"] in taken or cam.get("lat") is None or not _in_view(vm, cam["lat"], cam["lng"]):
                continue
            draw_existing(d, proj, cam)
        for o in others:
            if _in_view(vm, o["lat"], o["lng"]):
                for ch in o["channels"]:
                    if ch.get("view"):
                        draw_cone(c, proj, o["lat"], o["lng"], ch["view"], PLANNED, alpha=0.08, dash=[2, 1.5], width=0.4)
                draw_pin(d, proj, o["lat"], o["lng"], o["rep"][1:], r=5, font_size=5)
        for r in it["replaces"]:
            for v in r.get("views") or []:
                draw_cone(c, proj, r["lat"], r["lng"], v, RED, alpha=0.08, dash=[1.5, 1.5], width=0.4)
            draw_removed(d, proj, r)
            if r.get("lat") is not None:
                x, y = proj(r["lat"], r["lng"])
                d.text(x + 8, y - 2, r["name"][:30], "Barlow-Bold", 5.2, WHITE)
        if it["moves"]:
            draw_move(d, proj, it["moves"], (lat, lng))
        for ch in it["channels"]:
            if ch.get("view"):
                draw_cone(c, proj, lat, lng, ch["view"], PLANNED, alpha=0.28, width=1.1)
                ex, ey = proj(*dest_point(lat, lng, ch["view"]["az"], ch["view"]["range"] * 0.82))
                if len(it["channels"]) > 1:
                    c.setFillColorRGB(*WHITE)
                    c.circle(ex, ey, 5, stroke=0, fill=1)
                    d.text(ex, ey - 2.2, str(ch.get("no")), "Barlow-Bold", 6, PLANNED, "center")
        draw_pin(d, proj, lat, lng, it["rep"], r=10, font_size=7.5)

    def draw_macro(proj, k):
        for o in others:
            if _in_view(vM, o["lat"], o["lng"]):
                draw_pin(d, proj, o["lat"], o["lng"], o["rep"][1:], r=4, font_size=4.5)
        for r in it["replaces"]:
            draw_removed(d, proj, r)
        if it["moves"]:
            draw_move(d, proj, it["moves"], (lat, lng))
        x, y = proj(lat, lng)
        d.c.setStrokeColorRGB(*RED)
        d.c.setLineWidth(1.4)
        d.c.circle(x, y, 13, stroke=1, fill=0)
        draw_pin(d, proj, lat, lng, it["rep"], r=6.5, font_size=5.5)

    x0 = MARGIN + 6
    top = PAGE_H - 82
    d.label(x0, top, "Vue micro – implantation et angles de vue — éch. %s" % scale_label(vm, MICRO[0]))
    d.map(vm, x0, top - 8 - MICRO[1], MICRO[0], MICRO[1], draw_micro)
    yb = top - 8 - MICRO[1] - 18
    d.label(x0, yb, "Vue macro – situation — éch. %s" % scale_label(vM, MACRO[0]))
    d.map(vM, x0, yb - 8 - MACRO[1], MACRO[0], MACRO[1], draw_macro)
    # Operation (a cote de la vue macro)
    ox, ow = x0 + MACRO[0] + 16, MICRO[0] - MACRO[0] - 16
    oy = d.section(ox, yb, "Opération")
    oy = d.para(ox, oy, kind_label(it), ow, font="Barlow-SemiBold", size=8.5) - 3
    n = 1
    if it["moves"]:
        m = it["moves"]
        txt = "Caméra %s : %s%s." % ("réemployée" if m.get("removed_by") else "déplacée", m["name"], " (%s)" % m["ip"] if m.get("ip") else "")
        if m.get("removed_by"):
            txt += " Déposée par %s (remplacée sur place), puis reposée ici." % _proj_ref(m["removed_by"])
        if m.get("lat") is not None:
            txt += " Ancienne position %.6f, %.6f – déplacement de %d m." % (m["lat"], m["lng"], round(distance_m(m["lat"], m["lng"], lat, lng)))
        oy = d.bullet(ox, oy, n, txt, ow, size=7.4)
        n += 1
    for r in it["replaces"]:
        txt = "Remplace %s%s : à déposer" % (r["name"], " (%s)" % r["ip"] if r.get("ip") else "")
        if r.get("lat") is not None:
            txt += ", à %d m" % round(distance_m(r["lat"], r["lng"], lat, lng))
        txt += ", réemployée en %s" % _proj_ref(r["reused_by"]) if r.get("reused_by") else ", retour au stock"
        oy = d.bullet(ox, oy, n, txt + ".", ow, size=7.4)
        n += 1
        if oy < 60:
            break
    if it["note"] and oy > 70:
        oy = d.section(ox, oy - 4, "Note")
        d.para(ox, oy, it["note"], ow, size=7.4, max_lines=max(1, int((oy - 40) / 10)))

    # Colonne de droite : identification, support, canaux
    rx, rw = x0 + MICRO[0] + 26, PAGE_W - MARGIN - (x0 + MICRO[0] + 26) - 6
    ry = d.section(rx, top, "Identification")
    X, Y = lambert93(lat, lng)
    mount = MOUNT_LABELS.get(it["mount"], "À définir")
    if it["mount_height"]:
        mount += " – hauteur de pose %s m" % ("%g" % it["mount_height"]).replace(".", ",")
    if it["kind"] == "deplacement":
        model = "%s (caméra existante)" % (it["moves"].get("model") or "modèle non renseigné")
    else:
        model = ", ".join(cameras_models.name(m) or "à définir" for m in models_of(it)) or "À définir"
    rows = [("Repère", "%s – priorité %s" % (it["rep"], it["priority"] or "non définie")),
            ("Statut", STATUS_LABELS.get(it["status"], it["status"])),
            ("Modèle", model),
            ("Support de pose", mount),
            ("GPS (WGS84)", "%.6f N  %.6f E" % (lat, lng)),
            ("", "%s  %s" % (dms(lat, "N", "S"), dms(lng, "E", "O"))),
            ("Lambert 93", "X %s  Y %s" % (format(round(X, 1), ",.1f").replace(",", " ").replace(".", ","),
                                          format(round(Y, 1), ",.1f").replace(",", " ").replace(".", ",")))]
    ry = d.table(rx, ry, [("", 82, "left"), ("", rw - 82, "left")], rows, size=7.4, row_h=13, head=False)
    ry = d.section(rx, ry - 14, "Canaux & angles de vue")
    cols = [("Can.", 26, "center"), ("Objectif", rw - 26 - 50 - 40 - 38, "left"), ("Direction", 50, "left"),
            ("Ouv.", 40, "right"), ("Portée", 38, "right")]
    crow = []
    for ch in it["channels"]:
        m, lens = ch.get("lens_model"), ch.get("lens_obj")
        if lens:
            lab = "%s – %s" % (m.get("ref") or cameras_models.name(m), lens.get("label") or "")
        elif it["kind"] == "deplacement" and (it["moves"] or {}).get("model"):
            lab = "%s (existante)" % it["moves"]["model"]
        elif ch.get("optics"):
            lab = "%s – objectif à définir" % OPTICS_LABELS[ch["optics"]]
        else:
            lab = "Optique et objectif à définir"
        v = ch.get("view")
        if v:
            crow.append((ch.get("no"), lab, "%d° %s" % (round(v["az"]) % 360, cardinal(v["az"])), "%d°" % round(v["fov"]), "%d m" % round(v["range"])))
        else:
            crow.append((ch.get("no"), lab, "à dessiner", "–", "–"))
    ry = d.table(rx, ry, cols, crow, size=7.2, row_h=13)
    d.text(rx, ry - 9, "Direction : azimut depuis le nord, sens horaire. Portée : distance DORI « observer » des fiches techniques.",
           "Barlow-Regular", 5.2, MUTED)
    if any(ch.get("optics") == "mobile" for ch in it["channels"]):
        d.text(rx, ry - 16, "PTZ : angle dessiné = position de repos (preset) à régler à la mise en service.", "Barlow-Regular", 5.2, MUTED)
    rows_dev = _device_rows(it)
    devs = [lab for lab, _m in rows_dev]
    if devs and ry > 190:
        ry = d.section(rx, ry - 32, "Matériel")
        annex = d.o.get("annex") or {}
        for i, dev in enumerate(sorted(set(devs)), 1):
            m = next((mm for lab, mm in rows_dev if lab == dev), None)
            ref = " – fiche technique en annexe %s" % annex[m["_id"]] if m is not None and m["_id"] in annex else ""
            ry = d.bullet(rx, ry, i, "%s%s%s" % ("%d × " % devs.count(dev) if devs.count(dev) > 1 else "", dev, ref), rw, size=7.4)
            if ry < 140:
                break
    d.cartouche("Fiche emplacement %s" % it["rep"], "%s – %s" % (scale_label(vm, MICRO[0]), scale_label(vM, MACRO[0])))


VIGILANCE = [
    "Positions et angles de vue issus de la carte Cockpit (orthophoto IGN) : implantation exacte, hauteur et orientation à relever sur site.",
    "Portées DORI « observer » des fiches techniques (63 px/m) : à confirmer par un essai de cadrage à la pose.",
    "Alimentation et réseau (PoE, fibre, 4G) à vérifier pour chaque emplacement avant commande.",
    "Mâts et ancrages : DT-DICT avant toute fouille ; massif selon la notice du mât et la prise au vent.",
    "Caméras remplacées : maintenir la couverture pendant les travaux, dépose et retour du matériel au stock.",
    "Vues sur des espaces privés : dessiner les masques de vie privée à la mise en service.",
    "Après pose : déclarer chaque caméra dans Cockpit (page Caméras) avec ses canaux, usages et angles de vue.",
]


def pages_recap(d, items):
    cols = [("Rep.", 30, "left"), ("Emplacement", 168, "left"), ("Prio.", 30, "center"), ("Type", 78, "left"),
            ("Statut", 56, "left"), ("Support", 82, "left"), ("Canaux", 120, "left"), ("Remplace / déplace", 196, "left")]
    rows = []
    for it in items:
        mount = MOUNT_LABELS.get(it["mount"], "à définir")
        if it["mount_height"]:
            mount += " %s m" % ("%g" % it["mount_height"]).replace(".", ",")
        mob = sum(1 for ch in it["channels"] if ch.get("optics") == "mobile")
        fix = sum(1 for ch in it["channels"] if ch.get("optics") == "fixe")
        chans = " + ".join(x for x in ["%d PTZ" % mob if mob else "", "%d fixe%s" % (fix, "s" if fix > 1 else "") if fix else ""] if x) or "%d" % len(it["channels"])
        rel = ", ".join(r["name"] + (" (réemploi %s)" % (r["reused_by"]["rep"] or "hors dossier") if r.get("reused_by") else "")
                        for r in it["replaces"])
        if it["moves"]:
            rb = it["moves"].get("removed_by")
            rel = ("réemploi de %s, déposée en %s" % (it["moves"]["name"], rb["rep"] or "projet hors dossier") if rb
                   else "déplace %s" % it["moves"]["name"])
        rows.append((it["rep"], it["name"], it["priority"] or "–",
                     {"nouvelle": "Nouvelle", "remplacement": "Remplacement", "deplacement": "Déplacement"}[it["kind"]],
                     STATUS_LABELS.get(it["status"], it["status"]), mount, chans, rel or "–"))
    per_page = 16
    chunks = [rows[i:i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    for n, chunk in enumerate(chunks):
        d.new_page()
        d.frame("Récapitulatif & quantitatif")
        y = d.section(MARGIN + 6, PAGE_H - 84, "Récapitulatif des emplacements" + (" (suite)" if n else ""))
        y = d.table(MARGIN + 6, y, cols, chunk, size=7, row_h=12.5)
        if n < len(chunks) - 1:
            d.cartouche("Récapitulatif", "sans échelle")
            continue
        _quantitatif(d, items, y - 18)
        d.cartouche("Récapitulatif & quantitatif", "sans échelle")


def _quantitatif(d, items, y):
    x = MARGIN + 6
    if y < 250:  # plus de place : nouvelle page
        d.cartouche("Récapitulatif", "sans échelle")
        d.new_page()
        d.frame("Récapitulatif & quantitatif")
        y = PAGE_H - 84
    ly = d.section(x, y, "Points de vigilance")
    for i, t in enumerate(VIGILANCE, 1):
        ly = d.bullet(x, ly, i, t, 400, size=7.4)
    qx = x + 430
    qy = d.section(qx, y, "Quantitatif estimatif")
    rows_dev = [r for it in items for r in _device_rows(it)]
    devs = [lab for lab, _m in rows_dev]
    annex = d.o.get("annex") or {}
    mounts = [it["mount"] for it in items if it["kind"] != "deplacement" or it["mount"]]
    rows = []
    for dev in sorted(set(devs)):
        m = next((mm for lab, mm in rows_dev if lab == dev), None)
        rows.append((dev + (" (annexe %s)" % annex[m["_id"]] if m is not None and m["_id"] in annex else ""), "%d u" % devs.count(dev)))
    for key, lab in MOUNT_LABELS.items():
        if mounts.count(key):
            rows.append(("Support : %s" % lab.lower(), "%d u" % mounts.count(key)))
    if mounts.count(""):
        rows.append(("Support à définir", "%d" % mounts.count("")))
    n_move = sum(1 for it in items if it["kind"] == "deplacement")
    n_reuse = sum(1 for it in items if it["kind"] == "deplacement" and it["moves"].get("removed_by"))
    n_rep = sum(1 for it in items for r in it["replaces"] if not r.get("reused_by"))
    if n_move:
        rows.append(("Caméras existantes à déplacer (dépose + repose)%s" % (", dont %d réemploi%s" % (n_reuse, "s" if n_reuse > 1 else "") if n_reuse else ""),
                     "%d u" % n_move))
    if n_rep:
        rows.append(("Caméras existantes à déposer (remplacées, retour au stock)", "%d u" % n_rep))
    rows.append(("Relevé sur site, alimentation et réseau par emplacement", "%d ens." % len(items)))
    qy = d.table(qx, qy, [("", 270, "left"), ("", 72, "right")], rows, size=7.2, row_h=12.5, head=False, bold_last=True)
    d.text(qx, qy - 9, "Quantités indicatives pour chiffrage, à recaler après relevé.", "Barlow-Regular", 5.6, MUTED)


def annex_models(items):
    """Modeles des emplacements dont la fiche technique est enregistree, dans
    l'ordre d'apparition -> [(modele, chemin du PDF)]."""
    out, seen = [], set()
    for it in items:
        for m in models_of(it):
            path = cameras_models.datasheet_path(m["_id"])
            if m["_id"] not in seen and m.get("datasheet") and os.path.isfile(path):
                seen.add(m["_id"])
                out.append((m, path))
    return out


def page_annex(d, annexes, pages):
    """Intercalaire des annexes : liste des fiches techniques jointes."""
    d.new_page()
    d.frame("Annexes – fiches techniques")
    x = MARGIN + 6
    y = d.section(x, PAGE_H - 84, "Fiches techniques des modèles")
    rows = [("A%d" % i, cameras_models.name(m), (m.get("datasheet") or {}).get("name", ""), "%d" % n)
            for i, ((m, _p), n) in enumerate(zip(annexes, pages), 1)]
    y = d.table(x, y, [("Annexe", 50, "left"), ("Modèle", 260, "left"), ("Document", 340, "left"), ("Pages", 50, "right")],
                rows, size=7.4, row_h=13)
    d.text(x, y - 12, "Documents du constructeur joints tels quels à la suite de cette planche, dans l'ordre ci-dessus.",
           "Barlow-Regular", 6.5, MUTED)
    d.cartouche("Annexes", "sans échelle")


def _pdf_pages(path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def build_pdf(db, opts):
    """opts : statuses, ids (facultatif), title, subtitle, indice, phase,
    author, role, datasheets (fiches techniques en annexe). -> (octets du
    PDF, nombre d'emplacements)."""
    _register_fonts()
    items, existing = collect(db, opts.get("statuses"), opts.get("ids"))
    if not items:
        return None, 0
    now = datetime.now(PARIS)
    o = {
        "statuses": opts.get("statuses") or list(STATUS_LABELS),
        "title": (opts.get("title") or "Caméras – nouvelles implantations").strip()[:90],
        "subtitle": (opts.get("subtitle") or "Planification des travaux d'installation").strip()[:120],
        "indice": (opts.get("indice") or "A").strip()[:3].upper(),
        "phase": (opts.get("phase") or "Esquisse").strip()[:30],
        "date": now.strftime("%d/%m/%Y"),
        "month": "%s %d" % (MONTHS[now.month - 1], now.year),
        "short": "Caméras",
    }
    author = (opts.get("author") or "").strip()[:80]
    role = (opts.get("role") or "").strip()[:80]
    o["author_line"] = " – ".join(x for x in (author, role) if x) or "Direction des Opérations ACO"
    o["short"] = o["title"][:40]
    annexes, annex_pages = [], []
    if opts.get("datasheets"):
        for m, path in annex_models(items):
            n = _pdf_pages(path)
            if n:  # PDF illisible ou pypdf absent : fiche ecartee
                annexes.append((m, path))
                annex_pages.append(n)
    o["annex"] = {m["_id"]: "A%d" % i for i, (m, _p) in enumerate(annexes, 1)}
    views = _views_for(items, existing)
    # Deux passes : la premiere compte les planches (le recapitulatif peut
    # deborder), la seconde numerote les cartouches "n/total"
    total = 0
    for _ in range(2):
        d = Doc(o)
        d.total = total
        page_cover(d, items, views)
        page_overview(d, items, existing, views)
        for it in items:
            page_item(d, it, items, existing, views)
        pages_recap(d, items)
        if annexes:
            o["annex_page"] = d.page_no + 1
            page_annex(d, annexes, annex_pages)
        total = d.page_no
    d.c.showPage()
    d.c.save()
    pdf = d.buf.getvalue()
    if annexes:
        pdf = _append_pdfs(pdf, [p for _m, p in annexes])
    return pdf, len(items)


def _append_pdfs(pdf, paths):
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf)))
    for p in paths:
        try:
            w.append(PdfReader(p), import_outline=False)
        except Exception:
            continue
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()
