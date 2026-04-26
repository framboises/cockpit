"""Envoi par email d'un resume PC Organisation.

Construit un HTML soigne (inline CSS, compatible Outlook/Gmail) a partir
d'un doc serialise par pcorg_summary._serialize_summary(light=False), puis
envoie via SMTP.

Aligne sur le pattern de TITAN Home (home.py:_smtp_send_email) pour partager
la meme configuration et la meme charte visuelle (header ACO + footer RGPD).

Variables d'environnement (identiques a Home) :
- SMTP_HOST                (defaut "192.168.254.2")
- SMTP_PORT                (defaut 25)
- SMTP_USER, SMTP_PASSWORD (optionnels)
- SMTP_USE_TLS             (1/true/yes pour activer STARTTLS, defaut false)
- SMTP_FROM                (defaut "safe@lemans.org")
- SMTP_FROM_NAME           (defaut "TITAN ACO")
- SMTP_REPLY_TO            (optionnel)
- SMTP_TIMEOUT             (defaut 10s)
- PUBLIC_BASE_URL          (defaut "https://safe.lemans.org", utilise pour le logo)
"""

import html
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Helpers de formatage
# ----------------------------------------------------------------------------

SECTION_LABELS = [
    ("synthese", "Synthese", "#2563eb"),
    ("faits_marquants", "Faits marquants", "#f59e0b"),
    ("secours", "Secours", "#dc2626"),
    ("securite", "Securite", "#ef4444"),
    ("technique", "Technique", "#f59e0b"),
    ("flux", "Flux", "#0d9488"),
    ("fourriere", "Fourriere", "#6b7280"),
    ("recommandations", "Recommandations", "#16a34a"),
]

URGENCY_LABELS = {"EU": "Detresse vitale", "UA": "Urgence absolue", "UR": "Urgence relative", "IMP": "Implique"}


def _h(s):
    return html.escape(s if isinstance(s, str) else str(s if s is not None else ""))


def _md_inline(text):
    """Convertit le markdown inline ECHAPPE en HTML : **gras** -> <strong>."""
    import re as _re
    if not text:
        return ""
    return _re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", text)


