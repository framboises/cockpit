"""Briefing de situation a chaque releve - lance par tache planifiee Windows.

La tache tourne toutes les 15 minutes (scripts/install_briefing_task.ps1) ;
c'est CE script qui decide s'il y a quelque chose a faire, d'apres
cockpit_settings._id="briefing_releve" (edite par un admin dans Cockpit) :

    {enabled: bool, times: ["06:45", "14:45", "22:45"], recipients: [ObjectId],
     last_slots: ["2026-09-29 06:45", ...]}

Un creneau HH:MM est traite si HH:MM <= maintenant < HH:MM + 15 min et s'il
n'a pas deja ete envoye (last_slots). Changer les heures dans l'UI prend donc
effet sans reinstaller la tache.

Pipeline : contexte (situation_briefing.build_context, evenement detecte
comme le rapport matinal) -> redaction Claude si la cle API est presente
(sinon mail avec les chiffres bruts seuls) -> mail aux destinataires.

Usage :
    python scripts/briefing_releve.py              # production
    python scripts/briefing_releve.py --dry-run    # genere, n'envoie rien, ne marque rien
    python scripts/briefing_releve.py --force --no-llm --dry-run
                                                   # test hors creneau, sans cout
    python scripts/briefing_releve.py --force --to=alice@example.com
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

import situation_briefing as sb  # noqa: E402

TZ_PARIS = ZoneInfo("Europe/Paris")
BOT_EMAIL = "briefing-releve@cockpit.lemans.org"


def _setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    return logging.getLogger("briefing_releve")


def _connect_mongo():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGO_DB", "titan")
    client = MongoClient(uri)
    return client[db_name], client


def main(argv=None):
    parser = argparse.ArgumentParser(description="Briefing de situation a chaque releve")
    parser.add_argument("--dry-run", action="store_true",
                        help="Genere sans envoyer ni marquer le creneau (ni persister)")
    parser.add_argument("--no-mail", action="store_true", help="Genere et enregistre, sans mail")
    parser.add_argument("--no-llm", action="store_true", help="Pas d'appel Claude (chiffres bruts)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore l'interrupteur global et la fenetre horaire (test admin)")
    parser.add_argument("--to", default="", help="Destinataires (csv) a la place des inscrits (test)")
    parser.add_argument("--window", type=int, default=15,
                        help="Fenetre de declenchement en minutes (= periode de la tache)")
    args = parser.parse_args(argv)

    log = _setup_logging()
    db, client = _connect_mongo()
    try:
        settings = sb.get_settings(db)
        if not settings["enabled"] and not args.force:
            log.info("Briefing de releve desactive (cockpit_settings.briefing_releve.enabled=false).")
            return 0

        now_paris = datetime.now(TZ_PARIS)
        slot = sb.due_slot(settings["times"], now_paris, args.window, settings["last_slots"])
        if slot is None and not args.force:
            log.info("Aucun creneau de releve du a %s (heures : %s).",
                     now_paris.strftime("%H:%M"), ", ".join(settings["times"]))
            return 0
        slot = slot or now_paris.strftime("%Y-%m-%d %H:%M") + " (force)"
        log.info("Creneau de releve : %s", slot)

        # Marque le creneau AVANT l'appel Claude : un second passage de la tache
        # pendant une generation lente ne doit pas envoyer un doublon.
        if not args.dry_run and not args.force:
            sb.mark_slot_sent(db, slot)

        ctx = sb.build_context(db)
        log.info("Contexte %s %s en %d ms, sources indisponibles : %s", ctx.get("event"),
                 ctx.get("year"), ctx.get("duree_ms"), ctx.get("sources_indisponibles") or "aucune")

        doc = {"context": ctx, "sections": None, "trigger": "releve"}
        if not args.no_llm and sb.api_key_present():
            try:
                doc = sb.generate_briefing(db, ctx, by=BOT_EMAIL, trigger="releve",
                                           persist=not args.dry_run)
                log.info("Briefing IA genere (%s), usage %s", doc.get("model"), doc.get("usage"))
            except Exception as exc:
                # Le mail part quand meme, avec les chiffres bruts : une releve
                # sans briefing redige vaut mieux qu'une releve sans rien.
                log.error("Redaction IA impossible (%s) : envoi des chiffres bruts seuls", exc)
        elif not args.no_llm:
            log.warning("ANTHROPIC_API_KEY absente : envoi des chiffres bruts seuls")

        if args.dry_run or args.no_mail:
            print(sb.render_briefing_text(doc))
            log.info("Mode dry-run / no-mail : aucun envoi.")
            return 0

        if args.to:
            emails = [e.strip() for e in args.to.split(",") if e.strip()]
        else:
            emails = sb.recipient_emails(db, settings)
        if not emails:
            log.warning("Aucun destinataire. Abandon de l'envoi.")
            return 0
        try:
            res = sb.send_briefing_email(emails, doc)
        except sb.SmtpError as exc:
            log.error("Echec SMTP : %s", exc)
            return 4
        log.info("Mail envoye a %d destinataire(s) via %s", res["sent_count"], res["smtp_host"])
        if doc.get("_id") is not None:
            try:
                db[sb.COLLECTION].update_one({"_id": doc["_id"]}, {"$push": {"email_sends": {
                    "ts": datetime.now(timezone.utc), "to": emails, "slot": slot, "automated": True}}})
            except Exception:
                log.warning("Trace email_sends echouee (non bloquant)")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
