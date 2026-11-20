"""
Lecture souple des exports de scans : detection du format et des colonnes.

Deux familles d'exports existent, et rien ne garantit qu'il n'en viendra pas
d'autres :

- `matrice` : l'export Sirius hierarchique deja connu (zone / porte / device en
  lignes, un groupe de colonnes par creneau de 15 min). Parse par
  `scan_import.parse_scan_xlsx`, inchange.
- `journal` : UNE LIGNE PAR SCAN (export Weezevent du GP Explorer 2025 :
  746 516 lignes, 49 colonnes, 63 Mo). Les colonnes utiles sont reperees par
  leur en-tete ET leur contenu, et l'utilisateur peut corriger le choix depuis
  la modale d'import. Les lignes sont agregees au vol en creneaux de 15 min :
  la sortie a exactement le contrat de `parse_scan_xlsx`, donc toute la suite
  de la chaine (rattachement, documents, rapport) est commune.

Contraintes de volume :
- openpyxl met ~60 s a lire 746 000 lignes x 49 colonnes. Le lecteur xlsx de
  ce module parcourt directement le XML de la feuille en flux (~35 s), sans
  jamais garder plus d'une ligne en memoire.
- Le fichier n'est jamais charge entierement : un journal de 500 Mo de XML
  decompresse tient en quelques dizaines de Mo de memoire (creneaux agreges +
  empreintes de deduplication).

Fonctions pures : aucune dependance a Flask. La base n'est utilisee que par
`resolve_journal`, toujours passee en argument.
"""

import codecs
import csv
import io
import os
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, time as dtime
# defusedxml et non xml.etree : les parties XML du classeur viennent d'un
# fichier televerse. Il refuse DTD et entites (XXE, expansion d'entites) ;
# meme API, meme parseur expat, aucun surcout mesurable.
from defusedxml.ElementTree import fromstring, iterparse

import scan_import
from scan_import import ScanImportError, normalize_label, canonical_porte

TABLE_EXT = {'.xlsx', '.xlsm', '.csv', '.txt'}

# ---------------------------------------------------------------------------
# Lecture tabulaire en flux
# ---------------------------------------------------------------------------

_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

# Formats de nombre Excel predefinis qui sont des dates / heures.
_BUILTIN_DATE_FMTS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))


class _CountingReader(object):
    """Enveloppe un flux binaire et compte les octets lus (barre de progression)."""

    def __init__(self, fh, total, cb=None):
        self.fh = fh
        self.total = max(1, total)
        self.cb = cb
        self.done = 0
        self._last = -1

    def read(self, n=-1):
        data = self.fh.read(n)
        self.done += len(data)
        if self.cb:
            pct = int(self.done * 100 / self.total)
            if pct != self._last:
                self._last = pct
                self.cb(min(pct, 100))
        return data


def _is_date_format(code):
    if not code:
        return False
    txt = re.sub(r'"[^"]*"', '', code)      # texte litteral
    txt = re.sub(r'\[[^\]]*\]', '', txt)    # couleurs, conditions, [h]
    txt = txt.lower()
    if 'general' in txt:
        return False
    return bool(re.search(r'[dmyhs]', txt))


def _xlsx_meta(z):
    """(chemin de la 1re feuille, styles date, epoque 1904) d'un classeur."""
    sheet_path = 'xl/worksheets/sheet1.xml'
    date1904 = False
    try:
        wb = fromstring(z.read('xl/workbook.xml'))
        pr = wb.find(_NS + 'workbookPr')
        if pr is not None and pr.get('date1904') in ('1', 'true'):
            date1904 = True
        sheets = wb.find(_NS + 'sheets')
        rid = sheets[0].get(_REL_NS + 'id') if sheets is not None and len(sheets) else None
        if rid:
            rels = fromstring(z.read('xl/_rels/workbook.xml.rels'))
            for rel in rels:
                if rel.get('Id') == rid:
                    target = rel.get('Target') or ''
                    sheet_path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                    break
    except (KeyError, IndexError):
        pass

    date_styles = set()
    try:
        styles = fromstring(z.read('xl/styles.xml'))
        custom = {}
        nfs = styles.find(_NS + 'numFmts')
        if nfs is not None:
            for nf in nfs:
                try:
                    custom[int(nf.get('numFmtId'))] = nf.get('formatCode')
                except (TypeError, ValueError):
                    pass
        xfs = styles.find(_NS + 'cellXfs')
        if xfs is not None:
            for idx, xf in enumerate(xfs):
                try:
                    fid = int(xf.get('numFmtId') or 0)
                except ValueError:
                    continue
                if fid in _BUILTIN_DATE_FMTS or (fid in custom and _is_date_format(custom[fid])):
                    date_styles.add(idx)
    except KeyError:
        pass
    return sheet_path, date_styles, date1904


def _shared_strings(z):
    out = []
    try:
        fh = z.open('xl/sharedStrings.xml')
    except KeyError:
        return out
    with fh:
        si_tag, t_tag = _NS + 'si', _NS + 't'
        for _ev, el in iterparse(fh, events=('end',)):
            if el.tag == si_tag:
                out.append(''.join(x.text or '' for x in el.iter(t_tag)))
                el.clear()
    return out


_COL_CACHE = {}


def _col_index(ref):
    letters = ref.rstrip('0123456789')
    idx = _COL_CACHE.get(letters)
    if idx is None:
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch) - 64
        idx = _COL_CACHE[letters] = n - 1
    return idx