def _render_md(text):
    """Mini-renderer markdown -> HTML pour le contenu des sections.

    Supporte :
    - lignes commencant par '- ' ou '* ' -> liste a puces
    - paragraphes separes par lignes vides
    - **gras** -> <strong>
    - sauts de ligne dans un paragraphe -> <br>

    Echappe d'abord le HTML pour eviter toute injection.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    # Decoupage en blocs (paragraphes ou listes consecutives)
    lines = text.split("\n")
    out_html = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # Liste a puces : on consomme toutes les lignes consecutives '- '
        if stripped.startswith("- ") or stripped.startswith("* "):
            items = []
            while i < n:
                ls = lines[i].strip()
                if ls.startswith("- ") or ls.startswith("* "):
                    item_text = ls[2:].strip()
                    items.append(item_text)
                    i += 1
                else:
                    break
            li_html = "".join(
                "<li style=\"margin:4px 0;\">" + _md_inline(_h(it)) + "</li>"
                for it in items
            )
            out_html.append(
                "<ul style=\"margin:6px 0 8px 0;padding-left:20px;\">" + li_html + "</ul>"
            )
            continue
        # Paragraphe : consomme les lignes jusqu'a une ligne vide ou debut de liste
        para_lines = []
        while i < n:
            ls = lines[i].strip()
            if not ls:
                break
            if ls.startswith("- ") or ls.startswith("* "):
                break
            para_lines.append(ls)
            i += 1
        para = "<br>".join(_md_inline(_h(p)) for p in para_lines)
        out_html.append(
            "<p style=\"margin:0 0 8px 0;line-height:1.55;\">" + para + "</p>"
        )
    return "".join(out_html)


def _fmt_int(n):
    if n is None:
        return "-"
    try:
        return "{:,}".format(int(n)).replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _fmt_dt_human(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return _h(iso_str)
    return dt.strftime("%d/%m/%Y %H:%M")


def _fmt_period(start_iso, end_iso):
    if not start_iso or not end_iso:
        return ""
    return _fmt_dt_human(start_iso) + " &rarr; " + _fmt_dt_human(end_iso)


def _scope_label(summary):
    ev = summary.get("event")
    yr = summary.get("year")
    if ev and yr:
        return _h(ev) + " " + _h(yr)
    if ev:
        return _h(ev) + " (toutes annees)"
    if yr:
        return "Annee " + _h(yr) + " (tous evenements)"
    return "Tous evenements"


# ----------------------------------------------------------------------------
# Layout commun TITAN (header ACO + footer RGPD), aligne sur home.py
# ----------------------------------------------------------------------------

def _public_base_url():
    return os.getenv("PUBLIC_BASE_URL", "https://safe.lemans.org").rstrip("/")


def _header_aco_html():
    base = _public_base_url()
    return (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:collapse;margin:0;padding:0;width:100%;background:#0b1024;">'
        '<tr><td align="center" style="padding:16px;background:#0b1024;">'
        '<img src="' + base + '/static/img/ACO-logo-fit.png" '
        'alt="Automobile Club de l\'Ouest" height="30" style="display:block;">'
        '</td></tr></table>'
    )


def _footer_aco_html():
    return (
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:collapse;margin:0;padding:0;width:100%;background:#f4f5f7;">'
        '<tr><td align="center" style="padding:20px 16px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:12px;line-height:18px;color:#666666;">'
        'Cet email a ete envoye automatiquement par la plateforme TITAN.<br>'
        'Merci de ne pas repondre directement a ce message.<br><br>'
        'Pour toute question, contactez le support : '
        '<a href="mailto:safe@lemans.org" style="color:#1a5caa;text-decoration:underline;">safe@lemans.org</a><br><br>'
        'L\'Automobile Club de l\'Ouest attache une importance particuliere '
        'a la protection des donnees personnelles et a la securite des systemes d\'information.<br>'
        'Pour en savoir plus, consultez la notice de confidentialite accessible depuis le portail de connexion.<br><br>'
        '<span style="color:#999999;">&copy; Automobile Club de l\'Ouest &ndash; Association loi 1901. Tous droits reserves.</span>'
        '</td></tr></table>'
    )


# ----------------------------------------------------------------------------
# Composants HTML du rapport
# ----------------------------------------------------------------------------

def _wrap(content_html):
    return (
        '<!doctype html>'
        '<html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Rapport PC Organisation</title></head>'
        '<body style="margin:0;padding:0;background:#f4f5f7;'
        'font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;">'
        + _header_aco_html()
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f5f7;padding:24px 0;">'
        '<tr><td align="center">'
        '<table role="presentation" width="720" cellpadding="0" cellspacing="0" '
        'style="max-width:720px;width:100%;background:#ffffff;'
        'border-radius:8px;overflow:hidden;border:1px solid #e0e0e0;">'
        + content_html
        + '</table></td></tr></table>'
        + _footer_aco_html()
        + '</body></html>'
    )


def _report_header(summary):
    scope = _scope_label(summary)
    period = _fmt_period(summary.get("period_start"), summary.get("period_end"))
    author = _h((summary.get("created_by_name") or summary.get("created_by") or ""))
    created = _fmt_dt_human(summary.get("created_at"))
    fiches = _fmt_int(summary.get("fiches_count"))
    # Fallbacks pour Outlook (qui ne supporte pas linear-gradient) :
    # - bgcolor attribut HTML (lu en premier par Outlook)
    # - background-color avant le gradient
    return (
        '<tr><td bgcolor="#2563eb" '
        'style="background-color:#2563eb;'
        'background:linear-gradient(135deg,#2563eb,#7c3aed);'
        'padding:24px 28px;color:#ffffff;">'
        '<div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;'
        'color:#dbeafe;font-weight:600;">Cockpit &mdash; Rapport PC Organisation</div>'
        '<div style="font-size:22px;font-weight:700;margin-top:6px;color:#ffffff;">' + scope + '</div>'
        '<div style="font-size:14px;color:#dbeafe;margin-top:4px;">' + period + '</div>'
        '<div style="font-size:12px;color:#bfdbfe;margin-top:10px;">'
        'Genere le ' + created + ' par ' + author + ' &middot; ' + fiches + ' fiche(s) analysee(s)'
        '</div></td></tr>'
    )


def _section_card(title, body_text, accent="#2563eb"):
    if not body_text:
        body_text = "RAS"
    body_html = _render_md(body_text) if body_text != "RAS" else (
        '<p style="margin:0;color:#94a3b8;font-style:italic;">RAS</p>'
    )
    return (
        '<tr><td style="padding:0 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:8px 0;background:#ffffff;border:1px solid #e2e8f0;'
        'border-left:4px solid ' + accent + ';border-radius:8px;">'
        '<tr><td style="padding:14px 16px;">'
        '<div style="font-size:13px;font-weight:700;color:#1a1a1a;'
        'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;">'
        + _h(title) + '</div>'
        '<div style="font-size:14px;line-height:1.55;color:#333333;">' + body_html + '</div>'
        '</td></tr></table></td></tr>'
    )


def _kpi_block(kpis, comparisons=None):
    if not kpis:
        return ""
    total = _fmt_int(kpis.get("total"))
    open_ = _fmt_int(kpis.get("open"))
    closed = _fmt_int(kpis.get("closed"))
    avg_dur = kpis.get("avg_duration_min")
    avg_dur_str = (str(avg_dur) + " min") if avg_dur is not None else "-"

    tile = lambda label, val: (
        '<td align="center" valign="top" width="25%" style="padding:8px 6px;">'
        '<div style="background:#f4f5f7;border-radius:8px;padding:14px 8px;">'
        '<div style="font-size:22px;font-weight:800;color:#0b1024;">' + val + '</div>'
        '<div style="font-size:11px;color:#666666;text-transform:uppercase;'
        'letter-spacing:0.05em;font-weight:600;margin-top:4px;">' + label + '</div>'
        '</div></td>'
    )

    cat_rows = ""
    cats = kpis.get("by_category") or {}
    if cats:
        max_n = max(cats.values()) if cats else 1
        for k in sorted(cats, key=lambda x: -cats[x]):
            v = cats[k]
            pct = max(2, int(round(100 * v / max_n))) if max_n else 0
            cat_rows += (
                '<tr>'
                '<td style="font-size:12px;color:#475569;padding:3px 8px 3px 0;width:140px;">' + _h(k) + '</td>'
                '<td style="padding:3px 0;"><div style="background:#e2e8f0;border-radius:4px;'
                'height:8px;overflow:hidden;"><div style="background:#2563eb;height:8px;width:'
                + str(pct) + '%;"></div></div></td>'
                '<td style="font-size:12px;font-weight:700;color:#0b1024;text-align:right;'
                'padding:3px 0 3px 8px;width:40px;">' + _fmt_int(v) + '</td>'
                '</tr>'
            )

    urgency_html = ""
    urg = kpis.get("by_urgency") or {}
    urg_keys = [k for k in ["EU", "UA", "UR", "IMP"] if k in urg]
    if urg_keys:
        pills = []
        colors = {"EU": "#dc2626", "UA": "#f97316", "UR": "#eab308", "IMP": "#6b7280"}
        text_colors = {"UR": "#422006"}
        for k in urg_keys:
            bg = colors.get(k, "#6b7280")
            fg = text_colors.get(k, "#ffffff")
            pills.append(
                '<span style="display:inline-block;background:' + bg + ';color:' + fg + ';'
                'padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;'
                'margin:2px 4px 2px 0;">' + _h(k) + ' ' + _fmt_int(urg.get(k))
                + ' &mdash; ' + _h(URGENCY_LABELS.get(k, k)) + '</span>'
            )
        urgency_html = (
            '<tr><td colspan="4" style="padding:8px 6px 0 6px;">'
            '<div style="font-size:11px;font-weight:700;color:#666666;'
            'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Par urgence</div>'
            '<div>' + "".join(pills) + '</div></td></tr>'
        )

    cat_block = ""
    if cat_rows:
        cat_block = (
            '<tr><td colspan="4" style="padding:12px 6px 0 6px;">'
            '<div style="font-size:11px;font-weight:700;color:#666666;'
            'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Par categorie</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            + cat_rows + '</table></td></tr>'
        )

    cmp_block = _comparisons_block(kpis, comparisons)

    return (
        '<tr><td style="padding:8px 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;">'
        '<tr><td style="padding:14px 12px 4px 12px;">'
        '<div style="font-size:13px;font-weight:700;color:#1a1a1a;'
        'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
        'Indicateurs cles</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + tile("Total", total) + tile("Ouvertes", open_)
        + tile("Cloturees", closed) + tile("Duree moy.", avg_dur_str)
        + '</tr>' + cat_block + urgency_html + cmp_block
        + '</table></td></tr></table></td></tr>'
    )


def _comparisons_block(kpis, comparisons):
    if not comparisons:
        return ""
    total = int(kpis.get("total") or 0)

    def _chip(label, ref):
        if not ref:
            return None
        diff = total - int(ref)
        pct = round(100 * diff / int(ref)) if ref else 0
        if abs(pct) < 5:
            color, arrow = "#94a3b8", "&rarr;"
        elif pct > 0:
            color, arrow = "#dc2626", "&uarr;"
        else:
            color, arrow = "#16a34a", "&darr;"
        return (
            '<td valign="top" width="50%" style="padding:6px;">'
            '<div style="background:#f4f5f7;border-left:3px solid ' + color + ';'
            'border-radius:6px;padding:8px 10px;">'
            '<div style="font-size:11px;font-weight:700;color:#666666;'
            'text-transform:uppercase;letter-spacing:0.05em;">' + _h(label) + '</div>'
            '<div style="font-size:14px;font-weight:600;color:#1a1a1a;margin-top:2px;">'
            + _fmt_int(ref) + ' fiche(s)</div>'
            '<div style="font-size:12px;font-weight:700;color:' + color + ';margin-top:2px;">'
            + arrow + ' ' + (("+" + str(pct)) if pct >= 0 else str(pct)) + '%</div>'
            '</div></td>'
        )

    chips = []
    prev = (comparisons or {}).get("prev_period") or {}
    if (prev.get("kpis") or {}).get("total"):
        chips.append(_chip("Periode precedente", prev["kpis"]["total"]))
    py = (comparisons or {}).get("prev_year_aligned") or {}
    if (py.get("kpis") or {}).get("total"):
        label = "Edition precedente" + (" (" + str(py.get("year_prev")) + ")" if py.get("year_prev") else "")
        chips.append(_chip(label, py["kpis"]["total"]))
    chips = [c for c in chips if c]
    if not chips:
        return ""
    while len(chips) < 2:
        chips.append('<td width="50%"></td>')
    return (
        '<tr><td colspan="4" style="padding:12px 0 0 0;">'
        '<div style="font-size:11px;font-weight:700;color:#666666;'
        'text-transform:uppercase;letter-spacing:0.05em;margin:0 6px 4px 6px;">Comparaisons</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + "".join(chips) + '</tr></table></td></tr>'
    )


def _upcoming_block(summary):
    sections = summary.get("sections") or {}
    items = summary.get("upcoming") or []
    briefing = sections.get("prochaines_24h") or ""
    if not items and not briefing:
        return ""
    rows = ""
    for it in items[:30]:
        when = ""
        try:
            dt = datetime.fromisoformat(str(it.get("datetime")).replace("Z", "+00:00")) if it.get("datetime") else None
        except (ValueError, TypeError):
            dt = None
        if dt:
            when = dt.strftime("%d/%m %Hh%M")
        elif it.get("date") and it.get("time"):
            when = _h(it["date"]) + " " + _h(it["time"])
        label = _h(it.get("activity") or "")
        if it.get("place"):
            label += " &mdash; " + _h(it["place"])
        meta = []
        if it.get("event"):
            meta.append(_h(it["event"]) + (" " + _h(it.get("year")) if it.get("year") else ""))
        if it.get("category"):
            meta.append(_h(it["category"]))
        if it.get("department"):
            meta.append(_h(it["department"]))
        rows += (
            '<tr><td style="padding:5px 0;border-bottom:1px solid #eef2f6;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td valign="top" width="110" style="font-size:12px;font-weight:700;color:#4338ca;">'
            + when + '</td>'
            '<td valign="top" style="font-size:13px;color:#1a1a1a;">' + label
            + (('<div style="font-size:11px;color:#666666;margin-top:2px;">' + " &middot; ".join(meta) + '</div>') if meta else '')
            + '</td></tr></table></td></tr>'
        )
    briefing_html = ""
    if briefing:
        briefing_html = (
            '<div style="background:#ffffff;border-radius:6px;padding:10px 12px;'
            'font-size:13px;color:#333333;line-height:1.5;margin-bottom:10px;">'
            + _render_md(briefing) + '</div>'
        )
    return (
        '<tr><td style="padding:8px 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#eef2ff;border:1px solid #c7d2fe;border-left:4px solid #6366f1;'
        'border-radius:8px;">'
        '<tr><td style="padding:14px 16px;">'
        '<div style="font-size:13px;font-weight:700;color:#1e1b4b;'
        'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;">'
        'Prochaines 24 heures'
        + ('<span style="float:right;background:#e0e7ff;color:#4338ca;font-size:10px;'
           'padding:2px 8px;border-radius:999px;">' + _fmt_int(len(items)) + ' jalon(s)</span>' if items else '')
        + '</div>' + briefing_html
        + (('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            + rows + '</table>') if rows else '')
        + '</td></tr></table></td></tr>'
    )


def _attendance_block(summary):
    att = summary.get("attendance")
    if not att or not att.get("slots"):
        return ""

    def _slot_html(slot):
        is_today = slot.get("slot") == "today"
        bg = "#f0fdf4" if is_today else "#f4f5f7"
        border = "#16a34a" if is_today else "#e2e8f0"
        date_label = ""
        if slot.get("date"):
            try:
                d = datetime.fromisoformat(slot["date"]).date()
                date_label = d.strftime("%d/%m")
            except (ValueError, TypeError):
                date_label = _h(slot["date"])
        if not slot.get("is_public"):
            return (
                '<td valign="top" width="33%" style="padding:6px;">'
                '<div style="background:' + bg + ';border:1px solid ' + border + ';'
                'border-radius:8px;padding:12px;">'
                '<div style="font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;'
                'letter-spacing:0.05em;">' + _h(slot.get("label", "")) + '</div>'
                '<div style="font-size:12px;color:#94a3b8;margin-top:8px;font-style:italic;">'
                'Pas d\'ouverture publique</div></div></td>'
            )
        # Hier : pic constate uniquement (pas de fallback projection).
        # Aujourd'hui : pic projete en valeur principale + pic en cours en complement.
        # Demain : projection.
        slot_kind = slot.get("slot")
        if slot_kind == "yesterday":
            main_pic = slot.get("pic_observed")
            main_label = "Pic constate"
        elif slot_kind == "today":
            # En production temps reel comme en simulation, on veut systematiquement
            # afficher le pic projete (estimation finale) ; le pic en cours arrive
            # en complement quand il existe (live actif ou archive consultable).
            main_pic = slot.get("pic_projection")
            main_label = "Pic projete"
            if main_pic is None:
                main_pic = slot.get("pic_observed")
                main_label = "Pic en cours"
        else:
            main_pic = slot.get("pic_projection")
            main_label = "Pic projete"
        delta_html = ""
        if slot.get("pic_prev") is not None and main_pic is not None:
            v = slot.get("delta_pct_vs_prev")
            color, arrow = "#94a3b8", "&rarr;"
            if v is not None and abs(v) >= 5:
                color, arrow = ("#16a34a", "&uarr;") if v > 0 else ("#dc2626", "&darr;")
            delta_html = (
                '<div style="font-size:12px;font-weight:700;color:' + color + ';margin-top:2px;">'
                + arrow + ' ' + (("+" + str(v)) if v is not None and v >= 0 else str(v)) + '% '
                '<span style="color:#666666;font-weight:500;">(' + _fmt_int(slot.get("pic_prev")) + ')</span>'
                '</div>'
            )
        # Aujourd'hui : ligne complementaire "Pic en cours: X" si dispo
        extra_html = ""
        if slot_kind == "today" and slot.get("pic_observed") is not None and slot.get("pic_projection") is not None:
            extra_html = (
                '<div style="font-size:11px;color:#475569;margin-top:4px;'
                'background:#f1f5f9;border-radius:4px;padding:3px 6px;display:inline-block;">'
                'Pic en cours : <strong>' + _fmt_int(slot["pic_observed"]) + '</strong></div>'
            )
        billets = ""
        if slot.get("billets_vendus") is not None:
            billets = (
                '<div style="margin-top:8px;background:#ffffff;border:1px solid #e2e8f0;'
                'border-radius:6px;padding:6px 8px;font-size:12px;">'
                '<span style="font-weight:700;">' + _fmt_int(slot["billets_vendus"]) + '</span>'
                ' <span style="color:#666666;">billet(s) vendu(s)</span></div>'
            )
        if main_pic is None:
            value_html = (
                '<div style="font-size:12px;color:#94a3b8;margin-top:8px;font-style:italic;">'
                + ('Pic non disponible' if slot_kind == 'yesterday' else 'Pas de donnee') + '</div>'
            )
        else:
            value_html = (
                '<div style="font-size:11px;color:#666666;text-transform:uppercase;'
                'letter-spacing:0.04em;margin-top:8px;font-weight:600;">' + main_label + '</div>'
                '<div style="font-size:22px;font-weight:800;color:#0b1024;line-height:1.1;">'
                + _fmt_int(main_pic) + '</div>'
                + delta_html + extra_html
            )
        return (
            '<td valign="top" width="33%" style="padding:6px;">'
            '<div style="background:' + bg + ';border:1px solid ' + border + ';'
            'border-radius:8px;padding:12px;">'
            '<table role="presentation" width="100%"><tr>'
            '<td style="font-size:11px;font-weight:700;'
            + ('color:#15803d' if is_today else 'color:#475569') + ';'
            'text-transform:uppercase;letter-spacing:0.05em;">' + _h(slot.get("label", "")) + '</td>'
            '<td style="font-size:11px;color:#94a3b8;font-weight:600;text-align:right;">' + date_label + '</td>'
            '</tr></table>'
            + value_html + billets + '</div></td>'
        )

    cells = "".join(_slot_html(s) for s in att.get("slots", []))
    sub = ""
    if att.get("prev_year"):
        sub = (' <span style="font-size:10px;color:#475569;background:#f1f5f9;padding:2px 6px;'
               'border-radius:999px;font-weight:600;">Compare a ' + _h(att["prev_year"]) + '</span>')
    return (
        '<tr><td style="padding:8px 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid #16a34a;'
        'border-radius:8px;">'
        '<tr><td style="padding:14px 12px;">'
        '<div style="font-size:13px;font-weight:700;color:#14532d;'
        'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
        'Billetterie &amp; frequentation' + sub + '</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + cells + '</tr></table>'
        '</td></tr></table></td></tr>'
    )


_DOORS_CAT_LABELS = {
    "PCO.Flux": "Flux",
    "PCO.Securite": "Securite",
    "PCO.Information": "Info",
    "PCO.MainCourante": "Main courante",
}
_DOORS_CAT_COLORS = {
    "PCO.Flux": "#0d9488",
    "PCO.Securite": "#ef4444",
    "PCO.Information": "#2563eb",
    "PCO.MainCourante": "#8b5cf6",
}


def _doors_block(summary):
    dr = summary.get("door_reinforcement")
    if not dr or not dr.get("recommendations"):
        return ""

    # Limite : top 12 pour rester lisible
    all_recos = list(dr.get("recommendations") or [])
    fortes = [r for r in all_recos if r.get("criticite") == "forte"]
    moderees = [r for r in all_recos if r.get("criticite") != "forte"]
    # Tri par creneau croissant pour la lecture chronologique
    fortes.sort(key=lambda r: r.get("slot_n_start") or "")
    moderees.sort(key=lambda r: r.get("slot_n_start") or "")
    shown_fortes = fortes
    remaining_for_moderees = max(0, 12 - len(shown_fortes))
    shown_moderees = moderees[:remaining_for_moderees]
    nb_skipped = (len(fortes) - len(shown_fortes)) + (len(moderees) - len(shown_moderees))

    def _row(r):
        crit = r.get("criticite") or ""
        chips = ""
        for cat, n in (r.get("n1_fiches_by_category") or {}).items():
            color = _DOORS_CAT_COLORS.get(cat, "#64748b")
            label = _DOORS_CAT_LABELS.get(cat, cat)
            chips += (
                '<span style="display:inline-block;background:' + color + ';color:#fff;'
                'padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700;'
                'margin:1px 2px;">' + _h(label) + ' ' + str(n) + '</span>'
            )
        if not chips:
            chips = '<span style="color:#cbd5e1;">&mdash;</span>'
        pic_badge = ""
        if r.get("is_top3_pic"):
            pic_badge = ('<span style="margin-left:6px;display:inline-block;font-size:9px;'
                         'font-weight:700;background:#fef3c7;color:#b45309;'
                         'padding:1px 6px;border-radius:4px;text-transform:uppercase;'
                         'letter-spacing:0.04em;">top 3</span>')
        pic_value = _fmt_int(r.get("n1_scan_count")) if r.get("n1_scan_count") else "&mdash;"
        bg = "#fff7ed" if crit == "forte" else "#ffffff"
        return (
            '<tr style="background:' + bg + ';">'
            '<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;'
            'font-weight:700;color:#0b1024;font-size:14px;">'
            + _h(r.get("family_label", "?")) + '</td>'
            '<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;'
            'font-weight:600;color:#1d4ed8;white-space:nowrap;font-size:13px;">'
            + _h(r.get("slot_label_n", "")) + '</td>'
            '<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;'
            'white-space:nowrap;font-size:13px;color:#0b1024;">'
            + pic_value + pic_badge + '</td>'
            '<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">' + chips + '</td>'
            '</tr>'
        )

    def _section(title, color_bg, color_fg, recos, intro=""):
        if not recos:
            return ""
        rows = "".join(_row(r) for r in recos)
        intro_html = ""
        if intro:
            intro_html = (
                '<div style="font-size:12px;color:#475569;margin:0 0 6px 4px;font-style:italic;">'
                + _h(intro) + '</div>'
            )
        return (
            '<div style="margin-top:12px;">'
            '<div style="display:inline-block;background:' + color_bg + ';color:' + color_fg + ';'
            'font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;'
            'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
            + _h(title) + ' &middot; ' + str(len(recos)) + ' reco(s)</div>'
            + intro_html
            + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="background:#ffffff;border-radius:6px;font-size:13px;border:1px solid #fed7aa;">'
            '<thead><tr style="background:#fff7ed;color:#7c2d12;text-transform:uppercase;'
            'font-size:10px;letter-spacing:0.05em;">'
            '<th align="left" style="padding:8px 12px;border-bottom:1px solid #fed7aa;">Porte</th>'
            '<th align="left" style="padding:8px 12px;border-bottom:1px solid #fed7aa;">Creneau</th>'
            '<th align="left" style="padding:8px 12px;border-bottom:1px solid #fed7aa;">Pic N-1</th>'
            '<th align="left" style="padding:8px 12px;border-bottom:1px solid #fed7aa;">Incidents N-1</th>'
            '</tr></thead>'
            '<tbody>' + rows + '</tbody></table></div>'
        )

    skipped_html = ""
    if nb_skipped > 0:
        skipped_html = (
            '<div style="font-size:11px;color:#9a3412;font-style:italic;margin-top:10px;">'
            + str(nb_skipped) + ' reco(s) supplementaire(s) non affichee(s) (criticite '
            'moderee, voir donnees completes en base si besoin).</div>'
        )

    return (
        '<tr><td style="padding:8px 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#fff7ed;border:1px solid #fed7aa;border-left:4px solid #ea580c;'
        'border-radius:8px;">'
        '<tr><td style="padding:16px 18px;">'
        '<div style="font-size:14px;font-weight:700;color:#7c2d12;'
        'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">'
        'Renforts conseilles sur les portes (24h a venir)</div>'
        '<div style="font-size:12px;color:#9a3412;font-style:italic;margin-bottom:6px;">'
        'Croisement pic de trafic et incidents de l\'edition precedente'
        + (' (' + _h(str(dr.get("year_prev"))) + ')' if dr.get("year_prev") else '') + ', '
        'aligne sur le jour-equivalent course.</div>'
        + _section("Criticite forte", "#dc2626", "#ffffff", shown_fortes,
                   "Pic eleve l'an passe ET incidents avere(s) sur le meme creneau.")
        + _section("Criticite moderee", "#f59e0b", "#7c2d12", shown_moderees,
                   "Pic ou incidents l'an passe (un seul des deux).")
        + skipped_html
        + '</td></tr></table></td></tr>'
    )


def _retro_block(summary):
    retro = summary.get("n1_retro")
    if not retro or not retro.get("text"):
        return ""
    return (
        '<tr><td style="padding:8px 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#fef9c3;border:1px solid #facc15;border-left:4px solid #ca8a04;'
        'border-radius:8px;">'
        '<tr><td style="padding:14px 16px;">'
        '<div style="font-size:13px;font-weight:700;color:#713f12;'
        'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">'
        'Note retrospective &mdash; edition precedente</div>'
        '<div style="font-size:13px;line-height:1.55;color:#422006;white-space:pre-wrap;">'
        + _h(retro["text"]) + '</div>'
        '</td></tr></table></td></tr>'
    )


def _spacer(h_px=12):
    return '<tr><td style="height:' + str(h_px) + 'px;line-height:' + str(h_px) + 'px;font-size:0;">&nbsp;</td></tr>'


# ----------------------------------------------------------------------------
# Rendu HTML + plain text fallback
# ----------------------------------------------------------------------------

def render_summary_html(summary):
    body = (
        _report_header(summary)
        + _spacer(8)
        + _upcoming_block(summary)
        + _attendance_block(summary)
        + _doors_block(summary)
        + _kpi_block(summary.get("kpis") or {}, summary.get("comparisons"))
    )
    sections = summary.get("sections") or {}
    for key, label, color in SECTION_LABELS:
        body += _section_card(label, sections.get(key, ""), accent=color)
    # Note retrospective : conservee dans le doc Mongo (alimente Claude)
    # mais ne s'affiche plus en bloc standalone — elle est integree dans
    # les sections recommandations/flux directement par Claude.
    body += _spacer(12)
    return _wrap(body)


def render_summary_text(summary):
    parts = []
    parts.append("Rapport PC Organisation")
    parts.append("=" * 60)
    parts.append("Perimetre : " + _scope_label(summary).replace("&nbsp;", " "))
    parts.append("Periode : " + _fmt_period(summary.get("period_start"), summary.get("period_end")).replace("&rarr;", "->"))
    parts.append("Genere par : " + (summary.get("created_by_name") or summary.get("created_by") or ""))
    parts.append("Fiches analysees : " + str(summary.get("fiches_count") or 0))
    parts.append("")
    sections = summary.get("sections") or {}
    for key, label, _c in SECTION_LABELS:
        parts.append(label.upper())
        parts.append("-" * 60)
        parts.append((sections.get(key) or "RAS").strip())
        parts.append("")
    retro = summary.get("n1_retro")
    if retro and retro.get("text"):
        parts.append("NOTE RETROSPECTIVE EDITION PRECEDENTE")
        parts.append("-" * 60)
        parts.append(retro["text"].strip())
        parts.append("")
    parts.append("--")
    parts.append("Cockpit Assistant IA - TITAN ACO")
    return "\n".join(parts)


def email_subject(summary):
    scope = _scope_label(summary).replace("&nbsp;", " ")
    period = _fmt_period(summary.get("period_start"), summary.get("period_end")).replace("&rarr;", "->")
    return "[TITAN Cockpit] Rapport PC Org - " + scope + " - " + period


# ----------------------------------------------------------------------------
# Envoi SMTP - aligne sur home.py:_smtp_send_email
# ----------------------------------------------------------------------------

class SmtpError(Exception):
    pass


def send_summary_email(to_emails, summary, cc_emails=None, bcc_emails=None):
    """Envoie le rapport HTML aux destinataires. Aligne sur le pattern Home.

    Retourne dict {ok, sent_count, smtp_host, from}.
    Leve SmtpError pour SMTP non configure ou echec d'envoi.
    """
    host = (os.getenv("SMTP_HOST", "192.168.254.2") or "").strip()
    if not host:
        raise SmtpError("SMTP non configure")
    if not to_emails:
        raise SmtpError("Aucun destinataire")

    port = int(os.getenv("SMTP_PORT", "25"))
    user = (os.getenv("SMTP_USER", "") or "").strip()
    password = (os.getenv("SMTP_PASSWORD", "") or "").strip()
    sender = (os.getenv("SMTP_FROM", "safe@lemans.org") or "").strip()
    sender_name = (os.getenv("SMTP_FROM_NAME", "TITAN ACO") or "").strip()
    reply_to = (os.getenv("SMTP_REPLY_TO", "") or "").strip()
    use_tls = os.getenv("SMTP_USE_TLS", "false").strip().lower() in {"1", "true", "yes", "on"}
    timeout = int(os.getenv("SMTP_TIMEOUT", "10"))

    subject = email_subject(summary)
    text_body = render_summary_text(summary)
    html_body = render_summary_html(summary)

    sender_domain = sender.split("@")[-1] if "@" in sender else "lemans.org"

    message = MIMEMultipart("alternative")
    message["From"] = f"{sender_name} <{sender}>"
    message["To"] = ", ".join(to_emails)
    if cc_emails:
        message["Cc"] = ", ".join(cc_emails)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender_domain)
    if reply_to:
        message["Reply-To"] = reply_to
    message["X-Mailer"] = "TITAN Cockpit Assistant IA"
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    all_recipients = list(to_emails)
    if cc_emails:
        all_recipients += list(cc_emails)
    if bcc_emails:
        all_recipients += list(bcc_emails)

    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message, from_addr=sender, to_addrs=all_recipients)
    except (smtplib.SMTPException, OSError) as e:
        logger.warning("SMTP echec : %s", e)
        raise SmtpError("smtp_send_failed: " + str(e))

    return {
        "ok": True,
        "sent_count": len(all_recipients),
        "smtp_host": host,
        "from": sender,
    }
