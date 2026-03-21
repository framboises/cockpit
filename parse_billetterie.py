#!/usr/bin/env python3
"""
Parse les PDFs comparatifs billetterie et genere un xlsx par annee
avec les sous-totaux, prefixes par le topic_code.

Usage:
  python3 parse_billetterie.py 24M                                  # tous les PDFs dans uploads/
  python3 parse_billetterie.py 24M uploads/mon_fichier.pdf           # un seul PDF
  python3 parse_billetterie.py 24M fichier1.pdf fichier2.pdf         # plusieurs PDFs
"""

import pdfplumber
import re
import os
import sys
from openpyxl import Workbook
from openpyxl.styles import Font, Border, Side, PatternFill


# ── Utilitaires ──────────────────────────────────────────────────────────

def collect_text(words, x_min, x_max):
    return " ".join(w['text'] for w in words
                    if w['x0'] >= x_min and w['x0'] < x_max).strip()


def parse_int(s):
    s = s.replace("\u20ac", "").replace(",", ".").replace("\xa0", "")
    s = s.replace("\uf0ec", "").strip()
    s = re.sub(r'\s+', '', s)
    if not s or s == '-':
        return None
    try:
        return int(float(s))
    except (ValueError, OverflowError):
        return None


def clean_name(name):
    """Nettoie un nom de produit aire/tribune: retire prix, capacites."""
    name = name.replace("\u20ac", "").strip()
    # Fix espacement lettre a lettre: "2 r o u e s" -> "2 roues"
    def fix_spaced(m):
        return re.sub(r'\s+', '', m.group(0))
    name = re.sub(r'(?<!\w)(\w\s){3,}\w(?!\w)', fix_spaced, name)
    # Retirer prix/tarifs en fin
    name = re.sub(r'\s*\d+\s*,\s*\d+\s*\d*\s*$', '', name)
    # Retirer capacite en debut (3+ chiffres)
    name = re.sub(r'^\d{3}[\d\s]*(?=[A-Z])', '', name)
    name = re.sub(r'^\d{2,}\s+(?=\d\s*[a-z])', '', name)
    # Si juste un nombre, vide
    if re.match(r'^[\d\s,.\u20ac]+$', name.strip()):
        return ''
    return name.strip()


def normalize_name(topic_code, raw_name):
    """Normalise: TOPICCODE_NOMPRODUIT (majuscules, sans accent, sans espace)."""
    import unicodedata
    name = raw_name.strip()
    # Retirer "Carte " prefix
    name = re.sub(r'^Carte\s+', '', name)
    # Majuscules
    name = name.upper()
    # Retirer accents
    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    # Remplacer espaces et caracteres speciaux par _
    name = re.sub(r'[^A-Z0-9]+', '_', name)
    # Nettoyer underscores multiples et en debut/fin
    name = re.sub(r'_+', '_', name).strip('_')
    return f"{topic_code} {name}"


# ── Parseur ──────────────────────────────────────────────────────────────

def find_column_boundaries(page):
    """
    Trouve les positions X des colonnes Vendu
    depuis la ligne d'en-tete ("Vendu" et "Commande").
    Retourne (v24_range, v25_range).
    """
    words = page.extract_words(x_tolerance=2, y_tolerance=2)

    vendu_positions = []
    cmd_positions = []

    for w in words:
        if w['top'] > 60:
            continue
        if w['text'] == 'Vendu':
            vendu_positions.append(w['x0'])
        elif w['text'] == 'Commandé':
            cmd_positions.append(w['x0'])

    vendu_positions.sort()
    cmd_positions.sort()

    if len(vendu_positions) >= 2 and len(cmd_positions) >= 2:
        v24 = (vendu_positions[0] - 2, cmd_positions[0])
        v25 = (vendu_positions[1] - 2, cmd_positions[1])
        return v24, v25

    return (381, 418), (670, 706)