def _iter_xlsx(path, progress=None):
    """Lignes (listes) de la 1re feuille, lues en flux dans le XML."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ScanImportError('xlsx_illisible', 400, str(exc))
    with z:
        sheet_path, date_styles, date1904 = _xlsx_meta(z)
        strings = _shared_strings(z)
        epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
        try:
            info = z.getinfo(sheet_path)
        except KeyError:
            raise ScanImportError('xlsx_illisible', 400, 'feuille introuvable')
        c_tag, v_tag, row_tag = _NS + 'c', _NS + 'v', _NS + 'row'
        is_tag, t_tag = _NS + 'is', _NS + 't'
        with z.open(sheet_path) as raw_fh:
            fh = _CountingReader(raw_fh, info.file_size, progress)
            cells = {}
            for _ev, el in iterparse(fh, events=('end',)):
                tag = el.tag
                if tag == c_tag:
                    typ = el.get('t')
                    ref = el.get('r')
                    val = None
                    if typ == 'inlineStr':
                        node = el.find(is_tag)
                        if node is not None:
                            val = ''.join(x.text or '' for x in node.iter(t_tag))
                    else:
                        v = el.find(v_tag)
                        if v is not None and v.text is not None:
                            txt = v.text
                            if typ == 's':
                                try:
                                    val = strings[int(txt)]
                                except (ValueError, IndexError):
                                    val = None
                            elif typ in ('str', 'e'):
                                val = txt
                            elif typ == 'b':
                                val = txt == '1'
                            else:
                                try:
                                    num = float(txt)
                                except ValueError:
                                    num = None
                                if num is None:
                                    val = txt
                                elif el.get('s') and int(el.get('s')) in date_styles:
                                    try:
                                        val = epoch + timedelta(days=num)
                                        val = val.replace(microsecond=0) if val.microsecond < 500000 \
                                            else (val + timedelta(seconds=1)).replace(microsecond=0)
                                    except OverflowError:
                                        val = num
                                else:
                                    val = int(num) if num.is_integer() else num
                    if val is not None and ref:
                        cells[_col_index(ref)] = val
                    el.clear()
                elif tag == row_tag:
                    if cells:
                        row = [None] * (max(cells) + 1)
                        for i, v in cells.items():
                            row[i] = v
                    else:
                        row = []
                    yield row
                    cells = {}
                    el.clear()


def _sniff_encoding(sample):
    if sample.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return 'utf-16'
    try:
        sample.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError as exc:
        # Un caractere multi-octets coupe en fin d'echantillon n'est pas une
        # preuve de cp1252.
        if exc.start >= len(sample) - 3:
            return 'utf-8'
        return 'cp1252'


def _iter_csv(path, progress=None):
    with open(path, 'rb') as probe:
        sample = probe.read(65536)
    encoding = _sniff_encoding(sample)
    text = sample.decode(encoding, errors='replace')
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=';,\t|')
        delimiter = dialect.delimiter
    except csv.Error:
        counts = {d: text.count(d) for d in (';', ',', '\t', '|')}
        delimiter = max(counts, key=counts.get)
    total = max(1, os.path.getsize(path))
    with open(path, 'rb') as raw_fh:
        fh = io.TextIOWrapper(raw_fh, encoding=encoding, errors='replace', newline='')
        last = -1
        for n, row in enumerate(csv.reader(fh, delimiter=delimiter)):
            if progress and n % 5000 == 0:
                # Position du flux binaire sous-jacent : en avance du tampon de
                # lecture, largement assez precis pour une barre.
                pct = min(100, int(raw_fh.tell() * 100 / total))
                if pct != last:
                    last = pct
                    progress(pct)
            yield [c if c != '' else None for c in row]


def iter_rows(path, progress=None):
    """Toutes les lignes du fichier, en flux. `progress(pct)` de 0 a 100."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.csv', '.txt'):
        return _iter_csv(path, progress)
    return _iter_xlsx(path, progress)


def head_rows(path, n=300):
    """Les `n` premieres lignes, sans lire le reste du fichier."""
    out = []
    gen = iter_rows(path)
    try:
        for row in gen:
            out.append(row)
            if len(out) >= n:
                break
    finally:
        gen.close()
    return out


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DT_RE = re.compile(
    r'^\s*(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})'
    r'(?:[ T]+(\d{1,2})[:hH](\d{2})(?::(\d{2}))?(?:[.,]\d+)?)?'
    r'\s*(Z|[+\-]\d{2}:?\d{2})?\s*$')
_TIME_RE = re.compile(r'^\s*(\d{1,2})[:hH](\d{2})(?::(\d{2}))?(?:[.,]\d+)?\s*$')


def _parse_dt_text(txt, day_first=True):
    """Chaine -> datetime naif heure de Paris, ou None.

    Formats reconnus : 'jj/mm/aaaa hh:mm[:ss]', 'aaaa-mm-jj[T ]hh:mm[:ss]',
    avec un decalage UTC eventuel (converti en heure de Paris : toute la
    chaine scans travaille en naif Paris).
    """
    m = _DT_RE.match(txt)
    if not m:
        return None
    a, b, c, hh, mi, ss, tz = m.groups()
    try:
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
        else:
            year = int(c)
            if year < 100:
                year += 2000
            month, day = (int(b), int(a)) if day_first else (int(a), int(b))
        dt = datetime(year, month, day, int(hh or 0), int(mi or 0), int(ss or 0))
    except ValueError:
        return None
    if tz:
        if tz == 'Z':
            offset = timedelta(0)
        else:
            sign = -1 if tz[0] == '-' else 1
            digits = tz[1:].replace(':', '')
            offset = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        utc = (dt - offset).replace(tzinfo=scan_import.ZoneInfo('UTC'))
        dt = utc.astimezone(scan_import.TZ_PARIS).replace(tzinfo=None)
    return dt


