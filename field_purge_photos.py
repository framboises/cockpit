#!/usr/bin/env python3
# Cron leger : supprime les fichiers photos terrain plus vieux que 30 jours.
# Appelable directement (sans serveur Flask). Loggue le resultat sur stdout.

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("field_purge_photos")

from field import purge_old_photo_files, FIELD_PHOTO_FILE_TTL_DAYS

if __name__ == "__main__":
    stats = purge_old_photo_files()
    mb = stats["bytes_freed"] / (1024 * 1024)
    logger.info(
        "purge done: scanned=%d deleted=%d freed=%.2f MB (ttl=%d j)",
        stats["scanned"], stats["deleted"], mb, FIELD_PHOTO_FILE_TTL_DAYS,
    )
