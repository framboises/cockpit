"""Rapport matinal PC Organisation - lance par tache planifiee Windows.

Pipeline :
1. Calcule la fenetre 07h J-1 -> 07h J en Europe/Paris (24h glissantes).
2. Devine automatiquement l'evenement actif via la logique du bloc live-status :
   parametrages dont montage.start <= now <= demontage.end. Si plusieurs
   candidats : choisit celui dont la course est la plus proche. Si aucun :
   fallback sur l'evenement SAISON (annee courante).
3. Appelle pcorg_summary.generate_period_summary avec une consigne
   complementaire qui demande a Claude de mettre la nuit (00h-07h) en avant.
4. Lit la liste des destinataires inscrits dans cockpit_settings.morning_report.
5. Envoie le rapport par mail (via pcorg_summary_mail.send_summary_email).

Usage :
    python pcorg_morning_report.py            # production (cas standard)
    python pcorg_morning_report.py --dry-run  # genere et affiche, n'envoie pas
    python pcorg_morning_report.py --to=alice@example.com
                                              # test : forcer la liste de
                                              # destinataires (un seul mail
                                              # pour tester le rendu)

Pas d'option pour forcer l'event : la detection automatique est la seule
source de verite (alignee sur le bloc live-status de cockpit).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pymongo import MongoClient

import pcorg_summary
import pcorg_summary_mail


TZ_PARIS = ZoneInfo("Europe/Paris")

EXTRA_FOCUS_NOTE_NIGHT = (
    "Ce rapport est le brief matinal du PC Organisation, genere "
    "automatiquement a 07h00 sur les 24 dernieres heures. Le ton doit "
    "rester celui d'un debrief operationnel destine aux managers qui "
    "prennent leur service.\n"
    "\n"
    "Priorites de ce brief matinal :\n"
    "1. NUIT (00h00 - 07h00) : mets l'accent particulier sur ce creneau. "
    "Incidents nocturnes, situations qui ont sollicite l'astreinte, "
    "rondes notables, anomalies decouvertes au petit matin. Si la nuit "
    "n'a rien de notable, dis-le clairement plutot que de meubler.\n"
    "2. SYNTHESE : dans la synthese, situe systematiquement le volume "
    "d'activite par rapport a la veille meme creneau et a l'edition "
    "precedente (KPIs comparatifs fournis). Evoque la frequentation "
    "attendue de la journee qui arrive si elle est fournie (pic projete, "
    "billets vendus, comparaison annee precedente).\n"
    "3. RECOMMANDATIONS : croise systematiquement les constats de cette "
    "nuit avec la note retrospective de l'edition precedente quand elle "
    "est fournie. Signale les recurrences, propose des actions "
    "preventives concretes. Mentionne les jalons des prochaines 24h qui "
    "peuvent etre impactes par les constats de la nuit (ouvertures "
    "publiques, briefings, depart de course).\n"
)


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("morning_report")


def _connect_mongo():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGO_DB", "titan")
    client = MongoClient(uri)
    return client[db_name], client


def _last_24h_window():
    """Fenetre 07h J-1 -> 07h J en Europe/Paris, retournee en UTC aware.

    Si on tourne avant 07h, la fenetre couvre les 24h precedant maintenant
    (utile pour les declenchements de test). Sinon : alignee sur 07h pile.
    """
    now_paris = datetime.now(TZ_PARIS)
    end_paris = now_paris.replace(hour=7, minute=0, second=0, microsecond=0)
    if now_paris < end_paris:
        # Avant 07h -> on prend 07h hier matin -> 07h ce matin
        end_paris = end_paris - timedelta(days=1)
    start_paris = end_paris - timedelta(days=1)
    return start_paris.astimezone(timezone.utc), end_paris.astimezone(timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rapport matinal PC Organisation")
    parser.add_argument("--dry-run", action="store_true", help="Genere sans envoyer le mail")
    parser.add_argument("--to", default="", help="Destinataires (csv) qui remplacent la liste opt-in (test)")
    parser.add_argument("--no-mail", action="store_true", help="Genere et sauve mais n'envoie aucun mail")
    parser.add_argument("--as-of", default="", dest="as_of",
                        help="ISO datetime pour simuler le 'now' (test, ex: 2025-06-14T07:00)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore l'interrupteur global (utile pour test admin)")
    args = parser.parse_args(argv)

    log = _setup_logging()
    db, client = _connect_mongo()
    try:
        # Interrupteur global : si desactive, on quitte sans rien faire.
        # --force permet aux admins de bypasser pour tester en CLI.
        prefs = pcorg_summary.get_morning_report_prefs(db)
        if not prefs.get("enabled") and not args.force:
            log.info("Rapport matinal desactive globalement (cockpit_settings."
                     "morning_report.enabled=false). Sortie.")
            log.info("Pour activer : page Cockpit /edit -> 'Rapport matinal "
                     "automatique'. Pour bypasser ce check en CLI : --force.")
            return 0

        # Mode simulation : si --as-of fourni, on traite ce datetime comme le
        # "now" courant (en Europe/Paris si naif). Sert a tester en local hors
        # periode d'evenement.
        as_of_utc = None
        if args.as_of:
            try:
                s = args.as_of.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ_PARIS)
                as_of_utc = dt.astimezone(timezone.utc)
                log.info("Mode simulation : as_of = %s (UTC)", as_of_utc.isoformat())
            except (ValueError, TypeError) as e:
                log.error("--as-of invalide : %s", e)
                return 1

        if as_of_utc is not None:
            ts_end = as_of_utc.astimezone(TZ_PARIS).replace(hour=7, minute=0, second=0, microsecond=0)
            if as_of_utc.astimezone(TZ_PARIS) < ts_end:
                ts_end -= timedelta(days=1)
            ts_start_paris = ts_end - timedelta(days=1)
            ts_start = ts_start_paris.astimezone(timezone.utc)
            ts_end = ts_end.astimezone(timezone.utc)
        else:
            ts_start, ts_end = _last_24h_window()
        log.info("Fenetre analysee : %s -> %s (UTC)", ts_start.isoformat(), ts_end.isoformat())

        # 1. Detection automatique de l'event actif (alignee sur live-status).
        event, year = pcorg_summary.detect_active_event(db, now_utc=as_of_utc)
        log.info("Evenement detecte : %s %s", event, year)

        # 2. Generation du resume avec focus nuit (mode streaming pour eviter
        # le timeout sur les reponses longues + voir la progression CLI).
        log.info("Generation du resume Claude (streaming)...")

        last_log_chars = [0]
        def _on_progress(text_so_far, output_tokens):
            # Log toutes les ~800 chars pour avoir une progression visible
            if len(text_so_far) - last_log_chars[0] >= 800:
                last_log_chars[0] = len(text_so_far)
                log.info("  ... %d chars recus (%d output tokens)",
                         len(text_so_far), output_tokens)

        try:
            doc = pcorg_summary.generate_period_summary(
                db,
                event=event,
                year=year,
                ts_start=ts_start,
                ts_end=ts_end,
                created_by_email="morning-report@cockpit.lemans.org",
                created_by_name="Rapport matinal automatique",
                extra_focus_note=EXTRA_FOCUS_NOTE_NIGHT,
                as_of_utc=as_of_utc,
                on_progress=_on_progress,
            )
        except pcorg_summary.ClaudeError as e:
            log.error("Echec appel Claude : %s", e)
            return 2
        except Exception:
            log.exception("Erreur inattendue lors de la generation du resume")
            return 3

        log.info("Resume genere : id=%s fiches=%s tokens_in=%s tokens_out=%s",
                 doc.get("_id"),
                 doc.get("fiches_count"),
                 (doc.get("usage") or {}).get("input_tokens"),
                 (doc.get("usage") or {}).get("output_tokens"))

        if args.dry_run or args.no_mail:
            log.info("Mode dry-run / no-mail : aucun envoi.")
            return 0

        # 3. Resolution destinataires
        if args.to:
            emails = [e.strip() for e in args.to.split(",") if e.strip()]
            log.info("Destinataires forces (--to) : %d", len(emails))
        else:
            emails = pcorg_summary.get_morning_report_emails(db)
            log.info("Destinataires inscrits au rapport matinal : %d", len(emails))

        if not emails:
            log.warning("Aucun destinataire. Abandon de l'envoi.")
            return 0

        # 4. Envoi mail (le serializer n'inclut pas les champs riches sur le doc
        # retourne par save_summary, on relit en mode 'full').
        summary = pcorg_summary._serialize_summary(doc, light=False)
        try:
            result = pcorg_summary_mail.send_summary_email(emails, summary)
        except pcorg_summary_mail.SmtpError as e:
            log.error("Echec SMTP : %s", e)
            return 4
        except Exception:
            log.exception("Erreur inattendue lors de l'envoi mail")
            return 5

        log.info("Mail envoye a %d destinataire(s) via %s",
                 result.get("sent_count"), result.get("smtp_host"))

        # 5. Trace dans le doc summary (audit identique a la route /send)
        try:
            db["pcorg_summaries"].update_one(
                {"_id": doc["_id"]},
                {"$push": {"email_sends": {
                    "ts": datetime.now(timezone.utc),
                    "by_email": "morning-report@cockpit.lemans.org",
                    "by_name": "Rapport matinal automatique",
                    "to": emails,
                    "user_ids": [],
                    "group_ids": [],
                    "ok": True,
                    "smtp_host": result.get("smtp_host"),
                    "automated": True,
                }}},
            )
        except Exception:
            log.warning("Trace email_sends echouee (non bloquant)")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