def _coerce_dt(val, day_first=True):
    if isinstance(val, datetime):
        return val
    if val is None:
        return None
    return _parse_dt_text(str(val), day_first)


def _coerce_time(val):
    """Heure seule : datetime.time, 'hh:mm[:ss]', fraction de jour Excel."""
    if isinstance(val, dtime):
        return val
    if isinstance(val, datetime):
        return val.time()
    if isinstance(val, float) and 0 <= val < 1:
        secs = int(round(val * 86400))
        return dtime(secs // 3600 % 24, secs // 60 % 60, secs % 60)
    if val is None:
        return None
    m = _TIME_RE.match(str(val))
    if not m:
        return None
    try:
        return dtime(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    except ValueError:
        return None


def _guess_day_first(values):
    """jj/mm (France) sauf preuve du contraire dans l'echantillon."""
    for v in values:
        if isinstance(v, datetime) or v is None:
            continue
        m = _DT_RE.match(str(v))
        if not m or len(m.group(1)) == 4:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            return True
        if b > 12:
            return False
    return True


# ---------------------------------------------------------------------------
# Detection du format et des colonnes
# ---------------------------------------------------------------------------

FORMAT_LABELS = {
    'matrice': 'Matrice Sirius (zone / porte / device, colonnes de 15 min)',
    'journal': 'Journal de scans (une ligne par passage)',
}

# Roles qu'une colonne peut tenir dans un journal. `point` est l'unite de
# scan (porte, tribune, parking...), seul role obligatoire avec la date.
ROLES = [
    {'id': 'horodatage', 'label': 'Date et heure du scan', 'required': False},
    {'id': 'date', 'label': 'Date (si heure a part)', 'required': False},
    {'id': 'heure', 'label': 'Heure (si date a part)', 'required': False},
    {'id': 'point', 'label': 'Point de controle (unite)', 'required': True},
    {'id': 'sens', 'label': 'Sens (entree / sortie)', 'required': False},
    {'id': 'terminal', 'label': 'Terminal / boitier', 'required': False},
    {'id': 'code', 'label': 'Code-barre (dedoublonnage)', 'required': False},
    {'id': 'resultat', 'label': 'Resultat du scan (filtre)', 'required': False},
    {'id': 'famille', 'label': 'Famille de titre (personnes / vehicules)', 'required': False},
    {'id': 'zone', 'label': 'Zone de controle (regroupement)', 'required': False},
]
ROLE_IDS = [r['id'] for r in ROLES]

# Synonymes d'en-tete, compares apres normalize_label (majuscules, sans
# accents ni ponctuation). Le premier groupe vaut correspondance exacte.
_ROLE_SYNONYMS = {
    'horodatage': ['DATE ET HEURE DE SCAN', 'DATE ET HEURE', 'HORODATAGE', 'DATE HEURE',
                   'DATETIME', 'TIMESTAMP', 'SCAN DATETIME', 'DATE TIME', 'DATE HEURE SCAN',
                   'DATE DU SCAN', 'DATE DE PASSAGE', 'DATE SCAN', 'SCAN TIME'],
    'date': ['SCAN DATE', 'DATE DE SCAN', 'DATE', 'JOUR', 'DATE PASSAGE'],
    'heure': ['HEURE DE SCAN', 'HEURE', 'TIME', 'HEURE SCAN', 'HEURE PASSAGE'],
    'sens': ['TYPE SCAN', 'TYPE DE SCAN', 'SENS', 'DIRECTION', 'MOUVEMENT', 'TYPE PASSAGE',
             'IN OUT', 'SENS DE PASSAGE', 'TYPE'],
    'point': ['NOM OPERATEUR', 'POINT DE CONTROLE', 'POINT CONTROLE', 'CHECKPOINT',
              'POINT D ACCES', 'ACCES', 'PORTE', 'LIEU', 'OPERATEUR', 'OPERATOR', 'GATE',
              'CONTROLE', 'POSTE', 'ENTREE'],
    'terminal': ['TERMINAL', 'DEVICE', 'APPAREIL', 'LECTEUR', 'BOITIER', 'PDA', 'EQUIPEMENT',
                 'MATERIEL', 'SCANNER', 'DEVICE NAME', 'NOM DU TERMINAL'],
    'code': ['CODE BARRE', 'CODEBARRE', 'BARCODE', 'CODE', 'UTID', 'NUMERO DE BILLET',
             'BILLET', 'TICKET', 'CODE BILLET'],
    'resultat': ['RESULTAT', 'STATUT', 'STATUS', 'RESULT', 'VALIDATION', 'ETAT', 'ACCEPTATION',
                 'RESULTAT DU SCAN'],
    # Weezevent : « Evenement » = famille du titre (BILLETTERIE, INVITATION,
    # [WETIX] staff, IMPORT [ACO] vehicules, PARKING...). Jamais « Categorie
    # de tarif » : 73 valeurs, le jour et la tribune, pas la nature du titre.
    'famille': ['EVENEMENT', 'FAMILLE DE TITRE', 'FAMILLE', 'TYPE DE TITRE', 'TYPE DE BILLET',
                'NATURE DU TITRE', 'TYPE TITRE', 'BILLETTERIE'],
    # Jamais 'ZONE' seul : dans l'export Weezevent c'est la zone du DROIT
    # d'acces du billet, pas le lieu du scan. Regrouper dessus melangerait
    # des portes qui n'ont rien a voir.
    'zone': ['ZONE DE CONTROLE', 'ZONE CONTROLE', 'SECTEUR DE CONTROLE'],
}

# Une colonne de date dont l'en-tete contient l'un de ces mots n'est pas la
# date du scan (Weezevent : « Creation » = synchronisation du terminal).
_DATE_PENALTY = ('CREATION', 'MODIF', 'IMPORT', 'SYNCHRO', 'EXPORT', 'NAISSANCE', 'VALIDITE',
                 'ACHAT', 'COMMANDE', 'EMISSION', 'EXPIRATION', 'DEBUT', 'FIN')

SENS_VALUES = {
    'ENTREE': 'e', 'ENTRY': 'e', 'IN': 'e', 'ENTRANT': 'e', 'E': 'e', 'ENTER': 'e',
    'SORTIE': 's', 'EXIT': 's', 'OUT': 's', 'SORTANT': 's', 'S': 's',
    'AUTRE': 'a', 'INCONNU': 'a', 'UNKNOWN': 'a',
}

_RESULT_REFUSED = ('REFUS', 'REJET', 'INVALID', 'DENIED', 'DENY', 'KO', 'ERREUR', 'ERROR',
                   'DEJA', 'ALREADY', 'BLACK', 'INCONNU', 'UNKNOWN', 'EXPIR', 'INTERDIT',
                   'NON AUTORIS', 'ECHEC', 'FAIL')
_RESULT_ACCEPTED = ('OK', 'VALID', 'ACCEPT', 'AUTORIS', 'SUCCES', 'SUCCESS', 'GRANTED',
                    'AUTHORIZED', 'OUI', 'YES', 'PASS')


# Famille de titre : 'p' personnes (presents), 'h' hors presents (passage
# compte, mais ni entree ni present : un scan de vehicule ne dit pas combien
# de personnes sont a bord), 'x' exclue de l'import.
FAMILY_CHOICES = ('p', 'h', 'x')
_FAMILY_VEHICLE_RE = re.compile(
    r'\b(VEH\w*|PARKING|PARK|STATIONNEMENT|CONTREMARQUE\w*|2 ROUES|DEUX ROUES|VL|PL|MOTOS?)\b')


def family_default(norm_value):
    """Proposition pour une famille de titre : vehicule -> hors presents."""
    return 'h' if _FAMILY_VEHICLE_RE.search(norm_value or '') else 'p'


def result_accepted(norm_value):
    """Scan accepte d'apres le libelle du resultat (None : indecidable)."""
    v = ' %s ' % norm_value
    for tok in _RESULT_REFUSED:
        if tok in v:
            return False
    for tok in _RESULT_ACCEPTED:
        if (' %s ' % tok) in v or v.strip().startswith(tok):
            return True
    return None


def _find_header_row(rows):
    """Index de la ligne d'en-tete : la 1re ligne majoritairement textuelle."""
    for i, row in enumerate(rows[:15]):
        vals = [v for v in row if v not in (None, '')]
        if len(vals) < 3:
            continue
        texts = sum(1 for v in vals if isinstance(v, str) and not _DT_RE.match(v))
        if texts >= 0.7 * len(vals):
            return i
    return 0


def _synonym_score(header_norm, role):
    best = 0
    for i, syn in enumerate(_ROLE_SYNONYMS[role]):
        if header_norm == syn:
            best = max(best, 100 - i)
        elif re.search(r'\b%s\b' % re.escape(syn), header_norm):
            best = max(best, 40 - i)
    return best


def _column_values(rows, col):
    return [r[col] if col < len(r) else None for r in rows]


def detect(rows):
    """Format et colonnes d'un echantillon de lignes.

    Retourne {format, header_row, columns, mapping, options, warnings}.
    `mapping` vaut {role: index de colonne | None}. L'utilisateur peut le
    corriger depuis la modale : la detection n'est qu'une proposition.
    """
    if not rows:
        raise ScanImportError('fichier_vide_ou_illisible', 400)

    # Matrice Sirius : ligne 2 = sens, ligne 1 = datetimes sur les groupes.
    if len(rows) >= 4:
        sense_hits = sum(1 for v in rows[1][3:]
                         if v is not None and normalize_label(v) in ('ENTREE', 'SORTIE', 'AUTRE'))
        has_dt = any(isinstance(v, datetime) for v in rows[0][3:])
        if sense_hits >= 2 and has_dt:
            return {'format': 'matrice', 'header_row': None, 'columns': [],
                    'mapping': {}, 'options': {}, 'warnings': []}

    h = _find_header_row(rows)
    header = rows[h]
    body = [r for r in rows[h + 1:] if any(v not in (None, '') for v in r)]
    ncols = max([len(header)] + [len(r) for r in body[:50]])

    columns = []
    for c in range(ncols):
        head = header[c] if c < len(header) else None
        vals = [v for v in _column_values(body, c) if v not in (None, '')]
        columns.append({
            'index': c,
            'header': str(head).strip() if head not in (None, '') else 'Colonne %d' % (c + 1),
            'sample': [str(v)[:40] for v in vals[:3]],
        })

    norm_headers = [normalize_label(col['header']) for col in columns]
    filled = {c: [v for v in _column_values(body, c) if v not in (None, '')] for c in range(ncols)}
    mapping = {r: None for r in ROLE_IDS}
    taken = set()

    def ratio(c, fn):
        vals = filled[c]
        if not vals:
            return 0.0
        return sum(1 for v in vals if fn(v)) / float(len(vals))

    # 1. Horodatage complet : synonyme ET contenu date+heure.
    day_first = True
    best = None
    for c in range(ncols):
        vals = filled[c]
        if len(vals) < max(3, 0.5 * len(body)):
            continue
        df = _guess_day_first(vals)
        ok = ratio(c, lambda v: (_coerce_dt(v, df) is not None
                                 and (isinstance(v, datetime) or _DT_RE.match(str(v)).group(4) is not None)))
        if ok < 0.8:
            continue
        times = {(_coerce_dt(v, df) or datetime.min).time() for v in vals[:200]}
        if len(times) <= 1:
            continue  # date seule
        score = _synonym_score(norm_headers[c], 'horodatage') + ok * 10
        if any(p in norm_headers[c] for p in _DATE_PENALTY):
            score -= 60
        if 'SCAN' in norm_headers[c] or 'PASSAGE' in norm_headers[c]:
            score += 15
        if best is None or score > best[0]:
            best = (score, c, df)
    if best:
        mapping['horodatage'] = best[1]
        day_first = best[2]
        taken.add(best[1])
    else:
        # 2. Date et heure separees.
        for role, test in (('date', lambda v: _coerce_dt(v) is not None),
                           ('heure', lambda v: _coerce_time(v) is not None)):
            cands = []
            for c in range(ncols):
                if c in taken:
                    continue
                ok = ratio(c, test)
                if ok < 0.8:
                    continue
                score = _synonym_score(norm_headers[c], role) + ok * 10
                if any(p in norm_headers[c] for p in _DATE_PENALTY):
                    score -= 60
                cands.append((score, c))
            if cands:
                cands.sort(reverse=True)
                mapping[role] = cands[0][1]
                taken.add(cands[0][1])
        if mapping['date'] is not None:
            day_first = _guess_day_first(filled[mapping['date']])

    # 3. Sens : contenu d'abord (valeurs entree/sortie), en-tete ensuite.
    cands = []
    for c in range(ncols):
        if c in taken or not filled[c]:
            continue
        distinct = {normalize_label(v) for v in filled[c]}
        if len(distinct) > 12:
            continue
        ok = ratio(c, lambda v: normalize_label(v) in SENS_VALUES)
        # 'E'/'S' seuls sont trop ambigus sans en-tete evocateur.
        strong = distinct & {'ENTREE', 'SORTIE', 'ENTRY', 'EXIT', 'IN', 'OUT'}
        syn = _synonym_score(norm_headers[c], 'sens')
        if ok >= 0.9 and (strong or syn):
            cands.append((syn + ok * 50, c))
    if cands:
        cands.sort(reverse=True)
        mapping['sens'] = cands[0][1]
        taken.add(cands[0][1])

    # 4. Roles reperes par leur seul en-tete.
    for role in ('point', 'terminal', 'code', 'resultat', 'zone', 'famille'):
        cands = []
        for c in range(ncols):
            if c in taken or not filled[c]:
                continue
            syn = _synonym_score(norm_headers[c], role)
            if syn <= 0:
                continue
            if role in ('resultat', 'zone', 'famille'):
                if len({normalize_label(v) for v in filled[c]}) > 40:
                    continue
            cands.append((syn, c))
        if cands:
            cands.sort(reverse=True)
            mapping[role] = cands[0][1]
            taken.add(cands[0][1])

    warnings = []
    if mapping['sens'] is None:
        warnings.append('sens_absent')
    has_code = mapping['code'] is not None
    return {
        'format': 'journal',
        'header_row': h,
        'columns': columns,
        'mapping': mapping,
        'options': {'day_first': day_first, 'strip_tags': True, 'dedup': has_code},
        'warnings': warnings,
    }


def validate_mapping(mapping):
    """Code d'erreur si le mapping ne permet pas l'import, sinon None."""
    if mapping.get('point') is None:
        return 'colonne_point_absente'
    if mapping.get('horodatage') is None and mapping.get('date') is None:
        return 'colonne_date_absente'
    return None


# ---------------------------------------------------------------------------
# Parsing d'un journal
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r'\s*\[[^\]]*\]\s*')


def clean_point_name(raw, strip_tags=True):
    """Libelle d'unite affichable : '[GPE25]' et doubles espaces retires.

    Mis en majuscules, accents conserves : c'est la convention des exports
    Sirius deja en base, et le nom sert de cle aux unites sans lieu.
    """
    txt = str(raw).strip()
    if strip_tags:
        txt = _TAG_RE.sub(' ', txt)
    # « VIP+ » ecrit en toutes lettres : normalize_label efface la
    # ponctuation, et « Zone concert VIP+ » se confondait avec « Zone concert
    # VIP » (fusion des deux unites, et des choix memorises de l'une sur
    # l'autre).
    txt = re.sub(r'\s*\+', ' PLUS', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt.upper()


def _empty_counts():
    # e / s / a : personnes ; he / hs / ha : titres hors presents (vehicules)
    return {'e': 0, 's': 0, 'a': 0, 'he': 0, 'hs': 0, 'ha': 0}


def _fold(slots, enclosure):
    """Replie les titres hors presents selon la nature de l'unite.

    Porte d'enceinte : passage compte (« sans direction »), ni entree ni
    present. Zone (parking...) : entree / sortie normales.
    """
    out = {}
    for ts, c in slots.items():
        if enclosure:
            out[ts] = {'e': c['e'], 's': c['s'],
                       'a': c['a'] + c.get('he', 0) + c.get('hs', 0) + c.get('ha', 0)}
        else:
            out[ts] = {'e': c['e'] + c.get('he', 0), 's': c['s'] + c.get('hs', 0),
                       'a': c['a'] + c.get('ha', 0)}
    return out


def _slot(dt):
    return dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)


def parse_journal(path, detection, mapping=None, options=None, progress=None):
    """Agrege un journal (une ligne par scan) en creneaux de 15 min par point.

    Retourne un dict « journal » : les points de controle avec leurs creneaux
    et boitiers, et le profil de lecture (lignes lues, doublons, refus...).
    Le passage au contrat de `parse_scan_xlsx` se fait dans `build_parsed`,
    une fois decide quels points sont des portes d'enceinte.
    """
    mapping = dict(detection['mapping'] if mapping is None else mapping)
    opts = dict(detection.get('options') or {})
    opts.update(options or {})
    err = validate_mapping(mapping)
    if err:
        raise ScanImportError(err, 400)

    day_first = bool(opts.get('day_first', True))
    strip_tags = bool(opts.get('strip_tags', True))
    dedup = bool(opts.get('dedup')) and mapping.get('code') is not None
    sens_map = {k: v for k, v in (opts.get('sens_map') or {}).items()
                if v in ('e', 's', 'a', 'x')}
    result_map = {k: bool(v) for k, v in (opts.get('result_map') or {}).items()}
    family_map = {k: v for k, v in (opts.get('famille_map') or {}).items()
                  if v in FAMILY_CHOICES}

    def col(role):
        c = mapping.get(role)
        return int(c) if c is not None else None

    c_ts, c_date, c_time = col('horodatage'), col('date'), col('heure')
    c_point, c_sens, c_term = col('point'), col('sens'), col('terminal')
    c_code, c_res, c_zone = col('code'), col('resultat'), col('zone')
    c_fam = col('famille')

    points = {}                                   # cle -> {name, zone, slots, devices}
    device_hours = defaultdict(lambda: defaultdict(int))
    seen = set()
    stats = Counter()
    sens_seen, sens_raw = Counter(), {}
    res_seen, res_raw = Counter(), {}
    fam_seen, fam_raw = Counter(), {}
    fam_cache = {}
    name_cache, slot_cache = {}, {}
    header_row = detection.get('header_row') or 0

    def cell(row, c):
        return row[c] if c is not None and c < len(row) else None

    for i, row in enumerate(iter_rows(path, progress)):
        if i <= header_row:
            continue
        if not row:
            continue
        stats['lignes'] += 1

        raw_point = cell(row, c_point)
        if raw_point in (None, ''):
            stats['sans_point'] += 1
            continue

        # Date : la plupart des horodatages se repetent a la minute pres,
        # le cache sur la chaine evite de reparser 746 000 fois.
        if c_ts is not None:
            raw_ts = cell(row, c_ts)
            if isinstance(raw_ts, datetime):
                ts = raw_ts
            elif raw_ts in (None, ''):
                ts = None
            else:
                key = str(raw_ts)
                ts = slot_cache.get(key, False)
                if ts is False:
                    ts = _parse_dt_text(key, day_first)
                    if len(slot_cache) < 500000:
                        slot_cache[key] = ts
        else:
            d = _coerce_dt(cell(row, c_date), day_first)
            t = _coerce_time(cell(row, c_time)) if c_time is not None else None
            if d is None:
                ts = None
            elif t is not None:
                ts = datetime.combine(d.date(), t)
            elif c_time is not None and isinstance(cell(row, c_time), (int, float)) \
                    and 0 <= cell(row, c_time) <= 23:
                # Heure entiere seule (Weezevent « Heure de scan ») : precision horaire.
                ts = datetime.combine(d.date(), dtime(int(cell(row, c_time))))
                stats['precision_horaire'] += 1
            else:
                ts = d
        if ts is None:
            stats['sans_date'] += 1
            continue

        # Sens
        if c_sens is not None:
            raw_sens = cell(row, c_sens)
            nsens = normalize_label(raw_sens) if raw_sens not in (None, '') else ''
            sens_seen[nsens] += 1
            sens_raw.setdefault(nsens, raw_sens)
            kind = sens_map.get(nsens) or SENS_VALUES.get(nsens, 'a')
        else:
            kind = 'a'
        if kind == 'x':
            stats['exclus_sens'] += 1
            continue

        # Resultat (filtre des scans refuses)
        if c_res is not None:
            raw_res = cell(row, c_res)
            nres = normalize_label(raw_res) if raw_res not in (None, '') else ''
            res_seen[nres] += 1
            res_raw.setdefault(nres, raw_res)
            keep = result_map.get(nres)
            if keep is None:
                keep = result_accepted(nres) is not False
            if not keep:
                stats['refuses'] += 1
                continue

        # Famille de titre : seules les PERSONNES font des presents. Un titre
        # vehicule reste un passage de la porte (sens « sans direction »,
        # compte dans scan_count) mais ne compte ni en entree ni en present.
        if c_fam is not None:
            raw_fam = cell(row, c_fam)
            nfam = fam_cache.get(raw_fam)
            if nfam is None:
                nfam = fam_cache[raw_fam] = (normalize_label(raw_fam)
                                             if raw_fam not in (None, '') else '')
            fam_seen[nfam] += 1
            fam_raw.setdefault(nfam, raw_fam)
            choice = family_map.get(nfam) or family_default(nfam)
            if choice == 'x':
                stats['exclus_famille'] += 1
                continue
            if choice == 'h':
                # Mis de cote dans son propre compteur : sur une porte
                # d'enceinte il deviendra « sans direction » (ni entree ni
                # present), sur un parking il reste une entree / sortie,
                # puisque c'est le vehicule qu'on y mesure (cf. build_parsed).
                stats['hors_presents'] += 1
                kind = 'h' + kind

        term = cell(row, c_term)
        term = str(term).strip() if term not in (None, '') else None

        # Doublons stricts : meme billet, meme boitier, meme seconde, meme
        # sens. 495 lignes sur l'export GP Explorer 2025 (resynchronisations
        # de terminaux). Une empreinte entiere plutot que le tuple : quelques
        # dizaines de Mo au lieu de plusieurs centaines.
        if dedup:
            fp = hash((cell(row, c_code), term, ts, kind, raw_point))
            if fp in seen:
                stats['doublons'] += 1
                continue
            seen.add(fp)

        name = name_cache.get(raw_point)
        if name is None:
            name = name_cache[raw_point] = clean_point_name(raw_point, strip_tags)
        zone = None
        if c_zone is not None:
            zv = cell(row, c_zone)
            zone = clean_point_name(zv, strip_tags) if zv not in (None, '') else None
        key = (zone, name)
        pt = points.get(key)
        if pt is None:
            pt = points[key] = {'name': name, 'zone': zone, 'devices': set(),
                                'slots': defaultdict(_empty_counts)}
        slot = _slot(ts)
        pt['slots'][slot][kind] += 1
        if term:
            pt['devices'].add(term)
            hour = slot.replace(minute=0)
            device_hours[(key, term)][hour] += 1
        stats['retenues'] += 1

    seen.clear()
    if not points:
        raise ScanImportError('xlsx_sans_donnees', 400,
                              'aucune ligne exploitable (%d lue(s))' % stats['lignes'])

    def value_list(counter, raw, decide):
        out = []
        for norm, n in counter.most_common(30):
            out.append({'value': norm, 'label': str(raw.get(norm) if raw.get(norm) is not None else ''),
                        'count': n, 'map': decide(norm)})
        return out

    profile = {
        'stats': dict(stats),
        'sens_values': value_list(
            sens_seen, sens_raw,
            lambda v: sens_map.get(v) or SENS_VALUES.get(v, 'a')),
        'result_values': value_list(
            res_seen, res_raw,
            lambda v: result_map[v] if v in result_map else result_accepted(v) is not False),
        'famille_values': value_list(
            fam_seen, fam_raw,
            lambda v: family_map.get(v) or family_default(v)),
        'dedup': dedup,
    }
    return {
        'points': points,
        'device_hours': {k: dict(v) for k, v in device_hours.items()},
        'zone_mapped': c_zone is not None,
        'profile': profile,
    }


# ---------------------------------------------------------------------------
# Journal -> contrat de parse_scan_xlsx
# ---------------------------------------------------------------------------

def _merge_slots(target, slots):
    for ts, counts in slots.items():
        dst = target[ts]
        for k in ('e', 's', 'a'):
            dst[k] += counts[k]


def build_parsed(journal, kinds=None, source_file=None):
    """Unites au contrat de `parse_scan_xlsx`.

    - Avec une colonne « zone de controle » : meme semantique que la matrice
      Sirius (les points de ENCEINTE GENERALE sont les portes, les autres
      zones sont agregees).
    - Sans : chaque point de controle est une unite, porte d'enceinte ou zone
      selon `kinds` ({nom: 'porte'|'zone'}), decide par `resolve_journal`.
    """
    kinds = kinds or {}
    enceinte = normalize_label(scan_import.ENCEINTE)
    units = []
    porte_keys = {}                                  # cle point -> nom de porte
    uam_devices = set()

    if journal['zone_mapped']:
        by_zone = defaultdict(lambda: {'devices': set(),
                                       'slots': defaultdict(lambda: {'e': 0, 's': 0, 'a': 0})})
        for key, pt in journal['points'].items():
            zone = pt['zone'] or 'SANS ZONE'
            if normalize_label(zone) == enceinte:
                units.append(scan_import._make_unit(
                    name=pt['name'], kind='porte', zone=scan_import.ENCEINTE,
                    porte=pt['name'], devices=list(pt['devices']),
                    slots=_fold(pt['slots'], True)))
                porte_keys[key] = pt['name']
            else:
                agg = by_zone[zone]
                agg['devices'].update(pt['devices'])
                _merge_slots(agg['slots'], _fold(pt['slots'], False))
            if normalize_label(pt['name']) == 'UAM' or normalize_label(zone) == 'UAM':
                uam_devices.update(pt['devices'])
        for zone, agg in by_zone.items():
            units.append(scan_import._make_unit(
                name=zone, kind='zone', zone=zone, porte=None,
                devices=list(agg['devices']), slots=agg['slots']))
    else:
        for key, pt in journal['points'].items():
            kind = kinds.get(pt['name'], 'zone')
            if kind == 'porte':
                units.append(scan_import._make_unit(
                    name=pt['name'], kind='porte', zone=scan_import.ENCEINTE,
                    porte=pt['name'], devices=list(pt['devices']),
                    slots=_fold(pt['slots'], True)))
                porte_keys[key] = pt['name']
            else:
                units.append(scan_import._make_unit(
                    name=pt['name'], kind='zone', zone=pt['name'], porte=None,
                    devices=list(pt['devices']), slots=_fold(pt['slots'], False)))
            if normalize_label(pt['name']) == 'UAM':
                uam_devices.update(pt['devices'])

    units = [u for u in units if u['slots']]
    units.sort(key=lambda u: (u['kind'], u['name']))
    all_ts = sorted({ts for u in units for ts in u['slots']})
    if not all_ts:
        raise ScanImportError('xlsx_sans_creneaux', 400)

    # Detail par boitier des seules portes, hors boitiers UAM : grain du
    # calcul de l'aide UAM et des renforts (scan_import.compute_uam_help).
    device_hours = {}
    for (key, dev), hours in journal['device_hours'].items():
        porte = porte_keys.get(key)
        if porte is None or scan_import._classify_device(dev) != 'pda':
            continue
        dst = device_hours.setdefault((porte, dev), {})
        for hour, n in hours.items():
            dst[hour] = dst.get(hour, 0) + n
    all_devices = {d for u in units for d in u['devices']}

    return {
        'units': units,
        'period_start': all_ts[0],
        'period_end': all_ts[-1],
        'slot_count': len(all_ts),
        'has_autre': any(u['total_autre'] for u in units),
        'source_file': source_file,
        'zone_count': sum(1 for u in units if u['kind'] == 'zone'),
        'porte_count': sum(1 for u in units if u['kind'] == 'porte'),
        'device_hours': device_hours,
        'uam_devices': sorted(uam_devices),
        # Sans aucun tripode dans le fichier (tout en terminaux portatifs),
        # un PDA sur une porte a tripodes n'est pas un debordement : c'est
        # l'equipement normal de l'edition.
        'has_tripodes': any(scan_import._classify_device(d) == 'tripode' for d in all_devices),
        'format': 'journal',
        'kind_from_category': not journal['zone_mapped'],
    }


def _initial_kinds(db, journal, saved):
    """Porte d'enceinte ou zone, avant tout choix de l'utilisateur.

    Une categorie memorisee tranche ; sinon un nom deja connu comme porte
    (documents `portes` curates, geojson `portes`) ou commencant par PORTE.
    """
    known = scan_import._harvest_known_doors(db)
    geo = scan_import._load_geo_index(db)
    kinds = {}
    for pt in journal['points'].values():
        name = pt['name']
        cat = (saved.get(normalize_label(name)) or {}).get('category')
        if cat in scan_import.CATEGORY_IDS:
            kinds[name] = 'porte' if cat == 'porte' else 'zone'
            continue
        canon = canonical_porte(name)
        if canon in known or canon in geo['portes'] or re.match(r'^PORTE\b', canon):
            kinds[name] = 'porte'
        else:
            kinds[name] = 'zone'
    return kinds


def resolve_journal(db, journal, event, year, overrides_by_name=None, source_file=None):
    """Unites, rattachements et overrides re-cles pour un journal.

    Dans un journal, rien ne dit structurellement qu'un point est une porte
    d'enceinte (la matrice Sirius le dit par la zone ENCEINTE GENERALE). C'est
    donc la CATEGORIE qui decide : « Porte d'enceinte » fait entrer l'unite
    dans la serie de l'enceinte generale, toute autre categorie en fait une
    zone. Changer la categorie dans la modale change donc la nature de
    l'unite, et la cle (kind, name) suit.

    `overrides_by_name` : {nom: {...}} tel que recu de l'UI, sans le kind.
    Retourne (parsed, resolved, overrides re-cles {(kind, name): {...}}).
    """
    overrides_by_name = overrides_by_name or {}
    if journal['zone_mapped']:
        parsed = build_parsed(journal, source_file=source_file)
        live = {u['key']: overrides_by_name[u['name']] for u in parsed['units']
                if u['name'] in overrides_by_name}
        resolved = scan_import.resolve_features(db, parsed['units'], event, year,
                                                overrides=live, any_kind=True)
        return parsed, resolved, live

    saved = scan_import._load_overrides_by_name(db, event, year)
    kinds = _initial_kinds(db, journal, saved)
    for name, ov in overrides_by_name.items():
        if ov.get('category') in scan_import.CATEGORY_IDS:
            kinds[name] = 'porte' if ov['category'] == 'porte' else 'zone'

    for _ in range(3):
        parsed = build_parsed(journal, kinds, source_file=source_file)
        live = {u['key']: overrides_by_name[u['name']] for u in parsed['units']
                if u['name'] in overrides_by_name}
        resolved = scan_import.resolve_features(db, parsed['units'], event, year,
                                                overrides=live, any_kind=True)
        new = {u['name']: ('porte' if resolved[u['key']]['category'] == 'porte' else 'zone')
               for u in parsed['units']}
        if all(kinds.get(n) == k for n, k in new.items()):
            break
        kinds.update(new)
    return parsed, resolved, live


# ---------------------------------------------------------------------------
# Point d'entree : analyse d'un fichier
# ---------------------------------------------------------------------------

def analyze_file(path, mapping=None, options=None, progress=None):
    """Detecte le format et lit le fichier.

    Retourne {format, detection, parsed (matrice) | journal (journal)}.
    `progress(pct, message)`.
    """
    def report(pct, msg):
        if progress:
            progress(pct, msg)

    report(2, 'Detection du format')
    ext = os.path.splitext(path)[1].lower()
    sample = head_rows(path, 300)
    detection = detect(sample)

    if detection['format'] == 'matrice':
        if ext in ('.csv', '.txt'):
            raise ScanImportError('matrice_csv_non_supportee', 400)
        report(10, 'Lecture de la matrice')
        parsed = scan_import.parse_scan_xlsx(path)
        parsed['format'] = 'matrice'
        return {'format': 'matrice', 'detection': detection, 'parsed': parsed}

    effective = dict(detection['mapping'])
    if mapping:
        for role in ROLE_IDS:
            if role in mapping:
                v = mapping[role]
                effective[role] = int(v) if v not in (None, '') else None
    detection['mapping'] = effective
    if options:
        detection['options'].update(options)
    err = validate_mapping(effective)
    if err:
        raise ScanImportError(err, 400, detection=detection)

    report(5, 'Lecture du journal')
    journal = parse_journal(
        path, detection, effective, detection['options'],
        progress=lambda p: report(5 + int(p * 0.9), 'Lecture du journal (%d %%)' % p))
    return {'format': 'journal', 'detection': detection, 'journal': journal}
