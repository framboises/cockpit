"""Constat terrain envoye par mail avec resume IA -- PREPARE, A FINALISER.

Ce module construit deja le constat (`build_constat`) et son rendu mail
(`render_email_html`, meme habillage ACO que pcorg_summary_mail). L'apercu
est servi par GET /api/declarations/<id>/report/preview (bouton "Apercu du
constat" de la page /declarations).

Reste a faire (decisions a prendre avec l'exploitation) :
  1. Destinataires : par groupe declarant ? par categorie de la fiche ?
     liste fixe (prestataire, services techniques, assureur) ? -> config
     `cockpit_settings._id = "declarations_report"` (CONFIG_DEFAULTS).
  2. Declenchement : manuel (bouton) ou automatique a la cloture de la fiche
     liee ; un ou plusieurs envois (constat initial puis constat de reprise).
  3. Resume IA : `generate_summary` (prompt SUMMARY_PROMPT ci-dessous) a
     brancher sur le client Claude du projet (cle COCKPIT_ANTHROPIC_API_KEY,
     suivi des couts comme ai_reports.py) ; le resume ne doit JAMAIS inventer
     un fait absent du constat.
  4. Photos : /field/photos/* exige une session -> en piece jointe ou en
     image inline (CID) dans le mail, pas en lien.
  5. Envoi : `pcorg_summary_mail.send_summary_email` (SMTP_*) sait deja
     envoyer ; trace dans `declarations.report` (state, sent_at, to, by).
"""
import html
from datetime import timezone
from zoneinfo import ZoneInfo

import pcorg_summary_mail as PSM

PARIS = ZoneInfo("Europe/Paris")

CONFIG_ID = "declarations_report"
CONFIG_DEFAULTS = {
    "enabled": False,          # tant que False : apercu seulement, aucun envoi
    "recipients": [],          # adresses fixes
    "recipients_by_group": {}, # {beacon_group_id: [adresses]}
    "auto_on_close": False,    # envoi automatique a la cloture de la fiche liee
    "ai_summary": True,
}

STATUS_LABELS = {
    "nouvelle": "Nouveau, non traite",
    "en_suivi": "En suivi au PC Organisation",
    "transformee": "Transforme en fiche d'intervention",
    "classee": "Classe sans suite",
}

SUMMARY_PROMPT = (
    "Tu rediges le resume d'un constat terrain (degat, probleme, demande de "
    "reparation) pour un mail adresse aux services concernes de l'Automobile "
    "Club de l'Ouest. A partir UNIQUEMENT des elements fournis (description du "
    "declarant, complements, notes du PC, suite donnee), ecris 3 a 5 phrases "
    "factuelles : ce qui a ete constate, ou, quand, la gravite, ce qui a ete "
    "fait ou reste a faire. N'invente rien ; si une information manque, ne la "
    "mentionne pas. Pas de formule de politesse."
)


def get_config(db):
    doc = db["cockpit_settings"].find_one({"_id": CONFIG_ID}) or {}
    return dict(CONFIG_DEFAULTS, **{k: v for k, v in doc.items() if k in CONFIG_DEFAULTS})


def _fmt(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS).strftime("%d/%m/%Y %H:%M")


def build_constat(decl, fiche=None):
    """Donnees du constat, independantes du support (mail, PDF, prompt IA)."""
    gps = decl.get("gps") or {}
    out = {
        "ref": decl.get("ref"),
        "event": decl.get("event"),
        "year": decl.get("year"),
        "created_at": _fmt(decl.get("created_at")),
        "declarant": decl.get("device_name"),
        "group": decl.get("group_label"),
        "priority": decl.get("priority"),
        "text": decl.get("text") or "",
        "carroye": decl.get("carroye") or "",
        "lat": gps.get("lat"),
        "lng": gps.get("lng"),
        "status": decl.get("status"),
        "status_label": STATUS_LABELS.get(decl.get("status"), decl.get("status")),
        "classed_reason": decl.get("classed_reason"),
        "photos": [p.get("photo") for p in (decl.get("photos") or []) if p.get("photo")],
        "history": [{
            "ts": _fmt(h.get("ts")), "by": str(h.get("by") or "").replace("field:", "Tablette "),
            "origin": h.get("origin"),
            "kind": h.get("kind"), "text": h.get("text") or "",
        } for h in (decl.get("history") or [])],
        "fiche": None,
    }
    if fiche:
        out["fiche"] = {
            "id": fiche.get("_id"),
            "category": (fiche.get("category") or "").replace("PCO.", ""),
            "niveau_urgence": fiche.get("niveau_urgence"),
            "closed": fiche.get("status_code") == 10,
            "close_ts": _fmt(fiche.get("close_ts")),
            "operator_close": fiche.get("operator_close"),
            "unit": (fiche.get("content_category") or {}).get("patrouille"),
        }
    return out


def generate_summary(constat):
    """A brancher : appel Claude avec SUMMARY_PROMPT et le constat en JSON.
    Retourne None tant que ce n'est pas finalise (le mail part sans resume)."""
    return None


