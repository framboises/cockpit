"""
Blueprint Cockpit : rapport scans (parking_report.html) lisible depuis l'app.

Routes :
- /scan-report               -> page Cockpit avec sidebar + iframe
- /scan-report/static        -> sert directement le fichier HTML genere par
                                generate_parking_report.py (rendu dans l'iframe)

Pour l'instant uniquement event=24h_du_mans, year=2025 (un seul fichier
parking_report.html a la racine). A terme :
- support query params ?event=...&year=...
- chaque event/year aura son fichier dedie (cf. ROADMAP en fin de fichier)
"""

import os
from flask import Blueprint, request, render_template, send_file, abort, jsonify

scan_report_bp = Blueprint("scan_report", __name__)

REPORT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPORT_FILE = os.path.join(REPORT_DIR, 'parking_report.html')

# Whitelist event/year supportes (pour limiter l'acces a ce qui est genere)
SUPPORTED = {
    ('24h_du_mans', '2025'): DEFAULT_REPORT_FILE,
}


def _check_admin():
    """Meme logique que analyse_ops : reservation admin (avec bypass CODING)."""
    from app import (CODING, JWT_SECRET, JWT_ALGORITHM, ROLE_HIERARCHY,
                     ROLE_ORDER, APP_KEY)
    import jwt as pyjwt
    if CODING:
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["cockpit"], "roles_by_app": {"cockpit": sim_role},
            "global_roles": [], "roles": sim_roles, "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce", "lastname": "WAYNE",
            "email": "bruce@wayneenterprise.com",
        }
        return None
    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    roles = payload.get("roles_by_app", {}).get(APP_KEY, "")
    if isinstance(roles, str):
        roles = [roles]
    max_level = max((ROLE_HIERARCHY.get(r, 0) for r in roles), default=0)
    if max_level < ROLE_HIERARCHY.get("admin", 3):
        return jsonify({"error": "Admin required"}), 403
    effective_role = "admin" if max_level >= ROLE_HIERARCHY.get("admin", 3) else (roles[0] if roles else "user")
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY.get(r, 0) <= ROLE_HIERARCHY.get(effective_role, 0)]
    payload["app_role"] = effective_role
    request.user_payload = payload
    return None


@scan_report_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err


def _resolve_event_year():
    """Retourne (event, year, file_path) ou None si non supporte."""
    event = request.args.get('event', '24h_du_mans')
    year = request.args.get('year', '2025')
    return event, year, SUPPORTED.get((event, str(year)))


@scan_report_bp.route('/scan-report')
def scan_report_page():
    payload = getattr(request, 'user_payload', {})
    event, year, file_path = _resolve_event_year()
    return render_template(
        'scan_report.html',
        event=event, year=year,
        report_available=bool(file_path and os.path.isfile(file_path)),
        user_roles=payload.get('roles', []),
        user_firstname=payload.get('firstname', ''),
        user_lastname=payload.get('lastname', ''),
        user_email=payload.get('email', ''),
    )


@scan_report_bp.route('/scan-report/static')
def scan_report_static():
    event, year, file_path = _resolve_event_year()
    if not file_path or not os.path.isfile(file_path):
        abort(404, description=f'Rapport non disponible pour {event}/{year}')
    return send_file(file_path, mimetype='text/html')


# ---------------------------------------------------------------------------
# ROADMAP (multi-event/year)
# ---------------------------------------------------------------------------
# Pour supporter d'autres events :
# 1. Adapter generate_parking_report.py pour produire un fichier par
#    (event, year) ex : reports/parking_report_24h_motos_2025.html
# 2. Ajouter dans SUPPORTED chaque (event, year_str) -> chemin du fichier
# 3. Le selecteur cote front (sidebar event/year) propagera ?event=...&year=...
#    sur le lien "Rapport scans"