def parse_subtotals(page):
    """Extrait tous les sous-totaux + totaux tribunes/aires/supplements/parking."""
    words = page.extract_words(x_tolerance=2, y_tolerance=2)

    v24_range, v25_range = find_column_boundaries(page)

    rows = {}
    for w in words:
        y = round(w['top'] / 3) * 3
        if y not in rows:
            rows[y] = []
        rows[y].append(w)
    for y in rows:
        rows[y].sort(key=lambda w: w['x0'])

    x_split = page.width / 2
    label_24_end = v24_range[0]
    label_25_end = v25_range[0]

    results_24 = []
    results_25 = []

    # Trouver Y du "Total 202X" (fin de la zone ENTREES)
    y_total_entrees = None

    # ── 1. Sous-totaux de la zone ENTREES ──
    for y in sorted(rows.keys()):
        ws = rows[y]
        line = " ".join(w['text'] for w in ws)

        if 'Total 202' in line:
            y_total_entrees = y
            continue

        if 'ous-total' not in line:
            continue

        # Cote 2024
        left = [w for w in ws if w['x0'] < x_split]
        label_24 = collect_text(left, 0, label_24_end)
        m = re.search(r'ous-total\s+(.*)', label_24)
        if m:
            name = m.group(1).strip()
            vendu = parse_int(collect_text(left, *v24_range))
            if name and vendu is not None:
                results_24.append((name, vendu))

        # Cote 2025
        right = [w for w in ws if w['x0'] >= x_split]
        label_25 = collect_text(right, x_split, label_25_end)
        m = re.search(r'ous-total\s+(.*)', label_25)
        if m:
            name = m.group(1).strip()
            vendu = parse_int(collect_text(right, *v25_range))
            if name and vendu is not None:
                results_25.append((name, vendu))

    if not y_total_entrees:
        y_total_entrees = 575

    # ── 2. Supplements (nom sur une ligne, donnees 3px au-dessus) ──
    supp_names = []
    for y in sorted(rows.keys()):
        if y < y_total_entrees or y > y_total_entrees + 35:
            continue
        ws = rows[y]
        margin = collect_text(ws, 200, 280)
        margin = re.sub(r'^SUPP\s*', '', margin).strip()
        if margin and not re.match(r'^[\d\s.,\u20ac%]+$', margin):
            supp_names.append((y, margin))

    for name_y, name in supp_names:
        for offset in [-3, -6, -9]:
            data_y = name_y + offset
            if data_y in rows:
                ws = rows[data_y]
                v24 = parse_int(collect_text(ws, *v24_range))
                v25 = parse_int(collect_text(ws, *v25_range))
                # Fallback: vendu peut etre dans la zone total pour les supplements
                if v24 is None:
                    v24 = parse_int(collect_text(ws, v24_range[1], v24_range[1] + 30))
                if v24 is not None or v25 is not None:
                    if v24 is not None:
                        results_24.append((name, v24))
                    if v25 is not None:
                        results_25.append((name, v25))
                    break

    # ── 3. Tribunes (produits individuels) ──
    # Trouver les bornes Y: entre le header "Capacite" apres ENTREES et le Total tribunes
    y_trib_start = y_total_entrees + 30
    y_trib_end = None
    y_aires_end = None
    # Trouver les lignes "Total" a x<270 (tribunes puis aires)
    total_lines = []
    for y in sorted(rows.keys()):
        if y <= y_total_entrees:
            continue
        ws = rows[y]
        margin_words = [w for w in ws if w['x0'] < 270]
        margin_text = " ".join(w['text'] for w in margin_words).strip()
        if margin_text == 'Total' or (margin_text.startswith('Total') and len(margin_text) < 10):
            total_lines.append(y)
    y_trib_end = total_lines[0] if len(total_lines) >= 1 else y_trib_start + 60
    y_aires_end = total_lines[1] if len(total_lines) >= 2 else y_trib_end + 100

    # Detecter les noms de tribunes dans la marge
    tribune_markers = {}
    for y in sorted(rows.keys()):
        if y < y_trib_start or y > y_trib_end:
            continue
        margin = collect_text(rows[y], 200, 295)
        if 'T16' in margin:
            tribune_markers[y] = 'Tribune T16'
        elif 'T19' in margin:
            tribune_markers[y] = 'Tribune T19'
        elif 'T34' in margin:
            tribune_markers[y] = 'Tribune T34'

    def get_tribune(y):
        name = 'Tribune'
        for ty in sorted(tribune_markers.keys()):
            if ty <= y:
                name = tribune_markers[ty]
        return name

    # Extraire les lignes produit tribunes (celles avec un tarif en euros)
    for y in sorted(rows.keys()):
        if y < y_trib_start or y >= y_trib_end:
            continue
        ws = rows[y]
        line = " ".join(w['text'] for w in ws)
        if 'Capacit' in line or 'Occupation' in line:
            continue
        # Chercher un tarif (xx €) dans la zone tarif 2024 (x ~355-385) ou 2025
        tarif_zone = collect_text(ws, 355, 390)
        if not re.search(r'\d+\s*\u20ac', tarif_zone):
            continue
        v24 = parse_int(collect_text(ws, *v24_range))
        v25 = parse_int(collect_text(ws, *v25_range))
        trib = get_tribune(y)
        if v24 is not None:
            results_24.append((trib, v24))
        if v25 is not None:
            results_25.append((trib, v25))

    # ── 4. Aires d'accueil (produits individuels) ──
    y_aires_start = y_trib_end + 3

    # Detecter les noms d'aires dans la marge
    aire_markers = {}
    for y in sorted(rows.keys()):
        if y < y_aires_start or y > y_aires_end:
            continue
        margin = collect_text(rows[y], 200, 295)
        if 'Houx' in margin or 'Concentration' in margin:
            aire_markers[y] = 'AA Houx'
        elif 'Ouest' in margin:
            aire_markers[y] = 'AA Ouest'
        elif 'Maison' in margin:
            aire_markers[y] = 'AA Maison Blanche'
        elif 'Panorama' in margin:
            aire_markers[y] = 'AA Panorama'
        elif 'Tertre' in margin:
            aire_markers[y] = 'AA Tertre Rouge'

    def get_aire(y):
        name = 'AA'
        for ay in sorted(aire_markers.keys()):
            if ay <= y:
                name = aire_markers[ay]
        return name

    # Extraire les produits aires (Seule, Pack, 2 roues, 4 roues, etc.)
    for y in sorted(rows.keys()):
        if y < y_aires_start or y >= y_aires_end:
            continue
        ws = rows[y]
        line = " ".join(w['text'] for w in ws)
        if 'Total' in line or 'Capacit' in line or 'Occupation' in line or 'Remplissag' in line:
            continue
        if 'en 202' in line:
            continue

        # Nom produit cote 2025 (plus fiable, noms a jour)
        nom_25 = collect_text(ws, 570, 665)
        nom_24 = collect_text(ws, 280, 370)
        # Nettoyer: retirer chiffres purs (capacites)
        if re.match(r'^[\d\s]+$', nom_24): nom_24 = ''
        if re.match(r'^[\d\s]+$', nom_25): nom_25 = ''
        nom_24 = clean_name(nom_24)
        nom_25 = clean_name(nom_25)
        if not nom_24 and not nom_25:
            continue

        v24 = parse_int(collect_text(ws, *v24_range))
        v25 = parse_int(collect_text(ws, *v25_range))

        aire = get_aire(y)
        if v24 is not None and nom_24:
            results_24.append((f"{aire} {nom_24}", v24))
        if v25 is not None and nom_25:
            results_25.append((f"{aire} {nom_25}", v25))

    # ── 5. Parking ──
    for y in sorted(rows.keys()):
        if y < y_aires_end:
            continue
        ws = rows[y]
        margin = collect_text(ws, 200, 270)
        if 'Parking' in margin or ('8' in margin and 'AB' in margin.replace(' ', '')):
            v24 = parse_int(collect_text(ws, *v24_range))
            v25 = parse_int(collect_text(ws, *v25_range))
            if v24 is not None:
                results_24.append(('Parking 8AB', v24))
            if v25 is not None:
                results_25.append(('Parking 8AB', v25))
            break

    return results_24, results_25


