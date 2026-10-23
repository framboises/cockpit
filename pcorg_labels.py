"""Libelles des listes de la main courante : une seule orthographe par libelle.

Prysm a change d'orthographe au fil des editions ("Signaletique" puis
"Signalétique", "Electricite" / "Electricité"...) et la synchro recopiait
chaque variante dans pcorg_config.pcorg_lists (comparaison a l'octet pres) :
deux entrees dans les menus, statistiques coupees en deux.

Regle : deux libelles sont le meme s'ils ont la meme cle (sans accents, sans
casse, tirets et espaces multiples ignores). Le libelle retenu est celui de
la liste (pcorg_lists) ; entre deux variantes, la plus accentuee, puis la plus
frequente.

Module pur (ni Flask ni Mongo) : utilise par app.py, la synchro SQL et
scripts/dedupe_pcorg_labels.py.
"""
import re
import unicodedata

# Champs de content_category et liste de pcorg_lists qui les alimente
SC_FIELD = "sous_classification"
PERSON_FIELDS = tuple(f"intervenant{i}" for i in range(1, 6)) + (
    "moyens_engages_niveau_1", "moyens_engages_niveau_2")
SERVICE_FIELDS = ("service_contacte",)


def key(label):
    s = unicodedata.normalize("NFD", str(label or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\s\-_]+", " ", s)
    return s.strip().lower()


def same(a, b):
    if a in (None, "") or b in (None, ""):
        return a in (None, "") and b in (None, "")
    if not isinstance(a, str) or not isinstance(b, str):
        return a == b
    return key(a) == key(b)


def _accents(label):
    return sum(1 for c in unicodedata.normalize("NFD", label) if unicodedata.combining(c))


def best(labels, counts=None):
    """Variante a garder : la plus accentuee, puis la plus frequente, puis la premiere."""
    counts = counts or {}
    order = {l: i for i, l in reversed(list(enumerate(labels)))}
    return min(labels, key=lambda l: (-_accents(l), -counts.get(l, 0), order[l]))


def _label(it):
    return it.get("label") if isinstance(it, dict) else it


def dedupe_items(items, counts=None):
    """Fusionne les doublons d'une liste [{id, label}] (ou de chaines).

    Garde la premiere entree de chaque groupe (son id) avec le meilleur
    libelle. Retourne (liste, {libelle ecarte: libelle garde}).
    """
    groups, order = {}, []
    for it in items or []:
        lbl = _label(it)
        if not lbl:
            continue
        k = key(lbl)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(it)
    out, renamed = [], {}
    for k in order:
        grp = groups[k]
        labels = [_label(it) for it in grp]
        keep = best(labels, counts)
        first = grp[0]
        if isinstance(first, dict):
            first = dict(first, label=keep)
        else:
            first = keep
        out.append(first)
        for l in labels:
            if l != keep:
                renamed[l] = keep
    return out, renamed


class Canon:
    """Ramene les valeurs d'une fiche aux libelles de pcorg_lists."""

    def __init__(self, config):
        config = config or {}
        self.sc = {cat: self._map(items)
                   for cat, items in (config.get("sous_classifications") or {}).items()}
        self.people = self._map(config.get("intervenants"))
        self.services = self._map(config.get("services"))

    @staticmethod
    def _map(items):
        """{cle: libelle} ; liste encore doublonnee : la meme variante que
        dedupe_items et le menu du navigateur (la plus accentuee)."""
        groups = {}
        for it in items or []:
            lbl = _label(it)
            if lbl:
                groups.setdefault(key(lbl), []).append(lbl)
        return {k: best(v) for k, v in groups.items()}

    @staticmethod
    def _fix(value, mapping):
        if isinstance(value, str) and value:
            return mapping.get(key(value), value)
        if isinstance(value, list):
            return [mapping.get(key(v), v) if isinstance(v, str) and v else v for v in value]
        return value

    def apply_cc(self, category, cc):
        """Corrige content_category en place. Retourne la liste des champs changes."""
        if not isinstance(cc, dict):
            return []
        changed = []
        plan = [(SC_FIELD, self.sc.get(category) or {})]
        plan += [(f, self.people) for f in PERSON_FIELDS]
        plan += [(f, self.services) for f in SERVICE_FIELDS]
        for field, mapping in plan:
            if field in cc and mapping:
                new = self._fix(cc[field], mapping)
                if new != cc[field]:
                    cc[field] = new
                    changed.append(field)
        return changed