def _row(label, value):
    if value in (None, ""):
        return ""
    return ('<tr><td style="padding:6px 0;color:#64748b;font-size:13px;width:170px;vertical-align:top;">'
            + html.escape(label) + '</td><td style="padding:6px 0;font-size:14px;color:#0f172a;">'
            + html.escape(str(value)) + '</td></tr>')


def render_email_html(constat, summary=None):
    e = html.escape
    pr = {"basse": "Peut attendre", "normale": "A traiter rapidement",
          "haute": "Urgent, danger ou blocage"}.get(constat.get("priority"), "")
    pos = ""
    if constat.get("lat") is not None:
        pos = "%.5f, %.5f" % (constat["lat"], constat["lng"])
    rows = "".join([
        _row("Reference", constat.get("ref")),
        _row("Evenement", "%s %s" % (constat.get("event") or "", constat.get("year") or "")),
        _row("Date", constat.get("created_at")),
        _row("Declarant", "%s (%s)" % (constat.get("declarant") or "?", constat.get("group") or "")),
        _row("Priorite", pr),
        _row("Carroyage", constat.get("carroye")),
        _row("Position GPS", pos),
        _row("Etat", constat.get("status_label")),
        _row("Motif de classement", constat.get("classed_reason")),
    ])
    f = constat.get("fiche")
    if f:
        rows += _row("Fiche d'intervention", "%s%s - %s" % (
            f.get("category") or "", (" " + f["niveau_urgence"]) if f.get("niveau_urgence") else "",
            ("close le " + f["close_ts"] + (" par " + f["operator_close"] if f.get("operator_close") else ""))
            if f.get("closed") else "en cours"))
    summary_html = ""
    if summary:
        summary_html = ('<tr><td style="padding:16px 24px 0;"><div style="background:#eff6ff;border-left:4px solid '
                        '#2563eb;padding:12px 14px;font-size:14px;line-height:1.5;color:#0f172a;">'
                        '<strong>Resume</strong><br>' + e(summary) + '</div></td></tr>')
    else:
        summary_html = ('<tr><td style="padding:16px 24px 0;"><div style="background:#f8fafc;border:1px dashed '
                        '#94a3b8;padding:10px 14px;font-size:12px;color:#64748b;">Resume IA : a venir '
                        '(generation non encore activee).</div></td></tr>')
    photos = ""
    if constat.get("photos"):
        imgs = "".join('<img src="%s" alt="Photo du constat" style="width:200px;height:150px;object-fit:cover;'
                       'border-radius:6px;margin:0 8px 8px 0;border:1px solid #e2e8f0;">' % e(p)
                       for p in constat["photos"])
        photos = ('<tr><td style="padding:16px 24px 0;"><div style="font-size:13px;color:#64748b;margin-bottom:8px;">'
                  'Photos (%d)</div>%s</td></tr>' % (len(constat["photos"]), imgs))
    hist = "".join(
        '<li style="margin:0 0 6px;"><span style="color:#64748b;">%s - %s :</span> %s</li>'
        % (e(h["ts"]), e(h.get("by") or ""), e(h["text"]))
        for h in constat.get("history") or [] if h.get("text"))
    content = (
        '<tr><td style="padding:22px 24px 4px;"><div style="font-size:12px;letter-spacing:1px;color:#d97706;'
        'font-weight:bold;text-transform:uppercase;">Constat terrain</div>'
        '<div style="font-size:20px;font-weight:bold;color:#0f172a;margin-top:4px;">' + e(constat.get("ref") or "")
        + '<span style="font-size:14px;font-weight:normal;color:#64748b;"> &middot; '
        + e("%s %s" % (constat.get("event") or "", constat.get("year") or "")) + '</span>'
        + '</div></td></tr>'
        + summary_html
        + '<tr><td style="padding:16px 24px 0;"><div style="font-size:15px;line-height:1.5;color:#0f172a;'
        'white-space:pre-wrap;">' + e(constat.get("text") or "") + '</div></td></tr>'
        + '<tr><td style="padding:12px 24px 0;"><table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0">' + rows + '</table></td></tr>'
        + photos
        + ('<tr><td style="padding:16px 24px 22px;"><div style="font-size:13px;color:#64748b;margin-bottom:6px;">'
           'Chronologie</div><ul style="margin:0;padding-left:18px;font-size:13px;color:#0f172a;">'
           + hist + '</ul></td></tr>' if hist else '<tr><td style="padding:0 0 22px;"></td></tr>')
    )
    return PSM._wrap(content).replace("<title>Rapport PC Organisation</title>",
                                      "<title>Constat " + e(constat.get("ref") or "") + "</title>")


def send_constat(db, decl, fiche=None, to=None, by=None):
    """A finaliser (cf. en-tete) : resume IA, photos en pieces jointes,
    envoi SMTP, trace dans declarations.report."""
    raise NotImplementedError("envoi des constats a finaliser")