def detect_years(page):
    words = page.extract_words(x_tolerance=2, y_tolerance=2)
    text = " ".join(w['text'] for w in sorted(words, key=lambda w: (w['top'], w['x0'])))
    years = re.findall(r'(?:Tarifs|Total|CA\s+\w+)\s+(20\d{2})', text)
    years = sorted(set(years))
    return (int(years[0]), int(years[1])) if len(years) >= 2 else (None, None)


def detect_date_from_page1(page):
    """Detecte la date DD/MM/YYYY en haut a droite de la page 1."""
    words = page.extract_words(x_tolerance=2, y_tolerance=2)
    for w in words:
        if w['top'] < 50:
            m = re.match(r'(\d{2})/(\d{2})/(\d{4})', w['text'])
            if m:
                return m.group(1), m.group(2), int(m.group(3))
    return None, None, None


# ── XLSX ─────────────────────────────────────────────────────────────────

def write_xlsx(products, year, dd, mm, output_dir):
    wb = Workbook()
    ws = wb.active
    ws.title = "productReceipts"

    hdr_font = Font(bold=True, size=11)
    hdr_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'),
    )

    ws.cell(row=1, column=2, value="Produit").font = hdr_font
    ws.cell(row=1, column=2).fill = hdr_fill
    ws.cell(row=1, column=2).border = border
    ws.cell(row=1, column=3, value="Nb net de ventes (Total)").font = hdr_font
    ws.cell(row=1, column=3).fill = hdr_fill
    ws.cell(row=1, column=3).border = border

    total = 0
    for i, (nom, vendu) in enumerate(products, 2):
        ws.cell(row=i, column=2, value=nom).border = border
        c = ws.cell(row=i, column=3, value=vendu)
        c.border = border
        c.number_format = '#,##0'
        total += (vendu or 0)

    row = len(products) + 2
    ws.cell(row=row, column=2, value="Grand total").font = Font(bold=True)
    ws.cell(row=row, column=2).border = border
    c = ws.cell(row=row, column=3, value=total)
    c.font = Font(bold=True)
    c.border = border
    c.number_format = '#,##0'

    ws.column_dimensions['B'].width = 40
    ws.column_dimensions['C'].width = 25

    filename = f"Query_{year}{mm}{dd}.xlsx"
    filepath = os.path.join(output_dir, filename)
    wb.save(filepath)
    return filepath


# ── Traitement ───────────────────────────────────────────────────────────

def process_pdf(pdf_path, topic_code, output_dir):
    print(f"\nTraitement: {os.path.basename(pdf_path)}")

    pdf = pdfplumber.open(pdf_path)
    if len(pdf.pages) < 3:
        print(f"  SKIP: {len(pdf.pages)} pages")
        pdf.close()
        return []

    dd, mm, date_year = detect_date_from_page1(pdf.pages[0])
    if not dd:
        print("  WARN: date non trouvee sur page 1")
        pdf.close()
        return []

    year_old, year_new = detect_years(pdf.pages[2])
    if not year_old:
        year_old, year_new = date_year - 1, date_year

    st_old, st_new = parse_subtotals(pdf.pages[2])

    # Prefixer les noms avec le topic_code
    st_new = [(normalize_name(topic_code, n), v) for n, v in st_new]
    st_old = [(normalize_name(topic_code, n), v) for n, v in st_old]

    print(f"  Date: {dd}/{mm}/{date_year}")
    print(f"  {year_new}: {len(st_new)} sous-totaux | {year_old}: {len(st_old)} sous-totaux")
    for name, v in st_new:
        print(f"    {name:<35} {v:>8}")

    files = []
    if st_new:
        f = write_xlsx(st_new, year_new, dd, mm, output_dir)
        print(f"  -> {os.path.basename(f)}")
        files.append(f)
    if st_old:
        f = write_xlsx(st_old, year_old, dd, mm, output_dir)
        print(f"  -> {os.path.basename(f)}")
        files.append(f)

    pdf.close()
    return files


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]

    if not args:
        print("Usage: python3 parse_billetterie.py TOPIC_CODE [fichier.pdf ...]")
        print("  Ex:  python3 parse_billetterie.py 24M")
        print("       python3 parse_billetterie.py 24M uploads/fichier.pdf")
        sys.exit(1)

    topic_code = args[0]
    pdf_args = args[1:]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    uploads_dir = os.path.join(script_dir, "uploads")
    output_dir = os.path.join(uploads_dir, "xlsx_output")
    os.makedirs(output_dir, exist_ok=True)

    if pdf_args:
        pdfs = []
        for p in pdf_args:
            path = p if os.path.isabs(p) else os.path.join(os.getcwd(), p)
            if os.path.exists(path):
                pdfs.append(path)
            else:
                print(f"Fichier introuvable: {p}")
    else:
        pdfs = sorted([
            os.path.join(uploads_dir, f)
            for f in os.listdir(uploads_dir)
            if f.endswith('.pdf') and 'Billetterie' in f
        ])

    if not pdfs:
        print("Aucun PDF billetterie trouve")
        return

    print(f"Topic code: {topic_code}")
    print(f"{len(pdfs)} PDF(s) a traiter")

    all_files = []
    for pdf_path in pdfs:
        try:
            files = process_pdf(pdf_path, topic_code, output_dir)
            all_files.extend(files)
        except Exception as e:
            print(f"  ERREUR: {e}")
            import traceback
            traceback.print_exc()

    print(f"\nTermine! {len(all_files)} fichiers xlsx dans {output_dir}")


if __name__ == "__main__":
    main()
